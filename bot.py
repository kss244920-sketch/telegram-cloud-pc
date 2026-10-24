import logging
import os
import time
import shutil
import asyncio
import psutil
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
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
from runner import runner
from queue_manager import queue_manager
from file_manager import FileManager
from git_manager import GitManager

logger = logging.getLogger("telegram_bot")
START_TIME = time.time()

def get_mini_app_url() -> str:
    base = os.getenv("RENDER_EXTERNAL_URL", "https://telegram-cloud-pc.onrender.com")
    return base.rstrip("/")

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

# ----------------- Command Handlers -----------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user):
        return

    active_id = state.active_conversation_id
    active_conv = state.conversations.get(active_id, {})
    conv_title = active_conv.get("title", "Default Session")
    mini_app_url = get_mini_app_url()

    keyboard = [
        [InlineKeyboardButton("📟 Terminal Kholo (Mini App)", web_app=WebAppInfo(url=mini_app_url))],
        [InlineKeyboardButton("📋 Kaam Ki List (Queue)", callback_data="open_queue")],
    ]

    restart_msg = "\n_🔔 Server restart ke baad wapas aa gaya, sab safe hai!_\n" if state.was_restarted else ""

    text = (
        f"👋 **Namaste! Cloud PC Remote Bot Active Hai!**\n{restart_msg}\n"
        "Main aapka personal remote assistant hoon jo Render cloud machine par chalta hai.\n\n"
        f"⚙️ **System Config:**\n"
        f"• **Owner**: @{config.ALLOWED_USERNAME} (Verified Lock ✅)\n"
        f"• **Active Session**: {conv_title}\n"
        f"• **Mode**: `{state.mode}` (Queue Mode)\n"
        f"• **Auto-Allow**: ON (Bina permission maange fast execution)\n\n"
        "💡 **Kaise use karein:**\n"
        "1. Kuch bhi instruction, code, ya link chat mein bhejte jayein.\n"
        "2. Saare kaam list mein judenge, aakhir mein **PROCEED** dabayein.\n"
        "3. Real terminal dekhne ke liye neeche button dabayein!"
    )
    await update.message.reply_text(
        mask_secrets(text),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user):
        return

    used_mb, total_mb, cpu = get_memory_info()
    uptime_str = get_uptime_string()
    active_id = state.active_conversation_id
    active_conv = state.conversations.get(active_id, {})
    conv_title = active_conv.get("title", "Default Session")
    mini_app_url = get_mini_app_url()

    keyboard = [
        [InlineKeyboardButton("📟 Terminal Kholo", web_app=WebAppInfo(url=mini_app_url))],
    ]

    text = (
        "📊 **Cloud PC Status**\n\n"
        "• **Server**: Render Free Web Service (Online ✅)\n"
        f"• **RAM Usage**: {used_mb} MB / {total_mb} MB\n"
        f"• **CPU**: {cpu}%\n"
        f"• **Uptime**: {uptime_str}\n"
        f"• **Active Session**: {conv_title} (`{active_id[:8]}`)\n"
        f"• **Queue Items**: {len(state.queue)} bache hain\n"
        f"• **Mode**: `{state.mode}`\n"
        "• **Health Check**: `/health` (Alive)\n"
    )
    await update.message.reply_text(
        mask_secrets(text),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def queue_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user):
        return

    text, markup = queue_manager.build_queue_text_and_markup()
    await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user):
        return

    if runner.is_running:
        stopped = runner.stop_current_task()
        if stopped:
            await update.message.reply_text("🛑 Chal raha kaam rokne ka signal bhej diya gaya hai!")
        else:
            await update.message.reply_text("⚠️ Process rokne mein problem aayi.")
    else:
        await update.message.reply_text("ℹ️ Abhi koi kaam nahi chal raha.")

async def new_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user):
        return

    args = context.args or []
    custom_title = " ".join(args).strip() if args else None
    new_id = state.new_conversation(title=custom_title)
    conv = state.conversations[new_id]

    text = (
        "✨ **Naya Conversation Shuru Ho Gaya!**\n\n"
        f"• **Title**: {conv['title']}\n"
        f"• **ID**: `{new_id[:8]}...`\n\n"
        "Aapka purana kaam safe hai. Sessions dekhne ke liye `/conversations` dabayein."
    )
    await update.message.reply_text(mask_secrets(text), parse_mode="Markdown")

async def conversations_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user):
        return

    convs = state.list_conversations()
    buttons = []
    text_lines = ["🗂️ **Aapke Saved Conversations:**\n"]

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

async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user):
        return

    args = context.args or []
    if args and args[0].lower() in ["instant", "queue"]:
        state.mode = args[0].lower()
        state.save()
        await update.message.reply_text(f"✅ Mode badal kar `{state.mode}` kar diya gaya hai.")
    else:
        await update.message.reply_text(
            f"ℹ️ Current mode: `{state.mode}`\n\nBadalne ke liye likhein: `/mode instant` ya `/mode queue`"
        )

async def files_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user):
        return

    pdir = state.get_project_dir()
    header, markup = FileManager.build_file_browser(pdir)
    await update.message.reply_text(header, reply_markup=markup, parse_mode="Markdown")

async def get_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user):
        return

    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: `/get filename.py`")
        return

    fname = " ".join(args).strip()
    pdir = state.get_project_dir()
    target = pdir / fname

    if target.exists() and target.is_file():
        await update.message.reply_document(document=open(target, "rb"), caption=f"📄 {target.name}")
    else:
        # Search recursively
        matches = list(pdir.rglob(fname))
        if matches and matches[0].is_file():
            await update.message.reply_document(document=open(matches[0], "rb"), caption=f"📄 {matches[0].name}")
        else:
            await update.message.reply_text(f"❌ File '{fname}' nahi mili.")

async def zip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user):
        return

    msg = await update.message.reply_text("📦 Project ki zip ban rahi hai...")
    pdir = state.get_project_dir()
    try:
        zip_p = FileManager.create_project_zip(pdir)
        await update.message.reply_document(
            document=open(zip_p, "rb"),
            caption=f"📦 {zip_p.name} ({zip_p.stat().st_size // 1024} KB)"
        )
        await msg.delete()
    except Exception as e:
        await msg.edit_text(f"❌ Zip banane mein error: {e}")

async def undo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user):
        return

    pdir = state.get_project_dir()
    ok, out = GitManager.undo_last_checkpoint(pdir)
    if ok:
        await update.message.reply_text(f"⏪ **Undo Successful!**\n{out}")
    else:
        await update.message.reply_text(f"❌ {out}")

async def sync_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user):
        return

    pdir = state.get_project_dir()
    ok, out = GitManager.auto_commit_and_push(pdir, "chore: manual sync from telegram bot")
    await update.message.reply_text(f"🔄 **Sync Status:** {out}")

active_login_process: asyncio.subprocess.Process | None = None

def save_auth_bundle_to_render() -> bool:
    """Auto-persists newly created authentication files to Render env vars."""
    import tarfile, io, base64, urllib.request, json
    gemini_dir = Path.home() / ".gemini" / "antigravity-cli"
    if not (gemini_dir / "implicit").exists():
        gemini_dir = Path("/root/.gemini/antigravity-cli")
    if not (gemini_dir / "implicit").exists():
        return False

    try:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            if (gemini_dir / "jetski_state.pbtxt").exists():
                tar.add(str(gemini_dir / "jetski_state.pbtxt"), arcname="jetski_state.pbtxt")
            if (gemini_dir / "installation_id").exists():
                tar.add(str(gemini_dir / "installation_id"), arcname="installation_id")
            tar.add(str(gemini_dir / "implicit"), arcname="implicit")

        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        render_api_key = "rnd_3tagyUF2IqkgZyiRiBHD7CbZjxAg"
        service_id = "srv-dbe4n88m7kps73capeog"
        url = f"https://api.render.com/v1/services/{service_id}/env-vars"
        headers = {
            "Authorization": f"Bearer {render_api_key}",
            "Content-Type": "application/json"
        }
        data = [
            {"key": "BOT_TOKEN", "value": config.BOT_TOKEN},
            {"key": "ALLOWED_USERNAME", "value": config.ALLOWED_USERNAME},
            {"key": "PORT", "value": str(config.PORT)},
            {"key": "RENDER_EXTERNAL_URL", "value": "https://telegram-cloud-pc-docker.onrender.com"},
            {"key": "AGY_AUTH_DATA", "value": b64}
        ]
        req = urllib.request.Request(url, data=json.dumps(data).encode(), headers=headers, method="PUT")
        with urllib.request.urlopen(req) as resp:
            return resp.status == 200
    except Exception as e:
        logger.error(f"Failed to auto-save auth bundle to Render: {e}")
        return False

async def login_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active_login_process
    if not is_authorized(update.effective_user):
        return

    msg = await update.message.reply_text("⏳ Google OAuth Sign-in link generate ho rahi hai...")

    agy_bin = shutil.which("agy") or "/usr/local/bin/agy"
    env = os.environ.copy()
    env["PAGER"] = "cat"
    env["SYSTEMD_PAGER"] = "cat"

    try:
        active_login_process = await asyncio.create_subprocess_exec(
            agy_bin, "-p", "hello",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env
        )

        auth_url = ""
        start_time = time.time()
        while time.time() - start_time < 20:
            line_bytes = await active_login_process.stdout.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="ignore").strip()
            if "https://accounts.google.com/o/oauth2/auth" in line:
                for p in line.split():
                    if p.startswith("https://accounts.google.com/o/oauth2/auth"):
                        auth_url = p
                        break
                if auth_url:
                    break

        if auth_url:
            text = (
                "🔐 **Google Sign-In Link (Asli Poora Link):**\n\n"
                f"[👉 Yahan Click Karke Login Karein]({auth_url})\n\n"
                "Browser mein Sign-in karne ke baad jo **Authorization Code** mile, use chat mein bhejein:\n"
                "`/code <aapka_code>`\n\n"
                "*(Link 60 second ke liye active hai. Code bhejte hi login permanently save ho jayega)*"
            )
            await msg.edit_text(text, parse_mode="Markdown")
        else:
            await msg.edit_text("ℹ️ Agy pehle se authenticated hai ya direct API key mode active hai.")
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")

async def code_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active_login_process
    if not is_authorized(update.effective_user):
        return

    args = context.args or []
    if not args or not active_login_process:
        await update.message.reply_text("Usage: `/code <authorization_code>`\n\n(Pehle `/login` dabayein)")
        return

    code_val = args[0].strip()
    status_msg = await update.message.reply_text("⏳ Code verify ho raha hai...")

    try:
        active_login_process.stdin.write(f"{code_val}\n".encode())
        await active_login_process.stdin.drain()
        await asyncio.sleep(2)

        try:
            await update.message.delete()
        except Exception:
            pass

        saved = save_auth_bundle_to_render()
        save_note = "\n\n✅ Credentials Render environment mein permanently save ho gaye! Restart par bhi login nahi udega." if saved else ""
        await status_msg.edit_text(
            f"🎉 **Safalta Se Login Ho Gaya!**\n\nAb agy fully authenticated hai!{save_note}",
            parse_mode="Markdown"
        )
    except Exception as e:
        await status_msg.edit_text(f"❌ Error verifying code: {e}")
    finally:
        active_login_process = None

async def key_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user):
        return

    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: `/key AIzaSy...`", parse_mode="Markdown")
        return

    new_key = args[0].strip()
    config.GEMINI_API_KEY = new_key
    os.environ["GEMINI_API_KEY"] = new_key
    state.save()

    try:
        await update.message.delete()
    except Exception:
        pass

    await update.effective_chat.send_message("✅ API Key successfully set ho gayi aur secure kar di gayi!")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user):
        return

    text = (
        "📖 **Commands Guide (Simple Hinglish):**\n\n"
        "• `/start` - Bot status aur Terminal button\n"
        "• `/queue` - Kaam ki list dekhein aur PROCEED dabayein\n"
        "• `/stop` - Chal raha task turant rokein\n"
        "• `/status` - Server RAM, CPU aur uptime dekhein\n"
        "• `/new <naam>` - Naya session/conversation shuru karein\n"
        "• `/conversations` - Saved conversations switch karein\n"
        "• `/files` - Files browser kholein\n"
        "• `/get <file>` - Kisi file ko chat mein mangwayein\n"
        "• `/zip` - Pura project zip download karein\n"
        "• `/undo` - Aakhri checkpoint par wapas jayein\n"
        "• `/sync` - GitHub par push karein\n"
        "• `/mode` - Instant ya Queue mode chunein\n"
    )
    await update.message.reply_text(mask_secrets(text), parse_mode="Markdown")

# ----------------- Message & File Handlers -----------------

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user):
        return

    text = update.message.text.strip()
    if not text:
        return

    pdir = state.get_project_dir()

    if state.mode == "instant":
        # Run immediately
        status_msg = await update.message.reply_text("⚡ Instant mode: Kaam shuru ho raha hai...")
        await runner.run_task(
            prompt=text,
            project_dir=pdir,
            bot=context.bot,
            chat_id=update.effective_chat.id,
            live_message_id=status_msg.message_id,
            conversation_id=state.active_conversation_id
        )
    else:
        # Add to queue
        res = queue_manager.add_item(title=text, item_type="text")
        if res.get("is_next_round"):
            await update.message.reply_text(f"⏳ Kaam chal raha hai, isliye ye item **Agle Round** mein jod diya gaya: \"{text[:40]}\"")
        else:
            q_text, markup = queue_manager.build_queue_text_and_markup()
            await update.message.reply_text(q_text, reply_markup=markup, parse_mode="Markdown")

async def handle_document_or_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user):
        return

    message = update.message
    caption = message.caption or ""
    pdir = state.get_project_dir()

    tg_file = None
    original_name = "file"

    if message.document:
        tg_file = await message.document.get_file()
        original_name = message.document.file_name or "document"
    elif message.photo:
        tg_file = await message.photo[-1].get_file()
        original_name = f"photo_{int(time.time())}.jpg"

    if not tg_file:
        return

    content = await tg_file.download_as_bytearray()
    saved_info = FileManager.save_file(bytes(content), original_name, pdir)

    confirm_msg = f"📥 `{saved_info['name']}` save hui ({saved_info['size_kb']} KB)"
    if saved_info.get("unzip_info"):
        confirm_msg += f"\n{saved_info['unzip_info']}"

    # Add to queue
    item_title = f"{saved_info['name']} (file)" + (f": {caption}" if caption else "")
    res = queue_manager.add_item(
        title=item_title,
        item_type="file",
        file_path=str(saved_info["rel_path"]),
        caption=caption
    )

    if res.get("is_next_round"):
        await update.message.reply_text(f"{confirm_msg}\n⏳ Current run ke baad Agle Round mein chalegi.")
    else:
        q_text, markup = queue_manager.build_queue_text_and_markup()
        await update.message.reply_text(f"{confirm_msg}\n\n{q_text}", reply_markup=markup, parse_mode="Markdown")

# ----------------- Callback Queries -----------------

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_authorized(query.from_user):
        return

    await query.answer()
    data = query.data or ""
    pdir = state.get_project_dir()

    if data == "q_proceed":
        if queue_manager.is_processing:
            await query.answer("Pehle se run chal raha hai!", show_alert=True)
            return
        asyncio.create_task(
            queue_manager.process_queue(
                bot=context.bot,
                chat_id=query.message.chat_id,
                project_dir=pdir
            )
        )

    elif data == "q_clear":
        queue_manager.clear()
        q_text, markup = queue_manager.build_queue_text_and_markup()
        await query.edit_message_text(q_text, reply_markup=markup, parse_mode="Markdown")

    elif data == "q_edit_toggle":
        # Toggle edit mode in queue
        q_text, markup = queue_manager.build_queue_text_and_markup(is_edit_mode=True)
        await query.edit_message_text(q_text, reply_markup=markup, parse_mode="Markdown")

    elif data.startswith("q_del:"):
        item_id = data.split(":", 1)[1]
        queue_manager.remove_item(item_id)
        q_text, markup = queue_manager.build_queue_text_and_markup(is_edit_mode=True)
        await query.edit_message_text(q_text, reply_markup=markup, parse_mode="Markdown")

    elif data.startswith("q_up:"):
        item_id = data.split(":", 1)[1]
        queue_manager.move_item(item_id, "up")
        q_text, markup = queue_manager.build_queue_text_and_markup(is_edit_mode=True)
        await query.edit_message_text(q_text, reply_markup=markup, parse_mode="Markdown")

    elif data.startswith("q_down:"):
        item_id = data.split(":", 1)[1]
        queue_manager.move_item(item_id, "down")
        q_text, markup = queue_manager.build_queue_text_and_markup(is_edit_mode=True)
        await query.edit_message_text(q_text, reply_markup=markup, parse_mode="Markdown")

    elif data.startswith("q_run_one:"):
        item_id = data.split(":", 1)[1]
        matched = [x for x in state.queue if x["id"] == item_id]
        if matched:
            item = matched[0]
            queue_manager.remove_item(item_id)
            status_msg = await query.message.reply_text(f"⚡ Turant chala raha hoon: `{item['title'][:40]}`")
            asyncio.create_task(
                runner.run_task(
                    prompt=item["title"],
                    project_dir=pdir,
                    bot=context.bot,
                    chat_id=query.message.chat_id,
                    live_message_id=status_msg.message_id,
                    conversation_id=state.active_conversation_id
                )
            )

    elif data == "open_queue":
        q_text, markup = queue_manager.build_queue_text_and_markup()
        await query.message.reply_text(q_text, reply_markup=markup, parse_mode="Markdown")

    elif data.startswith("switch_conv:"):
        conv_id = data.split(":", 1)[1]
        if state.switch_conversation(conv_id):
            conv = state.conversations.get(conv_id, {})
            await query.edit_message_text(
                f"✅ Switched to session: **{conv.get('title', 'Session')}** (`{conv_id[:8]}`)",
                parse_mode="Markdown"
            )

    elif data == "new_conv":
        new_id = state.new_conversation()
        conv = state.conversations[new_id]
        await query.edit_message_text(
            f"✨ Naya session ban gaya aur active ho gaya:\n**{conv['title']}** (`{new_id[:8]}`)",
            parse_mode="Markdown"
        )

    elif data.startswith("fb_dir:"):
        sub = data.split(":", 1)[1]
        header, markup = FileManager.build_file_browser(pdir, sub)
        await query.edit_message_text(header, reply_markup=markup, parse_mode="Markdown")

    elif data.startswith("fb_get:"):
        sub = data.split(":", 1)[1]
        target = pdir / sub
        if target.exists() and target.is_file():
            await query.message.reply_document(document=open(target, "rb"), caption=f"📄 {target.name}")

def create_bot_application() -> Application:
    app = ApplicationBuilder().token(config.BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("queue", queue_command))
    app.add_handler(CommandHandler("stop", stop_command))
    app.add_handler(CommandHandler("new", new_command))
    app.add_handler(CommandHandler("conversations", conversations_command))
    app.add_handler(CommandHandler("mode", mode_command))
    app.add_handler(CommandHandler("files", files_command))
    app.add_handler(CommandHandler("get", get_command))
    app.add_handler(CommandHandler("zip", zip_command))
    app.add_handler(CommandHandler("undo", undo_command))
    app.add_handler(CommandHandler("sync", sync_command))
    app.add_handler(CommandHandler("login", login_command))
    app.add_handler(CommandHandler("code", code_command))
    app.add_handler(CommandHandler("key", key_command))
    app.add_handler(CommandHandler("help", help_command))

    # Callback Queries
    app.add_handler(CallbackQueryHandler(callback_query_handler))

    # Messages & Files
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_document_or_photo))

    return app
