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
DEFAULT_AGY_MODEL = os.getenv("DEFAULT_AGY_MODEL", "Gemini 3.8 Flash (High)")

def is_valid_gemini_key(key: str) -> bool:
    return bool(key and key.strip().startswith("AIzaSy"))

def get_initial_api_keys() -> list[str]:
    """Extract initial list of API keys from env vars, filtering only valid AIzaSy keys."""
    keys = []
    raw = os.getenv("GEMINI_API_KEYS", "")
    if raw:
        import re
        keys = [k.strip() for k in re.split(r"[\s,;]+", raw) if is_valid_gemini_key(k.strip())]
    if not keys and is_valid_gemini_key(GEMINI_API_KEY):
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

def configure_agy_settings(use_gemini_api: bool = False):
    """
    Configures ~/.gemini/antigravity-cli/settings.json and /root/.gemini/antigravity-cli/settings.json
    so that agy knows whether to use Gemini API keys directly or use Google OAuth authentication.
    """
    import json
    dest_dirs = [
        Path.home() / ".gemini" / "antigravity-cli",
        Path("/root/.gemini/antigravity-cli"),
        Path.home() / ".gemini",
        Path("/root/.gemini")
    ]
    for d in dest_dirs:
        try:
            d.mkdir(parents=True, exist_ok=True)
            settings_path = d / "settings.json"
            data = {}
            if settings_path.exists():
                try:
                    with open(settings_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    data = {}
            if use_gemini_api:
                data["modelProvider"] = "gemini"
                data["model"] = DEFAULT_AGY_MODEL
            else:
                data.pop("modelProvider", None)
                data.pop("model", None)

            with open(settings_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            pass

def restore_auth():
    """Restores agy authentication bundle from environment variable if present, only when not in API key mode."""
    # When valid API keys are configured, skip OAuth bundle restore so agy uses GEMINI_API_KEY directly!
    valid_keys = get_initial_api_keys()
    if valid_keys:
        configure_agy_settings(use_gemini_api=True)
        return
    raw_b64 = os.getenv("AGY_AUTH_DATA", "").strip()
    if not raw_b64:
        configure_agy_settings(use_gemini_api=False)
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
        configure_agy_settings(use_gemini_api=False)
    except Exception as e:
        print("Failed to restore agy auth bundle:", e)

# Unpack auth or configure mode
restore_auth()
