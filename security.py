import logging
import os
import re
from pathlib import Path
from telegram import User
import config

logger = logging.getLogger("security")

FORBIDDEN_DIRS = ["/etc", "/usr", "/root", "/var", "/bin", "/sbin", "/boot", "/dev", "/proc", "/sys"]

def is_authorized(user: User | None) -> bool:
    """Strict authorization: only ALLOWED_USER_ID or ALLOWED_USERNAME accepted."""
    if not user:
        return False

    if config.ALLOWED_USER_ID is not None and user.id == config.ALLOWED_USER_ID:
        return True

    username = (user.username or "").lower().lstrip("@")
    if config.ALLOWED_USERNAME and username == config.ALLOWED_USERNAME:
        if config.ALLOWED_USER_ID is None:
            config.set_allowed_user_id(user.id)
            logger.info(f"Verified and locked ALLOWED_USER_ID to {user.id} for username @{username}")
        return True

    logger.warning(
        f"Unauthorized access dropped: ID={user.id}, username=@{user.username}, name={user.full_name}"
    )
    return False

def mask_secrets(text: str) -> str:
    """Mask known sensitive secrets from text before sending to chat or terminal."""
    if not text:
        return text

    sensitive_values = [
        config.BOT_TOKEN,
        config.GITHUB_TOKEN,
        config.GEMINI_API_KEY,
    ]

    clean = text
    for val in sensitive_values:
        if val and len(val) > 4:
            clean = clean.replace(val, "********")

    # Regex patterns for Telegram Bot Tokens (e.g., 123456789:AA...)
    clean = re.sub(r"\b\d{8,10}:[a-zA-Z0-9_-]{35}\b", "********", clean)

    # Regex patterns for GitHub tokens (ghp_..., gho_...)
    clean = re.sub(r"\bgh[pousr]_[a-zA-Z0-9]{36}\b", "********", clean)

    # Regex patterns for generic API keys
    clean = re.sub(r"\bAIza[0-9A-Za-z-_]{35}\b", "********", clean)

    return clean

def sanitize_filename(filename: str) -> str:
    """Remove unsafe path traversal characters like ../ or full paths."""
    base = os.path.basename(filename)
    clean = re.sub(r"[^a-zA-Z0-9_.\-\s]", "_", base)
    return clean or "uploaded_file"

def is_path_safe(target_path: Path, base_dir: Path = config.WORKSPACE_DIR) -> bool:
    """Ensure target path does not escape base workspace directory."""
    try:
        resolved_target = target_path.resolve()
        resolved_base = base_dir.resolve()
        return resolved_base in resolved_target.parents or resolved_target == resolved_base
    except Exception:
        return False

def check_command_safety(command_str: str) -> tuple[bool, str]:
    """Check if a shell command tries to touch sensitive system paths."""
    cmd_lower = command_str.lower()
    for forbidden in FORBIDDEN_DIRS:
        pattern = rf"(^|\s|/|\.){re.escape(forbidden[1:])}(/|\s|$)"
        if forbidden in cmd_lower or re.search(pattern, cmd_lower):
            if any(allowed in cmd_lower for allowed in ["/usr/bin/python", "/usr/bin/git", "/usr/bin/curl", "/usr/local/bin"]):
                continue
            if "rm " in cmd_lower or "mv " in cmd_lower or "cp " in cmd_lower or "chmod " in cmd_lower:
                return False, f"System path '{forbidden}' modify karna blocked hai."
    return True, ""
