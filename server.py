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
logger = logging.getLogger("server")

bot_app = create_bot_application()

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Telegram Cloud PC terminal bot service...")
    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling(drop_pending_updates=True)
    logger.info("Telegram Bot polling started successfully.")

    yield

    logger.info("Shutting down Telegram Cloud PC service...")
    await bot_app.updater.stop()
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

    return {
        "status": "online",
        "service": "telegram-cloud-pc",
        "owner": f"@{config.ALLOWED_USERNAME}",
        "active_session": state.active_conversation_id,
        "storage_channel": config.STORAGE_CHANNEL_ID,
        "agy_installed": agy_path is not None,
        "agy_version": agy_version,
        "ram": f"{used_mb}MB / {total_mb}MB",
        "cpu": f"{cpu}%",
        "uptime": get_uptime_string(),
    }
