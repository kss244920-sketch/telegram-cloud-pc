import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env if present
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = BASE_DIR / "workspace"
INBOX_DIR = BASE_DIR / "inbox"
DATA_DIR = BASE_DIR / "data"

WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
INBOX_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Bot & Auth configuration
BOT_TOKEN = os.getenv("BOT_TOKEN", "8798239169:AAFbzjM4n7wYzwVNrLbiw-cw0irXIGLI8yI")
ALLOWED_USERNAME = os.getenv("ALLOWED_USERNAME", "OwAtom").lower().lstrip("@")
_allowed_user_id_raw = os.getenv("ALLOWED_USER_ID", "").strip()
ALLOWED_USER_ID: int | None = int(_allowed_user_id_raw) if _allowed_user_id_raw.isdigit() else None

PORT = int(os.getenv("PORT", "8000"))
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

def set_allowed_user_id(user_id: int):
    """Dynamically locks the allowed numerical ID once user @OwAtom messages the bot."""
    global ALLOWED_USER_ID
    ALLOWED_USER_ID = user_id
