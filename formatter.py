import re
import html
import uuid
import logging
from telegram import Bot
from telegram.constants import ParseMode
from security import mask_secrets

logger = logging.getLogger("formatter")

def markdown_to_telegram_html(text: str) -> str:
    """Converts GitHub-flavored markdown to Telegram HTML formatting."""
    if not text:
        return ""

    token = uuid.uuid4().hex[:8]
    code_blocks = []
    inline_codes = []

    # 1. Protect fenced code blocks (```lang ... ```)
    def save_code_block(m):
        lang = (m.group(1) or "").strip().lower()
        code = m.group(2)
        escaped_code = html.escape(code.rstrip("\n"))
        idx = len(code_blocks)
        if lang:
            code_blocks.append(f"<pre><code class=\"language-{html.escape(lang)}\">{escaped_code}</code></pre>")
        else:
            code_blocks.append(f"<pre>{escaped_code}</pre>")
        return f"@@CB{token}_{idx}@@"

    text = re.sub(r"```([a-zA-Z0-9_\-\+]*)\n?(.*?)```", save_code_block, text, flags=re.DOTALL)

    # 2. Protect inline code (`code`)
    def save_inline_code(m):
        code = m.group(1)
        escaped_code = html.escape(code)
        idx = len(inline_codes)
        inline_codes.append(f"<code>{escaped_code}</code>")
        return f"@@IC{token}_{idx}@@"

    text = re.sub(r"`([^`\n]+)`", save_inline_code, text)

    # 3. HTML-escape remaining text
    text = html.escape(text)

    # 4. Handle GitHub-style alerts (> [!NOTE], > [!TIP], > [!WARNING], > [!IMPORTANT], > [!CAUTION])
    def replace_alert(m):
        alert_type = m.group(1).upper()
        content = m.group(2)
        cleaned_content = re.sub(r"^&gt;\s?", "", content, flags=re.MULTILINE).strip()
        icons = {
            "NOTE": "ℹ️ Note",
            "TIP": "💡 Tip",
            "IMPORTANT": "❗ Important",
            "WARNING": "⚠️ Warning",
            "CAUTION": "🛑 Caution"
        }
        title = icons.get(alert_type, f"ℹ️ {alert_type.capitalize()}")
        idx = len(code_blocks)
        code_blocks.append(f"<blockquote><b>{title}:</b>\n{cleaned_content}</blockquote>")
        return f"@@CB{token}_{idx}@@"

    text = re.sub(r"^&gt;\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*\n?((?:&gt;[^\n]*\n?)*)", replace_alert, text, flags=re.MULTILINE)

    # 5. Handle blockquotes (> quote)
    def replace_quote(m):
        quote_content = m.group(0)
        cleaned = re.sub(r"^&gt;\s?", "", quote_content, flags=re.MULTILINE).strip()
        idx = len(code_blocks)
        code_blocks.append(f"<blockquote>{cleaned}</blockquote>")
        return f"@@CB{token}_{idx}@@"

    text = re.sub(r"(?:^&gt;[^\n]*\n?)+", replace_quote, text, flags=re.MULTILINE)

    # 6. Headers (# Heading) -> <b>Heading</b>
    text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)

    # 7. Horizontal rules (--- or *** or ___)
    text = re.sub(r"^[ \t]*[-*_]{3,}[ \t]*$", "", text, flags=re.MULTILINE)

    # 8. Bold (**text** or __text__)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text, flags=re.DOTALL)

    # 9. Italic (*text* or _text_)
    text = re.sub(r"(?<!\w)\*([^\*\n]+?)\*(?!\w)", r"<i>\1</i>", text)
    text = re.sub(r"(?<![a-zA-Z0-9])_([^_\n]+?)_(?![a-zA-Z0-9])", r"<i>\1</i>", text)

    # 10. Strikethrough (~~text~~)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text, flags=re.DOTALL)

    # 11. Links [text](url) -> <a href="url">text</a>
    text = re.sub(r"\[([^\]]+)\]\((https?://[^\s\)]+)\)", r"<a href=\"\2\">\1</a>", text)

    # 12. Bullet points (- item or * item) -> • item
    text = re.sub(r"^[ \t]*[-\*]\s+", "• ", text, flags=re.MULTILINE)

    # 13. Restore code blocks and inline code
    for i, code_html in enumerate(inline_codes):
        text = text.replace(f"@@IC{token}_{i}@@", code_html)
    for i, block_html in enumerate(code_blocks):
        text = text.replace(f"@@CB{token}_{i}@@", block_html)

    # 14. Clean excessive newlines
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def split_markdown_chunks(text: str, max_chars: int = 3000) -> list[str]:
    """Splits markdown safely without breaking code blocks or paragraphs."""
    if len(text) <= max_chars:
        return [text]

    chunks = []
    lines = text.split("\n")
    current_lines = []
    current_len = 0
    in_code_block = False
    code_lang = ""

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_code_block:
                in_code_block = True
                code_lang = stripped[3:].strip()
            else:
                in_code_block = False
                code_lang = ""

        # Handle extraordinarily long lines
        if len(line) > max_chars:
            if current_lines:
                chunks.append("\n".join(current_lines))
                current_lines = []
                current_len = 0
            for pos in range(0, len(line), max_chars):
                chunks.append(line[pos:pos + max_chars])
            continue

        if current_len + len(line) + 1 > max_chars:
            if in_code_block:
                current_lines.append("```")
                chunks.append("\n".join(current_lines))
                current_lines = [f"```{code_lang}", line]
                current_len = len(current_lines[0]) + len(line) + 2
            else:
                chunks.append("\n".join(current_lines))
                current_lines = [line]
                current_len = len(line) + 1
        else:
            current_lines.append(line)
            current_len += len(line) + 1

    if current_lines:
        chunks.append("\n".join(current_lines))

    return chunks


async def send_formatted_message(bot: Bot, chat_id: int, text: str):
    """Formats markdown into Telegram HTML and sends safely."""
    masked = mask_secrets(text)
    chunks = split_markdown_chunks(masked, max_chars=3000)

    for chunk in chunks:
        html_content = markdown_to_telegram_html(chunk)
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=html_content,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.warning(f"Telegram HTML send failed ({e}), attempting fallback...")
            try:
                plain = re.sub(r"<[^>]+>", "", html_content)
                plain = html.unescape(plain)
                await bot.send_message(chat_id=chat_id, text=plain)
            except Exception as e2:
                logger.error(f"Fallback plain text send also failed: {e2}")
