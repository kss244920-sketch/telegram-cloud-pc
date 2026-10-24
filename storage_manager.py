import os
import shutil
import zipfile
import logging
from pathlib import Path
from datetime import datetime
from telegram import Bot
import config

logger = logging.getLogger("storage_manager")

class StorageManager:
    def __init__(self):
        self.channel_id = config.STORAGE_CHANNEL_ID

    async def backup_file(self, bot: Bot, file_path: Path, caption: str = "") -> bool:
        """Uploads an individual file to the Telegram Storage Channel."""
        if not file_path.exists() or not file_path.is_file():
            return False

        try:
            with open(file_path, "rb") as f:
                await bot.send_document(
                    chat_id=self.channel_id,
                    document=f,
                    caption=f"📁 File: `{file_path.name}`\nSize: {round(file_path.stat().st_size / 1024, 1)} KB\n{caption}".strip()
                )
            logger.info(f"File {file_path.name} backed up to storage channel {self.channel_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to backup {file_path} to storage channel: {e}")
            return False

    async def backup_workspace_snapshot(self, bot: Bot, project_dir: Path, tag: str = "snapshot") -> bool:
        """Creates a zip archive of workspace and uploads to the storage channel."""
        try:
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            zip_name = f"workspace_{tag}_{timestamp}.zip"
            zip_path = config.DATA_DIR / zip_name

            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(project_dir):
                    dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("__pycache__", "venv", "node_modules")]
                    for file in files:
                        if file.startswith("."):
                            continue
                        full = Path(root) / file
                        rel = full.relative_to(project_dir)
                        zipf.write(full, rel)

            size_kb = round(zip_path.stat().st_size / 1024, 1)
            with open(zip_path, "rb") as f:
                await bot.send_document(
                    chat_id=self.channel_id,
                    document=f,
                    caption=f"📦 Workspace Snapshot: `{tag}`\nTime: {timestamp} UTC\nSize: {size_kb} KB"
                )

            # Cleanup local temp zip
            try:
                zip_path.unlink()
            except Exception:
                pass

            logger.info(f"Workspace snapshot backed up to channel {self.channel_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to create/backup workspace snapshot: {e}")
            return False

storage_manager = StorageManager()
