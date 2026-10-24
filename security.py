import logging
import re
from telegram import User
import config

logger = logging.getLogger("security")

def is_authorized(user: User | None) -> bool:
    """
    Strict security check:
    Only ALLOWED_USER_ID or ALLOWED_USERNAME can interact with this bot.
    Anyone else is completely and silently dropped (no reply, no error).
    """
    if not user:
        return False

    # Check numeric ID if known
    if config.ALLOWED_USER_ID is not None and user.id == config.ALLOWED_USER_ID:
        return True

    # Check username
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

    return clean
