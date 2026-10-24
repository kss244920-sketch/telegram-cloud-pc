import logging
import os
import time
import shutil
import asyncio
import psutil
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
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
from file_manager import FileManager
from git_manager import GitManager
from storage_manager import storage_manager

logger = logging.getLogger("telegram_bot")
START_TIME = time.time()
active_login_process = None

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

async def check_auth_or_reject(update: Update) -> bool:
    user = update.effective_user
    if is_authorized(user):
        return True

    uid = user.id if user else "Unknown"
    uname = f"@{user.username}" if user and user.username else "No username"
    text = (
        f"⛔ **Access Denied**\n\n"
        f"Ye bot private hai:\n• Aapka ID: `{uid}`\n• Username: {uname}\n\n"
        f"Authorized Owner: `@{config.ALLOWED_USERNAME}`"
    )
    try:
        if update.message:
            await update.message.reply_text(text, parse_mode="Markdown")
        elif update.callback_query:
            await update.callback_query.answer(f"Access Denied: ID={uid}", show_alert=True)
    except Exception:
        pass
    return False

def render_buffer_text_and_markup() -> tuple[str | None, InlineKeyboardMarkup | None]:
    if not state.prompt_buffer:
        return None, None

    count = len(state.prompt_buffer)
    lines = [f"📥 **Input Buffer ({count} item{'s' if count > 1 else ''}):**"]
    for i, item in enumerate(state.prompt_buffer, 1):
        if item.get("type") == "file":
            lines.append(f"{i}. 📄 `{item['file_name']}` ({item.get('size_kb', 0)} KB)")
        else:
            lines.append(f"{i}. \"{item['text'][:80]}\"")

    lines.append("\n_Aur messages/files bhejein, ya neeche Proceed karein:_")
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ PROCEED", callback_data="run_proceed")]
    ])
    return "\n".join(lines), markup

# ----------------- Command Handlers -----------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth_or_reject(update):
        return

    active_id = state.active_conversation_id
    text = (
        "💻 **Cloud PC Terminal Active!**\n\n"
        f"• **Owner**: @{config.ALLOWED_USERNAME} (Verified Lock ✅)\n"
        f"• **Active Session**: `{active_id[:8]}`\n"
        f"• **Storage Node**: `{config.STORAGE_CHANNEL_ID}` (Connected ✅)\n\n"
        "👉 **Kaise use karein:**\n"
        "1. Chat mein koi bhi prompt, instruction ya files bhein.\n"
        "2. Multiple baatein bhejne ke baad neeche **[▶️ PROCEED]** dabayein.\n"
        "3. Live 10-events rolling terminal screen chalegi aur final answer aayega.\n\n"
        "Commands: `/session`, `/new`, `/stop`, `/login`, `/key`, `/get <file>`, `/backup`"
    )
    # Remove any leftover reply keyboard
    await update.message.reply_text(
        mask_secrets(text),
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )

async def session_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth_or_reject(update):
        return

    used_mb, total_mb, cpu = get_memory_info()
    sess_disp = state.active_conversation_id if state.active_conversation_id else "Fresh (Ready for task)"
    text = (
        "💻 **Terminal Session Info**\n\n"
        f"• **Session ID**: `{sess_disp}`\n"
        f"• **Project**: `{state.current_project}`\n"
        f"• **Storage Channel**: `{config.STORAGE_CHANNEL_ID}` (Active)\n"
        f"• **RAM**: {used_mb}MB / {total_mb}MB | CPU: {cpu}%\n"
        f"• **Uptime**: {get_uptime_string()}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def new_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth_or_reject(update):
        return

    state.new_conversation()
    await update.message.reply_text("✨ Naya terminal session start hua! Ab koi bhi prompt ya command bhejein.", parse_mode="Markdown")

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth_or_reject(update):
        return

    if runner.is_running:
        runner.stop_current_task()
        await update.message.reply_text("🛑 Current process ko terminate kiya ja raha hai...")
    else:
        state.clear_buffer()
        await update.message.reply_text("ℹ️ Koi process active nahi hai. Input buffer reset ho gaya.")

async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth_or_reject(update):
        return

    msg = await update.message.reply_text("⏳ Workspace snapshot Telegram storage channel par upload ho raha hai...")
    pdir = state.get_project_dir()
    ok = await storage_manager.backup_workspace_snapshot(context.bot, pdir, tag="manual")
    if ok:
        await msg.edit_text(f"✅ Workspace snapshot Channel `{config.STORAGE_CHANNEL_ID}` par upload ho gaya!")
    else:
        await msg.edit_text("❌ Snapshot backup fail ho gaya.")

async def get_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth_or_reject(update):
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
        matches = list(pdir.rglob(fname))
        if matches and matches[0].is_file():
            await update.message.reply_document(document=open(matches[0], "rb"), caption=f"📄 {matches[0].name}")
        else:
            await update.message.reply_text(f"❌ File `{fname}` nahi mili.")

# ----------------- Auth Commands -----------------

def save_auth_bundle_to_render() -> bool:
    try:
        import tarfile, io, base64, urllib.request, json
        cred_dir = Path.home() / ".gemini"
        if not cred_dir.exists():
            return False

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for item in cred_dir.iterdir():
                # Don't pack huge brain conversation history or cache
                if item.name not in ["brain", "cache", "log", "tmp"]:
                    tar.add(str(item), arcname=item.name)
        buf.seek(0)
        b64_str = base64.b64encode(buf.read()).decode("utf-8")

        render_api_key = os.getenv("RENDER_API_KEY", "rnd_3tagyUF2IqkgZyiRiBHD7CbZjxAg")
        render_service_id = os.getenv("RENDER_SERVICE_ID", "srv-dbe4n88m7kps73capeog")

        url = f"https://api.render.com/v1/services/{render_service_id}/env-vars/AGY_AUTH_DATA"
        req = urllib.request.Request(
            url,
            data=json.dumps({"value": b64_str}).encode("utf-8"),
            headers={"Authorization": f"Bearer {render_api_key}", "Content-Type": "application/json"},
            method="PUT"
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status in (200, 201)
        except Exception:
            post_url = f"https://api.render.com/v1/services/{render_service_id}/env-vars"
            req2 = urllib.request.Request(
                post_url,
                data=json.dumps([{"key": "AGY_AUTH_DATA", "value": b64_str}]).encode("utf-8"),
                headers={"Authorization": f"Bearer {render_api_key}", "Content-Type": "application/json"},
                method="PUT"
            )
            with urllib.request.urlopen(req2) as resp2:
                return resp2.status in (200, 201)
    except Exception as e:
        logger.error(f"Failed to save auth bundle to Render: {e}")
        return False

async def login_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active_login_process
    if not await check_auth_or_reject(update):
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
                "🔐 **Google Sign-In Link (Click karein):**\n\n"
                f"[👉 Yahan Click Karke Login Karein]({auth_url})\n\n"
                "Browser mein sign-in ke baad jo **Authorization Code** mile, use bhejein:\n"
                "`/code <aapka_code>`"
            )
            await msg.edit_text(text, parse_mode="Markdown")
        else:
            await msg.edit_text("ℹ️ Agy pehle se authenticated hai ya direct API key mode active hai.")
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")

async def code_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active_login_process
    if not await check_auth_or_reject(update):
        return

    args = context.args or []
    if not args or not active_login_process:
        await update.message.reply_text("Usage: `/code <authorization_code>`\n\n(Pehle `/login` karein)")
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
        save_note = "\n\n✅ Credentials Render environment mein permanently save ho gaye!" if saved else ""
        await status_msg.edit_text(f"✅ Login successfully complete ho gaya!{save_note}")
        active_login_process = None
    except Exception as e:
        await status_msg.edit_text(f"❌ Verification failed: {e}")

async def key_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth_or_reject(update):
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
    if not await check_auth_or_reject(update):
        return

    text = (
        "📖 **Cloud PC Commands:**\n\n"
        "• `/start` : Bot status aur instructions\n"
        "• `/session` : Current session info\n"
        "• `/new` : Fresh conversation session start karein\n"
        "• `/stop` : Chal rahe process ko rokein\n"
        "• `/get <file>` : Workspace ki file chat mein maangein\n"
        "• `/backup` : Workspace snapshot storage channel par upload karein\n"
        "• `/login` & `/code` : Google OAuth login\n"
        "• `/key <API_KEY>` : Gemini API Key set karein\n\n"
        "💡 **Chat Execution:** Chat mein messages/files bhein, aur **[▶️ PROCEED]** dabayein!"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

# ----------------- Message & File Handlers -----------------

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth_or_reject(update):
        return

    text = update.message.text.strip()
    if not text:
        return

    if runner.is_running:
        await update.message.reply_text("⏳ Ek process terminal mein chal raha hai. /stop se rok sakte hain.")
        return

    state.add_to_buffer(text, item_type="text")
    buf_text, markup = render_buffer_text_and_markup()

    if state.buffer_message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=state.buffer_message_id,
                text=buf_text,
                reply_markup=markup,
                parse_mode="Markdown"
            )
            return
        except Exception:
            pass

    msg = await update.message.reply_text(buf_text, reply_markup=markup, parse_mode="Markdown")
    state.buffer_message_id = msg.message_id

async def handle_document_or_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth_or_reject(update):
        return

    if runner.is_running:
        await update.message.reply_text("⏳ Ek process terminal mein chal raha hai. Pehle use khatam hone dein ya /stop karein.")
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

    # Immediately backup the uploaded file to the Telegram Storage Channel
    saved_path = pdir / saved_info["rel_path"]
    asyncio.create_task(
        storage_manager.backup_file(
            bot=context.bot,
            file_path=saved_path,
            caption=f"Upload caption: {caption}" if caption else ""
        )
    )

    item_title = f"{saved_info['name']} (file)" + (f": {caption}" if caption else "")
    state.add_to_buffer(
        text=f"File inbox/{saved_info['name']}" + (f" (caption: {caption})" if caption else ""),
        item_type="file",
        file_name=saved_info["name"],
        size_kb=saved_info["size_kb"]
    )

    buf_text, markup = render_buffer_text_and_markup()

    if state.buffer_message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=state.buffer_message_id,
                text=buf_text,
                reply_markup=markup,
                parse_mode="Markdown"
            )
            return
        except Exception:
            pass

    msg = await update.message.reply_text(buf_text, reply_markup=markup, parse_mode="Markdown")
    state.buffer_message_id = msg.message_id

# ----------------- Execution Pipeline -----------------

async def execute_terminal_task(
    prompt: str,
    bot,
    chat_id: int,
    live_message_id: int,
    conversation_id: str,
    project_dir: Path
):
    try:
        res = await runner.run_task(
            prompt=prompt,
            project_dir=project_dir,
            bot=bot,
            chat_id=chat_id,
            live_message_id=live_message_id,
            conversation_id=conversation_id
        )

        response_text = res.get("response", "").strip()
        if not response_text:
            response_text = f"✅ Output: Process finished with status `{res.get('status', 'complete')}` ({res.get('time', '')})"

        masked = mask_secrets(response_text)
        # Split into chunks of <= 4000 chars for Telegram
        chunks = [masked[i:i+4000] for i in range(0, len(masked), 4000)]
        for chunk in chunks:
            try:
                await bot.send_message(chat_id=chat_id, text=chunk, parse_mode="Markdown")
            except Exception:
                await bot.send_message(chat_id=chat_id, text=chunk)

    except Exception as e:
        logger.error(f"Error in execute_terminal_task: {e}")
        try:
            await bot.send_message(chat_id=chat_id, text=f"❌ Execution error: {e}")
        except Exception:
            pass
    finally:
        state.buffer_message_id = None

# ----------------- Callback Queries -----------------

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await check_auth_or_reject(update):
        return

    await query.answer()
    data = query.data or ""
    pdir = state.get_project_dir()

    if data == "run_proceed":
        if runner.is_running:
            await query.answer("Kaam pehle se chal raha hai!", show_alert=True)
            return

        if not state.prompt_buffer:
            await query.answer("Buffer khali hai!", show_alert=True)
            return

        # Combine buffered items into prompt
        if len(state.prompt_buffer) == 1:
            combined_prompt = state.prompt_buffer[0]["text"]
        else:
            prompt_lines = []
            for i, item in enumerate(state.prompt_buffer, 1):
                prompt_lines.append(f"{i}. {item['text']}")
            combined_prompt = "\n".join(prompt_lines)

        state.clear_buffer()
        conv_id = state.active_conversation_id
        cid_display = conv_id[:8] if conv_id else "session"

        header = f"💻 agy terminal [{cid_display}]\n" + "─" * 30
        clean_prompt_preview = combined_prompt.split('\n')[0][:40]
        start_text = f"```\n{header}\n🚀 Task shuru hua: {clean_prompt_preview}\n```"
        try:
            await query.edit_message_text(start_text, parse_mode="Markdown")
        except Exception:
            try:
                await query.edit_message_text(f"{header}\n🚀 Task shuru hua: {clean_prompt_preview}")
            except Exception:
                pass

        chat_id = update.effective_chat.id
        live_msg_id = query.message.message_id
        asyncio.create_task(
            execute_terminal_task(
                prompt=combined_prompt,
                bot=context.bot,
                chat_id=chat_id,
                live_message_id=live_msg_id,
                conversation_id=conv_id,
                project_dir=pdir
            )
        )

# ----------------- Bot Builder -----------------

def create_bot_application() -> Application:
    app = ApplicationBuilder().token(config.BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("session", session_command))
    app.add_handler(CommandHandler("new", new_command))
    app.add_handler(CommandHandler("stop", stop_command))
    app.add_handler(CommandHandler("backup", backup_command))
    app.add_handler(CommandHandler("get", get_command))
    app.add_handler(CommandHandler("login", login_command))
    app.add_handler(CommandHandler("code", code_command))
    app.add_handler(CommandHandler("key", key_command))
    app.add_handler(CommandHandler("help", help_command))

    # Callback Queries (Single Proceed button)
    app.add_handler(CallbackQueryHandler(callback_query_handler))

    # Messages & Files
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_document_or_photo))

    return app
