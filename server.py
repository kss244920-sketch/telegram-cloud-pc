import os
import json
import asyncio
import logging
import subprocess
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

import config
from bot import create_bot_application, get_uptime_string, get_memory_info
from terminal_pty import pty_session, verify_telegram_init_data
from state import state
from queue_manager import queue_manager
from chat_service import chat_service

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

@app.post("/api/chat")
async def chat_api(req: Request):
    try:
        body = await req.json()
        message = body.get("message", "").strip()
        conv_id = body.get("conversation_id") or state.active_conversation_id
        if not message:
            return JSONResponse({"error": "Empty message"}, status_code=400)
        pdir = state.get_project_dir()
        reply = await chat_service.generate_reply(prompt=message, conv_id=conv_id, project_dir=pdir)
        return {"response": reply, "conversation_id": conv_id}
    except Exception as e:
        logger.error(f"Chat API error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/chat/history")
async def chat_history_api(conv_id: str = Query(default="")):
    target_conv = conv_id or state.active_conversation_id
    return {"messages": state.get_chat_messages(target_conv)}

@app.post("/api/queue/add")
async def queue_add_api(req: Request):
    body = await req.json()
    title = body.get("title", "").strip()
    if not title:
        return JSONResponse({"error": "Empty title"}, status_code=400)
    queue_manager.add_item(title=title, item_type="text")
    return {"success": True, "queue": state.queue}

@app.post("/api/queue/proceed")
async def queue_proceed_api():
    if queue_manager.is_processing:
        return JSONResponse({"error": "Queue processing is already running"}, status_code=400)
    if not state.queue:
        return JSONResponse({"error": "Queue is empty"}, status_code=400)
    pdir = state.get_project_dir()
    asyncio.create_task(
        queue_manager.process_queue(
            bot=bot_app.bot,
            chat_id=config.ALLOWED_USER_ID or 0,
            project_dir=pdir
        )
    )
    return {"success": True, "message": "Queue execution started"}

@app.post("/api/queue/clear")
async def queue_clear_api():
    queue_manager.clear()
    return {"success": True, "queue": []}

@app.get("/api/files")
async def files_api():
    pdir = state.get_project_dir()
    files = []
    for root, dirs, filenames in os.walk(pdir):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("__pycache__", "venv", "node_modules")]
        for f in filenames:
            if f.startswith("."):
                continue
            full_path = Path(root) / f
            try:
                rel_path = full_path.relative_to(pdir)
                size_bytes = full_path.stat().st_size
                files.append({
                    "path": str(rel_path),
                    "name": f,
                    "size_kb": round(size_bytes / 1024, 1)
                })
            except Exception:
                pass
    return {"project": state.current_project, "files": sorted(files, key=lambda x: x["path"])}

@app.get("/api/file-content")
async def file_content_api(path: str = Query(...)):
    pdir = state.get_project_dir()
    resolved = (pdir / path).resolve()
    if not resolved.is_relative_to(pdir.resolve()):
        return JSONResponse({"error": "Access denied"}, status_code=403)
    if not resolved.exists() or not resolved.is_file():
        return JSONResponse({"error": "File not found"}, status_code=404)
    try:
        content = resolved.read_text(encoding="utf-8", errors="ignore")
        return {"path": path, "content": content}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/mode")
async def mode_api(req: Request):
    body = await req.json()
    m = body.get("mode", "").strip().lower()
    if m in ["chat", "queue", "instant"]:
        state.mode = m
        state.save()
        return {"success": True, "mode": state.mode}
    return JSONResponse({"error": "Invalid mode"}, status_code=400)

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
