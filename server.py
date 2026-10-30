import os
import json
import asyncio
import logging
import subprocess
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

import config
from bot import create_bot_application, get_uptime_string, get_memory_info
from state import state

import collections

# Ring buffer for recent logs
LOG_BUFFER = collections.deque(maxlen=100)

class BufferLogHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            LOG_BUFFER.append(f"{record.asctime if hasattr(record, 'asctime') else ''} [{record.levelname}] {record.name}: {msg}")
        except Exception:
            pass

buffer_handler = BufferLogHandler()
buffer_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
logging.getLogger().addHandler(buffer_handler)
logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger("server")

bot_app = create_bot_application()

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Telegram Cloud PC terminal bot service...")
    # Start Cloud Backup, Pin & Disaster Recovery Daemon
    try:
        from audit_cloud_manager import start_backup_daemon
        start_backup_daemon(interval_seconds=3 * 3600)
        logger.info("☁️ Telegram Cloud PC Auto Cloud Backup & Pin Daemon active! ✅")
    except Exception as e:
        logger.warning(f"Error starting backup daemon: {e}")
    await bot_app.initialize()
    await bot_app.start()

    # Direct resilient polling loop that seamlessly survives Render rolling deploys
    is_shutting_down = False
    polling_task = None

    async def custom_polling_loop():
        offset = 0
        bot = bot_app.bot
        logger.info("Direct resilient Telegram polling loop started!")
        while not is_shutting_down:
            try:
                updates = await bot.get_updates(
                    offset=offset,
                    timeout=8,
                    allowed_updates=["message", "callback_query"]
                )
                for update in updates:
                    offset = update.update_id + 1
                    u_user = update.effective_user
                    if update.message:
                        u_text = update.message.text or update.message.caption or "(file or media)"
                    else:
                        u_text = "(callback or other)"
                    logger.info(f"Incoming Update #{update.update_id} from ID={u_user.id if u_user else 0} (@{u_user.username if u_user else 'None'}): {u_text[:40]}")
                    asyncio.create_task(bot_app.process_update(update))
            except Exception as e:
                err_str = str(e)
                if "Conflict" in err_str:
                    logger.warning("Telegram Conflict: Previous container shutting down. Retrying in 3s...")
                    await asyncio.sleep(3)
                elif "TimedOut" in err_str or "NetworkError" in err_str:
                    await asyncio.sleep(0.5)
                else:
                    logger.error(f"Polling error: {e}. Retrying in 2s...")
                    await asyncio.sleep(2)

    polling_task = asyncio.create_task(custom_polling_loop())

    # 4-minute self-ping keepalive task to reset Render 15-min inactivity timer
    pinger_task = None
    async def internal_pinger():
        external_url = os.getenv("RENDER_EXTERNAL_URL", "https://telegram-cloud-pc-docker.onrender.com").rstrip("/")
        ping_endpoint = f"{external_url}/health"
        logger.info(f"Self-ping keepalive task started targeting {ping_endpoint}")
        import aiohttp
        async with aiohttp.ClientSession() as session:
            while not is_shutting_down:
                await asyncio.sleep(240)  # 4 minutes
                try:
                    async with session.get(ping_endpoint, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                        logger.info(f"Self-ping keepalive: HTTP {resp.status} (Inactivity timer reset)")
                except Exception as e:
                    logger.warning(f"Self-ping notice: {e}")

    pinger_task = asyncio.create_task(internal_pinger())

    yield

    logger.info("Shutting down Telegram Cloud PC service...")
    is_shutting_down = True
    if polling_task:
        polling_task.cancel()
    if pinger_task:
        pinger_task.cancel()
    await bot_app.stop()
    await bot_app.shutdown()
    logger.info("Shutdown complete.")

app = FastAPI(title="Telegram Cloud PC Terminal", lifespan=lifespan)

@app.get("/", response_class=HTMLResponse)
async def home():
    return HTMLResponse(
        "<html><body style='font-family:sans-serif; background:#1a1b26; color:#c0caf5; padding:20px;'>"
        "<h2>⚡ Telegram Cloud PC Terminal Service is Online</h2>"
        "<p>Pure Text-to-Text Terminal active on Telegram.</p>"
        f"<p>Storage Channel: <code>{config.STORAGE_CHANNEL_ID}</code></p>"
        "</body></html>"
    )

@app.get("/is_busy")
async def is_busy():
    return JSONResponse(content={"busy": False, "process": "Idle"}, status_code=200)

@app.get("/backup_and_pin")
async def backup_and_pin():
    try:
        from audit_cloud_manager import dispatch_and_pin_backup
        res = dispatch_and_pin_backup(trigger_reason="3-Hour Scheduled Restart Checkpoint")
        return JSONResponse(content=res, status_code=200)
    except Exception as e:
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)

@app.get("/health")
async def health():
    return JSONResponse(
        content={
            "status": "ok",
            "service": "telegram-cloud-pc",
            "uptime": get_uptime_string(),
            "storage_channel": config.STORAGE_CHANNEL_ID
        },
        status_code=200
    )

@app.get("/api/debug-logs")
async def debug_logs_api():
    return {"logs": list(LOG_BUFFER)}

@app.get("/api/check-agy")
async def check_agy_endpoint():
    agy_bin = shutil.which("agy") or "/usr/local/bin/agy"
    env = os.environ.copy()
    env["PAGER"] = "cat"
    files = {}
    for p in ["/root/.gemini", "/root/.gemini/antigravity-cli", str(Path.home() / ".gemini")]:
        if os.path.exists(p):
            files[p] = os.listdir(p)
    
    try:
        proc = subprocess.run([agy_bin, "--model", config.DEFAULT_AGY_MODEL, "-p", "hi", "--output-format", "stream-json", "--dangerously-skip-permissions"],
                              capture_output=True, text=True, timeout=10, env=env)
        return {
            "returncode": proc.returncode,
            "stdout": proc.stdout[:1000],
            "stderr": proc.stderr[:1000],
            "files": files,
        }
    except subprocess.TimeoutExpired as e:
        return {
            "timeout": True,
            "stdout": (e.stdout or "")[:1500] if hasattr(e, "stdout") else "",
            "stderr": (e.stderr or "")[:1500] if hasattr(e, "stderr") else "",
            "files": files,
        }
    except Exception as e:
        return {"error": str(e), "files": files}

@app.get("/status")
async def status_api():
    used_mb, total_mb, cpu = get_memory_info()
    agy_path = shutil.which("agy") or ("/usr/local/bin/agy" if os.path.exists("/usr/local/bin/agy") else None)
    agy_version = "not_found"
    if agy_path:
        try:
            res = subprocess.run([agy_path, "--version"], capture_output=True, text=True, timeout=5)
            agy_version = res.stdout.strip() or res.stderr.strip()
        except Exception as e:
            agy_version = f"error: {e}"

    from runner import runner
    return {
        "status": "online",
        "service": "telegram-cloud-pc",
        "owner": f"@{config.ALLOWED_USERNAME}",
        "active_session": state.active_conversation_id or "fresh",
        "runner_busy": runner.is_running,
        "storage_channel": config.STORAGE_CHANNEL_ID,
        "agy_installed": agy_path is not None,
        "agy_version": agy_version,
        "ram": f"{used_mb}MB / {total_mb}MB",
        "cpu": f"{cpu}%",
        "uptime": get_uptime_string(),
    }
