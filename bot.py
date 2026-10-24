import logging
import os
import re
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
        if update.effective_chat:
            state.last_chat_id = update.effective_chat.id
            state.save()
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
            fname = item.get("file_name", "file")
            ext = Path(fname).suffix.lower()
            if ext in [".mp3", ".ogg", ".wav", ".m4a"]:
                icon = "🎵"
            elif ext in [".mp4", ".mov", ".mkv", ".webm"]:
                icon = "🎬"
            elif ext == ".apk":
                icon = "📱"
            elif ext in [".jpg", ".png", ".jpeg", ".webp"]:
                icon = "🖼️"
            elif ext in [".zip", ".tar", ".gz"]:
                icon = "📦"
            else:
                icon = "📄"
            lines.append(f"{i}. {icon} `{fname}` ({item.get('size_kb', 0)} KB)")
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

    cwd = state.get_current_cwd()
    text = (
        "💻 **Cloud PC Real-Time Terminal Active!**\n\n"
        f"• **Owner**: @{config.ALLOWED_USERNAME} (Verified Lock ✅)\n"
        f"• **Working Directory**: `{cwd.name}`\n"
        f"• **Storage Node**: `{config.STORAGE_CHANNEL_ID}` (Connected ✅)\n\n"
        "👉 **Direct Terminal Usage:**\n"
        "Seedha chat mein koi bhi shell command (`ls -la`, `pwd`, `python ...`, `pip ...`) ya AI prompt type karein.\n"
        "Ye message turant real-time live terminal window ban kar stream hoga!\n\n"
        "Commands: `/session`, `/new`, `/stop`, `/storage`, `/login`, `/logout`, `/key`, `/get <file>`, `/backup`"
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
            # Check Telegram Storage Channel manifest and restore if available!
            restored = await storage_manager.ensure_file_present(context.bot, fname, pdir / "inbox")
            if restored and restored.exists():
                await update.message.reply_document(
                    document=open(restored, "rb"),
                    caption=f"📄 {restored.name} (Restored from Telegram Cloud Storage)"
                )
            else:
                await update.message.reply_text(f"❌ File `{fname}` workspace ya cloud storage channel mein nahi mili.")

async def storage_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth_or_reject(update):
        return

    manifest = storage_manager.load_manifest()
    if not manifest:
        await update.message.reply_text(
            f"☁️ **Telegram Cloud Storage Channel:** `{config.STORAGE_CHANNEL_ID}`\n\nAbhi koi file stored nahi hai.",
            parse_mode="Markdown"
        )
        return

    lines = [
        f"☁️ **Telegram Storage Files (Channel `{config.STORAGE_CHANNEL_ID}`):**\n"
    ]
    for item in manifest[-20:]:
        fn = item.get("file_name", "file")
        sz = item.get("size_kb", 0)
        dt = item.get("uploaded_at", "")[:16].replace("T", " ")
        lines.append(f"• `{fn}` ({sz} KB) - _{dt}_")

    lines.append(f"\nTotal: {len(manifest)} files permanently stored.")
    lines.append("Chat me paane ke liye: `/get <file_name>`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

# ----------------- Auth Commands -----------------

def save_auth_bundle_to_render() -> bool:
    try:
        import tarfile, io, base64, urllib.request, json
        cred_dir = Path.home() / ".gemini"
        if not cred_dir.exists():
            cred_dir = Path("/root/.gemini")
        if not cred_dir.exists():
            return False

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for item in cred_dir.iterdir():
                if item.name not in ["brain", "cache", "log", "tmp"]:
                    tar.add(str(item), arcname=item.name)

            # Ensure the crucial antigravity-oauth-token is included!
            cli_dir = cred_dir / "antigravity-cli"
            if cli_dir.exists():
                for fname in ["antigravity-oauth-token", "installation_id", "settings.json", "jetski_state.pbtxt"]:
                    fpath = cli_dir / fname
                    if fpath.exists():
                        tar.add(str(fpath), arcname=f"antigravity-cli/{fname}")
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

async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active_login_process
    if not await check_auth_or_reject(update):
        return

    msg = await update.message.reply_text("⏳ AI session logout aur credentials reset ho rahe hain...")

    if active_login_process:
        try:
            active_login_process.terminate()
        except Exception:
            pass
        active_login_process = None

    target_dirs = [
        Path.home() / ".gemini",
        Path("/root/.gemini")
    ]
    files_to_remove = [
        "oauth_creds.json",
        "google_accounts.json",
        "antigravity-cli/antigravity-oauth-token",
        "antigravity-cli/jetski_state.pbtxt"
    ]
    for d in target_dirs:
        for f in files_to_remove:
            target = d / f
            try:
                if target.exists():
                    target.unlink()
            except Exception as e:
                logger.warning(f"Failed to remove {target}: {e}")

    config.GEMINI_API_KEY = ""
    os.environ.pop("GEMINI_API_KEY", None)

    # Clear AGY_AUTH_DATA on Render
    try:
        import urllib.request
        render_api_key = os.getenv("RENDER_API_KEY", "rnd_3tagyUF2IqkgZyiRiBHD7CbZjxAg")
        render_service_id = os.getenv("RENDER_SERVICE_ID", "srv-dbe4n88m7kps73capeog")
        url = f"https://api.render.com/v1/services/{render_service_id}/env-vars/AGY_AUTH_DATA"
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {render_api_key}"},
            method="DELETE"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            pass
    except Exception as e:
        logger.warning(f"Could not clear Render AGY_AUTH_DATA: {e}")

    state.new_conversation()

    text = (
        "🚪 **Successfully Logged Out!**\n\n"
        "Purana Google account / session clear ho gaya hai.\n\n"
        "👉 **Naya account connect karne ke liye:**\n"
        "1. Chat mein `/login` bhejein\n"
        "2. Naye Gmail account se sign-in karke code lein\n"
        "3. `/code <authorization_code>` bhejein\n\n"
        "Ya Gemini API key set karein: `/key <API_KEY>`"
    )
    await msg.edit_text(text, parse_mode="Markdown")

async def login_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active_login_process
    if not await check_auth_or_reject(update):
        return

    args = [x.lower() for x in (context.args or [])]
    force_new = "force" in args or "new" in args

    # If force, perform logout cleanup first
    if force_new:
        target_dirs = [Path.home() / ".gemini", Path("/root/.gemini")]
        for d in target_dirs:
            for f in ["oauth_creds.json", "google_accounts.json", "antigravity-cli/antigravity-oauth-token"]:
                t = d / f
                if t.exists():
                    try: t.unlink()
                    except Exception: pass

    msg = await update.message.reply_text("⏳ Google OAuth Sign-in link generate ho rahi hai...")

    agy_bin = shutil.which("agy") or "/usr/local/bin/agy"
    env = os.environ.copy()
    env["PAGER"] = "cat"
    env["SYSTEMD_PAGER"] = "cat"
    env["COLUMNS"] = "5000"
    env["LINES"] = "100"
    env["TERM"] = "dumb"

    try:
        active_login_process = await asyncio.create_subprocess_exec(
            agy_bin,
            "-p", "hello",
            "--output-format", "stream-json",
            "--dangerously-skip-permissions",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env
        )

        auth_url = ""
        accumulated = ""
        start_time = time.time()
        while time.time() - start_time < 25:
            try:
                chunk = await asyncio.wait_for(active_login_process.stdout.read(512), timeout=2.0)
                if not chunk:
                    break
                line = chunk.decode("utf-8", errors="ignore")
                accumulated += line
            except asyncio.TimeoutError:
                pass

            match = re.search(r'https://accounts\.google\.com/o/oauth2/auth[^\s"\'\<\>\)]+', accumulated)
            if match:
                auth_url = match.group(0).strip().rstrip(".,;)")
                break

            if active_login_process.returncode is not None:
                break

        if auth_url:
            login_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("👉 Click Here to Sign In (Google)", url=auth_url)]
            ])
            text = (
                "🔐 **Google Authentication Required!**\n\n"
                "1. Neeche diye button par click karein ya URL browser mein kholein.\n"
                "2. Google account se sign-in karke **Authorization Code** copy karein.\n"
                "3. Chat mein bhejein:\n"
                "`/code <aapka_code>`\n\n"
                "🔗 **Direct Full Link (Tap to Copy):**\n"
                f"`{auth_url}`"
            )
            await msg.edit_text(
                text,
                reply_markup=login_markup,
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
        else:
            await msg.edit_text(
                "ℹ️ Agy pehle se authenticated hai ya direct API key mode active hai.\n\n"
                "👉 Kisi naye Google account se switch karne ke liye:\n"
                "• `/logout` karein, phir `/login` karein\n"
                "• Ya seedha `/login force` karein!"
            )
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
        try:
            await asyncio.wait_for(active_login_process.wait(), timeout=6.0)
        except Exception:
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
        "• `/storage` : Telegram storage channel ki saved files list\n"
        "• `/get <file>` : Workspace ki file chat mein maangein\n"
        "• `/backup` : Workspace snapshot storage channel par upload karein\n"
        "• `/login` & `/code` : Google OAuth login\n"
        "• `/logout` : Current Google account logout karein (switch account)\n"
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
        cancel_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛑 Stop Running Task", callback_data="stop_current_task")]
        ])
        await update.message.reply_text(
            "⏳ Ek process terminal mein pehle se chal raha hai. /stop karein ya neeche button dabayein.",
            reply_markup=cancel_markup
        )
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
        await update.message.reply_text("⏳ Ek process chal raha hai. Pehle use khatam hone dein ya /stop karein.")
        return

    message = update.message
    caption = (message.caption or "").strip()
    pdir = state.get_project_dir()

    tg_file = None
    original_name = "file"

    if message.document:
        tg_file = await message.document.get_file()
        original_name = message.document.file_name or "document"
    elif message.video:
        tg_file = await message.video.get_file()
        original_name = message.video.file_name or f"video_{int(time.time())}.mp4"
    elif message.audio:
        tg_file = await message.audio.get_file()
        original_name = message.audio.file_name or f"audio_{int(time.time())}.mp3"
    elif message.voice:
        tg_file = await message.voice.get_file()
        original_name = f"voice_{int(time.time())}.ogg"
    elif message.animation:
        tg_file = await message.animation.get_file()
        original_name = message.animation.file_name or f"animation_{int(time.time())}.mp4"
    elif message.video_note:
        tg_file = await message.video_note.get_file()
        original_name = f"video_note_{int(time.time())}.mp4"
    elif message.photo:
        tg_file = await message.photo[-1].get_file()
        original_name = f"photo_{int(time.time())}.jpg"
    elif message.sticker:
        tg_file = await message.sticker.get_file()
        ext = ".webm" if message.sticker.is_video else (".webp" if not message.sticker.is_animated else ".tgs")
        original_name = f"sticker_{int(time.time())}{ext}"

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
            caption=f"Upload: {original_name}" + (f" ({caption})" if caption else ""),
            project_dir=pdir
        )
    )

    extra_note = ""
    if saved_info.get("is_zip") and saved_info.get("unzip_info"):
        extra_note = f" ({saved_info['unzip_info']} at inbox/extracted_{saved_path.stem})"

    state.add_to_buffer(
        text=f"File inbox/{saved_info['name']}" + (f" (caption: {caption})" if caption else "") + extra_note,
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
        # Only send response if not empty (for bash tasks, output is already in terminal block)
        if response_text:
            masked = mask_secrets(response_text)
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

# ----------------- Callback Queries -----------------

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await check_auth_or_reject(update):
        return

    await query.answer()
    data = query.data or ""

    if data == "stop_current_task":
        if runner.is_running:
            runner.stop_current_task()
            await query.answer("🛑 Process roka ja raha hai...", show_alert=True)
        else:
            await query.answer("Koi process running nahi hai.", show_alert=False)
        return

    if data == "run_proceed":
        if runner.is_running:
            await query.answer("Kaam pehle se chal raha hai!", show_alert=True)
            return

        if not state.prompt_buffer:
            await query.answer("Buffer khali hai!", show_alert=True)
            return

        combined_prompt = state.prompt_buffer[0]["text"] if len(state.prompt_buffer) == 1 else "\n".join(x["text"] for x in state.prompt_buffer)
        state.clear_buffer()
        conv_id = state.active_conversation_id
        cwd = state.get_current_cwd()
        dir_name = cwd.name or "workspace"

        clean_preview = combined_prompt.split('\n')[0][:35]
        header = f"┌── 💻 Terminal [{dir_name}] "
        header += "─" * max(1, 38 - len(header)) + "┐"
        footer = "└── [⏳ Starting...] " + "─" * 16 + "┘"
        initial_box = f"```text\n{header}\n🚀 Task: {clean_preview}\n{footer}\n```"

        cancel_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛑 Cancel", callback_data="stop_current_task")]
        ])

        try:
            await query.edit_message_text(
                initial_box,
                reply_markup=cancel_markup,
                parse_mode="Markdown"
            )
        except Exception:
            pass

        live_msg_id = query.message.message_id
        asyncio.create_task(
            execute_terminal_task(
                prompt=combined_prompt,
                bot=context.bot,
                chat_id=update.effective_chat.id,
                live_message_id=live_msg_id,
                conversation_id=conv_id,
                project_dir=cwd
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
    app.add_handler(CommandHandler("storage", storage_command))
    app.add_handler(CommandHandler("backup", backup_command))
    app.add_handler(CommandHandler("get", get_command))
    app.add_handler(CommandHandler("login", login_command))
    app.add_handler(CommandHandler("logout", logout_command))
    app.add_handler(CommandHandler("code", code_command))
    app.add_handler(CommandHandler("key", key_command))
    app.add_handler(CommandHandler("help", help_command))

    # Callback Queries (Single Proceed button)
    app.add_handler(CallbackQueryHandler(callback_query_handler))

    # Messages & Files (Every File & Media: Documents, Videos, Audios, Voice, Animations, Photos, Stickers, Attachments)
    media_filters = (
        filters.ATTACHMENT
        | filters.Document.ALL
        | filters.PHOTO
        | filters.VIDEO
        | filters.AUDIO
        | filters.VOICE
        | filters.ANIMATION
        | filters.VIDEO_NOTE
        | filters.Sticker.ALL
    )
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    app.add_handler(MessageHandler(media_filters, handle_document_or_photo))

    return app
