import os
import json
import asyncio
import logging
import subprocess
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

import config
from bot import create_bot_application, get_uptime_string, get_memory_info
from terminal_pty import pty_session, verify_telegram_init_data
from state import state
from queue_manager import queue_manager

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("server")

bot_app = create_bot_application()

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Telegram Cloud PC service...")
    try:
        pty_session.start()
    except Exception as e:
        logger.warning(f"Could not start PTY session: {e}")

    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling(drop_pending_updates=True)
    logger.info("Telegram Bot polling started successfully.")

    yield

    logger.info("Shutting down Telegram Cloud PC service...")
    await bot_app.updater.stop()
    await bot_app.stop()
    await bot_app.shutdown()
    pty_session.close()
    logger.info("Shutdown complete.")

app = FastAPI(title="Telegram Cloud PC", lifespan=lifespan)

STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/", response_class=HTMLResponse)
@app.get("/terminal", response_class=HTMLResponse)
async def serve_mini_app():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return HTMLResponse("<h3>Cloud PC Terminal Mini App is running</h3>")

@app.get("/health")
async def health():
    return JSONResponse(
        content={
            "status": "ok",
            "service": "telegram-cloud-pc",
            "uptime": get_uptime_string(),
        },
        status_code=200
    )

@app.get("/status")
@app.get("/api/system-info")
async def system_info_api():
    used_mb, total_mb, cpu = get_memory_info()
    
    # Check agy installation status
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
        "active_conversation": state.active_conversation_id,
        "agy_installed": agy_path is not None,
        "agy_version": agy_version,
        "agy_path": agy_path,
        "ram": f"{used_mb}MB / {total_mb}MB",
        "cpu": f"{cpu}%",
        "uptime": get_uptime_string(),
        "queue_count": len(state.queue),
        "mode": state.mode,
        "has_api_key": bool(config.GEMINI_API_KEY)
    }

@app.get("/api/queue")
async def get_queue():
    return {
        "queue": state.queue,
        "is_processing": queue_manager.is_processing,
    }

@app.websocket("/ws/terminal")
async def websocket_terminal(websocket: WebSocket, initData: str = Query(default="")):
    is_valid = verify_telegram_init_data(initData, config.BOT_TOKEN)
    if not is_valid and initData:
        logger.warning("Rejected WebSocket connection: Invalid initData signature.")
        await websocket.close(code=1008)
        return

    await websocket.accept()
    pty_session.connected_websockets.add(websocket)
    logger.info(f"Client connected to terminal (active clients: {len(pty_session.connected_websockets)})")

    # Replay buffered terminal history
    if pty_session.history_buffer:
        try:
            await websocket.send_text(pty_session.history_buffer)
        except Exception:
            pass

    try:
        while True:
            data = await websocket.receive_text()
            if data:
                if data.startswith('{"resize":'):
                    try:
                        msg = json.loads(data)
                        pty_session.set_winsize(msg["resize"]["rows"], msg["resize"]["cols"])
                    except Exception:
                        pass
                else:
                    pty_session.write(data)
    except WebSocketDisconnect:
        logger.info("Client disconnected from terminal.")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        pty_session.connected_websockets.discard(websocket)
