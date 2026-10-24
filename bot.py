import logging
import time
import psutil
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
from security import is_authorized, mask_secrets
from state import state

logger = logging.getLogger("telegram_bot")
START_TIME = time.time()

def get_uptime_string() -> str:
    elapsed = int(time.time() - START_TIME)
    mins, secs = divmod(elapsed, 60)
    hours, mins = divmod(mins, 60)
    days, hours = divmod(hours, 24)
    if days > 0:
        return f"{days}d {hours}h {mins}m"
    if hours > 0:
        return f"{hours}h {mins}m {secs}s"
    return f"{mins}m {secs}s"

def get_memory_info() -> tuple[int, int, float]:
    try:
        mem = psutil.virtual_memory()
        used_mb = int(mem.used / (1024 * 1024))
        total_mb = int(mem.total / (1024 * 1024))
        cpu_percent = psutil.cpu_percent(interval=None)
        return used_mb, total_mb, cpu_percent
    except Exception:
        return 0, 512, 0.0

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user):
        return

    active_id = state.active_conversation_id
    active_conv = state.conversations.get(active_id, {})
    conv_title = active_conv.get("title", "Default Session")

    text = (
        "👋 **Cloud PC Bot Active Hai!**\n\n"
        "Aapka Remote Cloud PC ready hai. Main sirf aapke (@OwAtom) orders leta hoon.\n\n"
        f"⚙️ **Status:**\n"
        f"• **Owner**: @{config.ALLOWED_USERNAME} (Verified ✅)\n"
        f"• **Session**: {conv_title}\n"
        f"• **Mode**: `{state.mode}` (Queue mode)\n"
        f"• **Security Lock**: Active (Baki sab block)\n\n"
        "📋 **Commands:**\n"
        "• `/status` - Server RAM aur Uptime dekhein\n"
        "• `/new` - Naya session/conversation shuru karein\n"
        "• `/conversations` - Sessions ki list dekhein aur switch karein\n"
        "• `/help` - Commands ki list\n"
    )
    await update.message.reply_text(mask_secrets(text), parse_mode="Markdown")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user):
        return

    used_mb, total_mb, cpu = get_memory_info()
    uptime_str = get_uptime_string()
    active_id = state.active_conversation_id
    active_conv = state.conversations.get(active_id, {})
    conv_title = active_conv.get("title", "Default Session")

    text = (
        "📊 **Cloud PC Server Status**\n\n"
        "• **Bot**: Online & Healthy ✅\n"
        f"• **RAM Usage**: {used_mb} MB / {total_mb} MB\n"
        f"• **CPU**: {cpu}%\n"
        f"• **Uptime**: {uptime_str}\n"
        f"• **Active Session**: {conv_title} (`{active_id[:8]}`)\n"
        f"• **Total Sessions**: {len(state.conversations)}\n"
        f"• **Queue Mode**: `{state.mode}`\n"
        "• **Health Check**: `/health` endpoint live hai\n"
    )
    await update.message.reply_text(mask_secrets(text), parse_mode="Markdown")

async def new_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user):
        return

    # Check if user provided an optional custom title
    args = context.args or []
    custom_title = " ".join(args).strip() if args else None

    new_id = state.new_conversation(title=custom_title)
    conv = state.conversations[new_id]

    text = (
        "✨ **Naya Conversation Shuru Ho Gaya!**\n\n"
        f"• **Title**: {conv['title']}\n"
        f"• **Session ID**: `{new_id[:8]}...`\n\n"
        "Aapka purana kaam bilkul safe hai. Switch karne ke liye `/conversations` dabayein."
    )
    await update.message.reply_text(mask_secrets(text), parse_mode="Markdown")

async def conversations_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user):
        return

    convs = state.list_conversations()
    buttons = []
    text_lines = ["🗂️ **Aapke Saved Conversations / Sessions:**\n"]

    for c in convs:
        is_active = (c["id"] == state.active_conversation_id)
        indicator = "🟢 (Active)" if is_active else ""
        text_lines.append(f"• **{c['title']}** `{c['id'][:8]}` {indicator}")
        if not is_active:
            buttons.append([
                InlineKeyboardButton(
                    f"👉 Switch to: {c['title'][:20]}",
                    callback_data=f"switch_conv:{c['id']}"
                )
            ])

    buttons.append([InlineKeyboardButton("➕ Naya Conversation", callback_data="new_conv")])
    reply_markup = InlineKeyboardMarkup(buttons)
    await update.message.reply_text("\n".join(text_lines), reply_markup=reply_markup, parse_mode="Markdown")

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_authorized(query.from_user):
        return

    await query.answer()
    data = query.data or ""

    if data.startswith("switch_conv:"):
        conv_id = data.split(":", 1)[1]
        if state.switch_conversation(conv_id):
            conv = state.conversations.get(conv_id, {})
            await query.edit_message_text(
                f"✅ Switched to session: **{conv.get('title', 'Session')}** (`{conv_id[:8]}`)",
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text("❌ Session nahi mila.")

    elif data == "new_conv":
        new_id = state.new_conversation()
        conv = state.conversations[new_id]
        await query.edit_message_text(
            f"✨ Naya session ban gaya aur active ho gaya:\n**{conv['title']}** (`{new_id[:8]}`)",
            parse_mode="Markdown"
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user):
        return

    text = (
        "📖 **Commands List (Simple Hinglish):**\n\n"
        "• `/start` - Bot status aur intro\n"
        "• `/status` - Server RAM, CPU aur uptime\n"
        "• `/new <naam>` - Naya conversation/session shuru karein\n"
        "• `/conversations` - Saved conversations dekhein aur switch karein\n"
        "• `/help` - Ye madad menu\n\n"
        "Agli step mein `agy` runner, queue aur mini app terminal judenge!"
    )
    await update.message.reply_text(mask_secrets(text), parse_mode="Markdown")

async def unauthorized_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Silently drop non-command messages from unauthorized users
    if not is_authorized(update.effective_user):
        return

def create_bot_application() -> Application:
    app = ApplicationBuilder().token(config.BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("new", new_command))
    app.add_handler(CommandHandler("conversations", conversations_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    app.add_handler(MessageHandler(filters.ALL, unauthorized_filter))

    return app
