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
        self.active_conversation_id = str(uuid.uuid4())
        self.conversations = {}
        self.prompt_buffer = []  # list of items waiting for Proceed
        self.buffer_message_id: int | None = None
        self.history = []
        self.last_terminal_output = ""
        self.was_restarted = False
        
        self.load()
        if not self.conversations:
            self.new_conversation(title="Default Session")

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
        new_id = str(uuid.uuid4())
        timestamp = datetime.utcnow().isoformat()
        conv_title = title or f"Session {len(self.conversations) + 1}"
        self.conversations[new_id] = {
            "id": new_id,
            "title": conv_title,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        self.active_conversation_id = new_id
        self.save()
        return new_id

    def switch_conversation(self, conv_id: str) -> bool:
        if conv_id in self.conversations:
            self.active_conversation_id = conv_id
            self.save()
            return True
        return False

    def list_conversations(self) -> list[dict]:
        return list(self.conversations.values())

    def get_project_dir(self) -> Path:
        pdir = config.WORKSPACE_DIR / self.current_project
        pdir.mkdir(parents=True, exist_ok=True)
        return pdir

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
            "last_save": datetime.utcnow().isoformat(),
        }
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"State save failed: {e}")

    def load(self):
        if not STATE_FILE.exists():
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
            
            # If state file was loaded, mark restart recovery
            self.was_restarted = True
            logger.info("State recovered successfully from local storage.")
        except Exception as e:
            logger.error(f"State load failed: {e}")

state = StateManager()
