import os
import re
import json
import uuid
import logging
from datetime import datetime
from pathlib import Path
import config

logger = logging.getLogger("state")
STATE_FILE = config.DATA_DIR / "state.json"

class StateManager:
    def __init__(self):
        self.mode = "terminal"
        self.current_project = "default"
        self.auto_push = True
        self.active_conversation_id = ""
        self.conversations = {}
        self.prompt_buffer = []  # list of items waiting for Proceed
        self.buffer_message_id: int | None = None
        self.queue = []
        self.history = []
        self.chat_history = {}
        self.last_terminal_output = ""
        self.was_restarted = False
        self.last_chat_id: int | None = 8327850869
        self.api_keys: list[str] = []
        self.active_key_index: int = 0
        
        self.load()

    def set_active_conversation_id(self, conv_id: str):
        self.active_conversation_id = conv_id
        if conv_id and conv_id not in self.conversations:
            timestamp = datetime.utcnow().isoformat()
            self.conversations[conv_id] = {
                "id": conv_id,
                "title": f"Session {len(self.conversations) + 1}",
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        self.save()

    def add_to_buffer(self, text: str, item_type: str = "text", file_name: str | None = None, size_kb: float | None = None):
        self.prompt_buffer.append({
            "text": text,
            "type": item_type,
            "file_name": file_name,
            "size_kb": size_kb
        })

    def clear_buffer(self):
        self.prompt_buffer.clear()
        self.buffer_message_id = None

    def add_chat_message(self, conv_id: str, role: str, text: str):
        if conv_id not in self.chat_history:
            self.chat_history[conv_id] = []
        self.chat_history[conv_id].append({
            "role": role,
            "text": text,
            "timestamp": datetime.utcnow().isoformat()
        })
        if len(self.chat_history[conv_id]) > 40:
            self.chat_history[conv_id] = self.chat_history[conv_id][-40:]
        self.save()

    def get_chat_messages(self, conv_id: str) -> list[dict]:
        return self.chat_history.get(conv_id, [])

    def new_conversation(self, title: str | None = None) -> str:
        self.active_conversation_id = ""
        self.clear_buffer()
        self.save()
        return "new"

    def switch_conversation(self, conv_id: str) -> bool:
        if conv_id in self.conversations:
            self.active_conversation_id = conv_id
            self.save()
            return True
        return False

    def get_active_api_key(self) -> str:
        if not self.api_keys:
            return config.GEMINI_API_KEY or ""
        if self.active_key_index >= len(self.api_keys):
            self.active_key_index = 0
        return self.api_keys[self.active_key_index]

    def rotate_api_key(self) -> str | None:
        """Rotates to next available API key upon quota exhaustion."""
        if not self.api_keys or len(self.api_keys) <= 1:
            return None
        self.active_key_index = (self.active_key_index + 1) % len(self.api_keys)
        next_key = self.api_keys[self.active_key_index]
        config.GEMINI_API_KEY = next_key
        os.environ["GEMINI_API_KEY"] = next_key
        config.configure_agy_settings(use_gemini_api=True)
        self.save()
        logger.info(f"Auto-rotated to API key index {self.active_key_index + 1}/{len(self.api_keys)}")
        return next_key

    def sync_keys_to_render(self):
        """Asynchronously syncs current key pool to Render environment so it persists across container rebuilds."""
        import os, threading
        def _sync():
            try:
                render_api_key = os.getenv("RENDER_API_KEY", "")
                render_service_id = os.getenv("RENDER_SERVICE_ID", "")
                if not render_api_key or not render_service_id:
                    rc_path = config.BASE_DIR / "render_config.json"
                    if rc_path.exists():
                        with open(rc_path) as f:
                            rc = json.load(f)
                            render_api_key = render_api_key or rc.get("api_key")
                            render_service_id = render_service_id or rc.get("service_id")
                if render_api_key and render_service_id:
                    import urllib.request
                    url = f"https://api.render.com/v1/services/{render_service_id}/env-vars"
                    
                    # Read existing env vars to preserve BOT_TOKEN, GITHUB_TOKEN, etc.
                    existing_vars = []
                    current_remote_keys = ""
                    try:
                        get_req = urllib.request.Request(url, headers={"Authorization": f"Bearer {render_api_key}"})
                        with urllib.request.urlopen(get_req, timeout=10) as get_resp:
                            raw = json.loads(get_resp.read().decode())
                            for item in raw:
                                k = item.get("envVar", {}).get("key")
                                v = item.get("envVar", {}).get("value")
                                if k == "GEMINI_API_KEYS":
                                    current_remote_keys = v or ""
                                if k and k not in ["GEMINI_API_KEYS", "GEMINI_API_KEY"]:
                                    existing_vars.append({"key": k, "value": v})
                    except Exception:
                        pass

                    val = ",".join(self.api_keys) if self.api_keys else ""
                    # If remote keys already match, skip PUT to prevent triggering a redundant container redeploy
                    if val == current_remote_keys:
                        logger.debug("Render env keys already identical; skipping redeploy trigger.")
                        return

                    if self.api_keys:
                        active = self.get_active_api_key()
                        payload_list = existing_vars + [
                            {"key": "GEMINI_API_KEYS", "value": val},
                            {"key": "GEMINI_API_KEY", "value": active}
                        ]
                    else:
                        payload_list = existing_vars
                    payload = json.dumps(payload_list).encode("utf-8")
                    req = urllib.request.Request(
                        url,
                        data=payload,
                        headers={"Authorization": f"Bearer {render_api_key}", "Content-Type": "application/json"},
                        method="PUT"
                    )
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        logger.info(f"Successfully synced API keys to Render env vars without overwriting others!")
            except Exception as e:
                logger.warning(f"Failed to sync API keys to Render: {e}")

        threading.Thread(target=_sync, daemon=True).start()

    def set_api_keys(self, keys: list[str], sync_render: bool = True):
        # Keep unique in order
        clean_keys = []
        for k in keys:
            k_clean = k.strip()
            if k_clean and k_clean not in clean_keys:
                clean_keys.append(k_clean)

        if clean_keys:
            self.api_keys = clean_keys
            self.active_key_index = 0
            config.GEMINI_API_KEY = clean_keys[0]
            os.environ["GEMINI_API_KEY"] = clean_keys[0]
            config.configure_agy_settings(use_gemini_api=True)
            self.save()
            if sync_render:
                self.sync_keys_to_render()

    def add_api_key(self, key: str, sync_render: bool = True):
        clean = key.strip()
        if clean and clean not in self.api_keys:
            self.api_keys.append(clean)
            if not config.GEMINI_API_KEY:
                config.GEMINI_API_KEY = clean
                os.environ["GEMINI_API_KEY"] = clean
            config.configure_agy_settings(use_gemini_api=True)
            self.save()
            if sync_render:
                self.sync_keys_to_render()

    def clear_api_keys(self, sync_render: bool = True):
        self.api_keys = []
        self.active_key_index = 0
        config.GEMINI_API_KEY = ""
        os.environ.pop("GEMINI_API_KEY", None)
        os.environ.pop("GEMINI_API_KEYS", None)
        config.configure_agy_settings(use_gemini_api=False)
        self.save()
        if sync_render:
            self.sync_keys_to_render()

    def list_conversations(self) -> list[dict]:
        return list(self.conversations.values())

    def get_project_dir(self) -> Path:
        pdir = config.WORKSPACE_DIR / self.current_project
        pdir.mkdir(parents=True, exist_ok=True)
        return pdir

    def get_current_cwd(self) -> Path:
        if not hasattr(self, "_current_cwd") or self._current_cwd is None:
            self._current_cwd = self.get_project_dir()
        if not self._current_cwd.exists():
            self._current_cwd = self.get_project_dir()
        return self._current_cwd

    def set_current_cwd(self, p: Path):
        self._current_cwd = p

    def change_directory(self, target_str: str) -> tuple[bool, str]:
        target_str = target_str.strip()
        curr = self.get_current_cwd()
        ws_root = config.WORKSPACE_DIR.resolve()

        if not target_str or target_str == "~":
            new_path = self.get_project_dir()
        elif target_str == "..":
            if curr.resolve() != ws_root and ws_root in curr.resolve().parents:
                new_path = curr.parent
            else:
                new_path = curr
        else:
            p = Path(target_str)
            if p.is_absolute():
                new_path = p.resolve()
            else:
                new_path = (curr / p).resolve()

        # Enforce containment: Cannot cd outside workspace root
        if new_path != ws_root and ws_root not in new_path.parents:
            return False, "Access restricted: Directory cannot escape workspace root."

        if new_path.exists() and new_path.is_dir():
            self.set_current_cwd(new_path)
            try:
                rel = new_path.relative_to(config.WORKSPACE_DIR)
                disp = "~" if str(rel) == "." else f"~/{rel}"
                return True, disp
            except ValueError:
                return True, str(new_path)
        return False, f"Directory not found: {target_str}"

    def add_history_entry(self, entry: dict):
        entry["timestamp"] = datetime.utcnow().isoformat()
        self.history.append(entry)
        if len(self.history) > 50:
            self.history = self.history[-50:]
        self.save()

    def save(self):
        data = {
            "mode": self.mode,
            "current_project": self.current_project,
            "auto_push": self.auto_push,
            "active_conversation_id": self.active_conversation_id,
            "conversations": self.conversations,
            "queue": self.queue,
            "history": self.history[-20:],
            "chat_history": self.chat_history,
            "allowed_user_id": config.ALLOWED_USER_ID,
            "last_chat_id": self.last_chat_id,
            "api_keys": self.api_keys,
            "active_key_index": self.active_key_index,
            "last_save": datetime.utcnow().isoformat(),
        }
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"State save failed: {e}")

    def load(self):
        if not STATE_FILE.exists():
            initial_keys = config.get_initial_api_keys()
            if initial_keys:
                self.set_api_keys(initial_keys, sync_render=False)
            return
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.mode = data.get("mode", self.mode)
            self.current_project = data.get("current_project", self.current_project)
            self.auto_push = data.get("auto_push", self.auto_push)
            self.conversations = data.get("conversations", {})
            self.active_conversation_id = data.get("active_conversation_id", self.active_conversation_id)
            self.queue = data.get("queue", [])
            self.history = data.get("history", [])
            self.chat_history = data.get("chat_history", {})
            
            if data.get("allowed_user_id") and config.ALLOWED_USER_ID is None:
                config.set_allowed_user_id(data["allowed_user_id"])
            if data.get("last_chat_id"):
                self.last_chat_id = data["last_chat_id"]
            if data.get("api_keys"):
                self.api_keys = data["api_keys"]
                self.active_key_index = data.get("active_key_index", 0)
                active_k = self.get_active_api_key()
                if active_k:
                    config.GEMINI_API_KEY = active_k
                    os.environ["GEMINI_API_KEY"] = active_k
            else:
                initial_keys = config.get_initial_api_keys()
                if initial_keys:
                    self.set_api_keys(initial_keys, sync_render=False)
            
            # If state file was loaded, mark restart recovery
            self.was_restarted = True
            logger.info("State recovered successfully from local storage.")
        except Exception as e:
            logger.error(f"State load failed: {e}")

state = StateManager()
