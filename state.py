import json
import uuid
from datetime import datetime
from pathlib import Path
import config

STATE_FILE = config.DATA_DIR / "state.json"

class StateManager:
    def __init__(self):
        self.mode = "queue"  # 'queue' or 'instant'
        self.current_project = "default"
        self.active_conversation_id = str(uuid.uuid4())
        self.conversations = {
            self.active_conversation_id: {
                "id": self.active_conversation_id,
                "title": "Initial Session",
                "created_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat(),
            }
        }
        self.queue = []
        self.history = []
        self.load()

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

    def save(self):
        data = {
            "mode": self.mode,
            "current_project": self.current_project,
            "active_conversation_id": self.active_conversation_id,
            "conversations": self.conversations,
            "allowed_user_id": config.ALLOWED_USER_ID,
        }
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def load(self):
        if not STATE_FILE.exists():
            return
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.mode = data.get("mode", self.mode)
            self.current_project = data.get("current_project", self.current_project)
            self.conversations = data.get("conversations", self.conversations)
            self.active_conversation_id = data.get("active_conversation_id", self.active_conversation_id)
            if data.get("allowed_user_id") and config.ALLOWED_USER_ID is None:
                config.set_allowed_user_id(data["allowed_user_id"])
        except Exception:
            pass

state = StateManager()
