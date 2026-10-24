import logging
import os
import time
import shutil
import asyncio
import psutil
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, ReplyKeyboardMarkup, KeyboardButton
from telegram.constants import ChatAction
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
from chat_service import chat_service

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

def get_main_keyboard(mini_app_url: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("💬 Chat Mode (Txt)"), KeyboardButton("📋 Queue Mode (Tasks)")],
            [KeyboardButton("📱 Open Mini App", web_app=WebAppInfo(url=mini_app_url)), KeyboardButton("📊 System Status")]
        ],
        resize_keyboard=True
    )

# ----------------- Command Handlers -----------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user):
        return

    active_id = state.active_conversation_id
    active_conv = state.conversations.get(active_id, {})
    conv_title = active_conv.get("title", "Default Session")
    mini_app_url = get_mini_app_url()

    keyboard = [
        [
            InlineKeyboardButton("💬 Chat Mode (Txt)", callback_data="switch_mode:chat"),
            InlineKeyboardButton("📋 Queue Mode (Tasks)", callback_data="switch_mode:queue")
        ],
        [InlineKeyboardButton("📱 Open Mini App (Terminal & Chat)", web_app=WebAppInfo(url=mini_app_url))],
        [
            InlineKeyboardButton("📊 System Status", callback_data="open_status"),
            InlineKeyboardButton("📁 Files Browser", callback_data="open_files")
        ]
    ]

    restart_msg = "\n_🔔 Server restart ke baad wapas aa gaya, sab safe hai!_\n" if state.was_restarted else ""

    text = (
        f"👋 **Namaste! Cloud PC Remote Bot Active Hai!**\n{restart_msg}\n"
        "Main aapka personal remote AI assistant hoon jo Render cloud server par chalta hai.\n\n"
        f"⚙️ **System Config:**\n"
        f"• **Owner**: @{config.ALLOWED_USERNAME} (Verified Lock ✅)\n"
        f"• **Current Mode**: `{state.mode.upper()}`\n"
        f"• **Active Session**: {conv_title}\n"
        f"• **Auto-Allow**: ON (Bina permission maange fast execution)\n\n"
        "💡 **Kaise use karein:**\n"
        "1. 💬 **Chat Mode (Txt-to-Txt)**: Normal baatein karein, sawal puchiye, code explain karwayein. Turant direct text reply milega!\n"
        "2. 📋 **Queue Mode (Tasks)**: Coding aur repo ke kaam list mein jodiye aur [PROCEED] se run karein.\n"
        "3. 📱 **Mini App**: Terminal, Chat tab, Queue, aur Files browser phone par full screen kholein!\n\n"
        "👇 _Neeche keyboard ya buttons se mode switch karein ya seedha message type karein:_"
    )
    await update.message.reply_text(
        mask_secrets(text),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    # Also attach persistent reply keyboard
    await update.message.reply_text(
        "⚡ Quick Menu keyboard ready hai:",
        reply_markup=get_main_keyboard(mini_app_url)
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
    mini_app_url = get_mini_app_url()
    if args and args[0].lower() in ["chat", "queue", "instant"]:
        state.mode = args[0].lower()
        state.save()
        await update.message.reply_text(
            f"✅ Mode badal kar `{state.mode.upper()}` kar diya gaya hai.",
            reply_markup=get_main_keyboard(mini_app_url)
        )
    else:
        keyboard = [
            [
                InlineKeyboardButton("💬 Chat (Txt-to-Txt)", callback_data="switch_mode:chat"),
                InlineKeyboardButton("📋 Queue (Tasks)", callback_data="switch_mode:queue")
            ],
            [
                InlineKeyboardButton("⚡ Instant Mode", callback_data="switch_mode:instant")
            ]
        ]
        await update.message.reply_text(
            f"ℹ️ **Current Mode**: `{state.mode.upper()}`\n\nNeeche se apna pasandida mode select karein:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

async def chat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force direct conversational response regardless of active mode."""
    if not is_authorized(update.effective_user):
        return

    text = " ".join(context.args or []).strip()
    if not text:
        await update.message.reply_text("Usage: `/chat <aapka sawal/prompt>`\nYa direct baatein karne ke liye `💬 Chat Mode (Txt)` switch karein.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    pdir = state.get_project_dir()
    reply = await chat_service.generate_reply(prompt=text, conv_id=state.active_conversation_id, project_dir=pdir)
    masked = mask_secrets(reply)

    mini_app_url = get_mini_app_url()
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📋 Switch to Queue", callback_data="switch_mode:queue"),
            InlineKeyboardButton("📱 Mini App", web_app=WebAppInfo(url=mini_app_url))
        ]
    ])
    try:
        await update.message.reply_text(masked, reply_markup=kb, parse_mode="Markdown")
    except Exception:
        await update.message.reply_text(masked, reply_markup=kb)

async def task_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force add task item to queue regardless of active mode."""
    if not is_authorized(update.effective_user):
        return

    text = " ".join(context.args or []).strip()
    if not text:
        await update.message.reply_text("Usage: `/task <kaam ki description>`")
        return

    res = queue_manager.add_item(title=text, item_type="text")
    if res.get("is_next_round"):
        await update.message.reply_text(f"⏳ Kaam chal raha hai, item Agle Round mein jod diya gaya: \"{text[:40]}\"")
    else:
        q_text, markup = queue_manager.build_queue_text_and_markup()
        await update.message.reply_text(f"➕ Task queue mein jud gaya:\n\n{q_text}", reply_markup=markup, parse_mode="Markdown")

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
    mini_app_url = get_mini_app_url()

    # 1. Handle Persistent Keyboard Button Clicks
    if text in ["💬 Chat Mode (Txt)", "/mode chat"]:
        state.mode = "chat"
        state.save()
        await update.message.reply_text(
            "💬 **Chat Mode (Txt-to-Txt) Active!**\n\nAb aap jo bhi likhenge, AI seedha direct text reply karega (bina kisi queue ya git commit ke). Puchhiye koi bhi sawal!",
            reply_markup=get_main_keyboard(mini_app_url),
            parse_mode="Markdown"
        )
        return

    if text in ["📋 Queue Mode (Tasks)", "/mode queue"]:
        state.mode = "queue"
        state.save()
        q_text, markup = queue_manager.build_queue_text_and_markup()
        await update.message.reply_text(
            f"📋 **Queue Mode (Batch Tasks) Active!**\n\nAb aapke messages aur files kaam ki list mein judenge aur **[PROCEED]** dabane par run honge.\n\n{q_text}",
            reply_markup=markup,
            parse_mode="Markdown"
        )
        return

    if text in ["📊 System Status", "/status"]:
        await status_command(update, context)
        return

    if text in ["📱 Open Mini App", "📟 Open Mini App"]:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📱 Open Cloud PC Mini App", web_app=WebAppInfo(url=mini_app_url))]
        ])
        await update.message.reply_text("📱 Mini app kholne ke liye neeche button dabayein:", reply_markup=kb)
        return

    # 2. Branch by Active Mode
    if state.mode == "chat":
        # Direct Text-to-Text Conversation
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        reply = await chat_service.generate_reply(
            prompt=text,
            conv_id=state.active_conversation_id,
            project_dir=pdir
        )
        masked_reply = mask_secrets(reply)
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📋 Switch to Queue", callback_data="switch_mode:queue"),
                InlineKeyboardButton("📱 Mini App", web_app=WebAppInfo(url=mini_app_url))
            ]
        ])

        if len(masked_reply) > 4000:
            chunks = [masked_reply[i:i+4000] for i in range(0, len(masked_reply), 4000)]
            for i, chunk in enumerate(chunks):
                is_last = (i == len(chunks) - 1)
                cur_kb = kb if is_last else None
                try:
                    await update.message.reply_text(chunk, reply_markup=cur_kb, parse_mode="Markdown")
                except Exception:
                    await update.message.reply_text(chunk, reply_markup=cur_kb)
        else:
            try:
                await update.message.reply_text(masked_reply, reply_markup=kb, parse_mode="Markdown")
            except Exception:
                await update.message.reply_text(masked_reply, reply_markup=kb)

    elif state.mode == "instant":
        # Run immediately on workspace with agy
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
        # Default: Add to task queue
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

    elif data.startswith("switch_mode:"):
        new_mode = data.split(":", 1)[1]
        if new_mode in ["chat", "queue", "instant"]:
            state.mode = new_mode
            state.save()
            mini_app_url = get_mini_app_url()
            mode_names = {
                "chat": "💬 **Chat Mode (Txt-to-Txt)**\n\nDirect conversational replies active hain. Kuch bhi sawal ya instruction likhein!",
                "queue": "📋 **Queue Mode (Batch Tasks)**\n\nKaam ki list banegi, aur **[PROCEED]** se execute hogi.",
                "instant": "⚡ **Instant Mode**\n\nHar command seedha workspace par execute hoga."
            }
            await query.answer(f"Switched to {new_mode.upper()} mode!")
            await query.message.reply_text(
                f"✅ {mode_names.get(new_mode, new_mode)}",
                reply_markup=get_main_keyboard(mini_app_url),
                parse_mode="Markdown"
            )

    elif data == "open_status":
        used_mb, total_mb, cpu = get_memory_info()
        uptime_str = get_uptime_string()
        text = (
            "📊 **Cloud PC Status**\n\n"
            f"• **RAM Usage**: {used_mb} MB / {total_mb} MB\n"
            f"• **CPU**: {cpu}%\n"
            f"• **Uptime**: {uptime_str}\n"
            f"• **Queue Items**: {len(state.queue)} bache hain\n"
            f"• **Mode**: `{state.mode.upper()}`\n"
        )
        await query.message.reply_text(text, parse_mode="Markdown")

    elif data == "open_files":
        header, markup = FileManager.build_file_browser(pdir)
        await query.message.reply_text(header, reply_markup=markup, parse_mode="Markdown")

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
    app.add_handler(CommandHandler("chat", chat_command))
    app.add_handler(CommandHandler("task", task_command))
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
