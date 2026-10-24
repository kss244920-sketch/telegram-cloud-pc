import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import JSONResponse

import config
from bot import create_bot_application, get_uptime_string, get_memory_info
from state import state

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("server")

bot_app = create_bot_application()

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing Telegram Bot...")
    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling(drop_pending_updates=True)
    logger.info("Telegram Bot polling started successfully.")
    
    yield
    
    logger.info("Stopping Telegram Bot...")
    await bot_app.updater.stop()
    await bot_app.stop()
    await bot_app.shutdown()
    logger.info("Telegram Bot shutdown complete.")

app = FastAPI(title="Telegram Cloud PC", lifespan=lifespan)

@app.get("/")
async def root():
    used_mb, total_mb, _ = get_memory_info()
    return {
        "status": "online",
        "service": "telegram-cloud-pc",
        "owner": f"@{config.ALLOWED_USERNAME}",
        "active_conversation": state.active_conversation_id,
        "ram": f"{used_mb}MB / {total_mb}MB",
        "uptime": get_uptime_string(),
    }

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
