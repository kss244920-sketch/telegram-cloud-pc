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
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ALLOWED_USERNAME = os.getenv("ALLOWED_USERNAME", "OwAtom").lower().lstrip("@")
_allowed_user_id_raw = os.getenv("ALLOWED_USER_ID", "").strip()
ALLOWED_USER_ID: int | None = int(_allowed_user_id_raw) if _allowed_user_id_raw.isdigit() else None

PORT = int(os.getenv("PORT", "8000"))
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_API_KEYS = os.getenv("GEMINI_API_KEYS", "")

def get_initial_api_keys() -> list[str]:
    """Extract initial list of API keys from env vars."""
    keys = []
    raw = os.getenv("GEMINI_API_KEYS", "")
    if raw:
        import re
        keys = [k.strip() for k in re.split(r"[\s,;]+", raw) if k.strip()]
    if not keys and GEMINI_API_KEY:
        keys = [GEMINI_API_KEY.strip()]
    return keys

_channel_id_raw = os.getenv("STORAGE_CHANNEL_ID", "-1004343308436").strip()
try:
    STORAGE_CHANNEL_ID = int(_channel_id_raw)
except ValueError:
    STORAGE_CHANNEL_ID = -1004343308436

def set_allowed_user_id(user_id: int):
    """Dynamically locks the allowed numerical ID once user @OwAtom messages the bot."""
    global ALLOWED_USER_ID
    ALLOWED_USER_ID = user_id

def restore_auth():
    """Restores agy authentication bundle from environment variable if present, only when not in API key mode."""
    # When API keys are configured, skip OAuth bundle restore so agy uses GEMINI_API_KEY directly!
    if os.getenv("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEYS"):
        return
    raw_b64 = os.getenv("AGY_AUTH_DATA", "").strip()
    if not raw_b64:
        return
    try:
        import tarfile, io, base64
        # Restore into ~/.gemini/antigravity-cli
        dest_dirs = [
            Path.home() / ".gemini",
            Path("/root/.gemini")
        ]
        for dest in dest_dirs:
            try:
                dest.mkdir(parents=True, exist_ok=True)
                buf = io.BytesIO(base64.b64decode(raw_b64))
                with tarfile.open(fileobj=buf, mode="r:gz") as tar:
                    tar.extractall(path=dest)
            except Exception:
                pass
    except Exception as e:
        print("Failed to restore agy auth bundle:", e)

# Unpack auth if available
restore_auth()
