import os
import json
import shutil
import zipfile
import logging
from pathlib import Path
from datetime import datetime
from telegram import Bot
import config

logger = logging.getLogger("storage_manager")
MANIFEST_FILE = config.DATA_DIR / "storage_manifest.json"

class StorageManager:
    def __init__(self):
        self.channel_id = config.STORAGE_CHANNEL_ID

    def load_manifest(self) -> list[dict]:
        """Loads metadata manifest of files saved to the Telegram Storage Channel."""
        candidates = [
            MANIFEST_FILE,
            config.WORKSPACE_DIR / "default" / ".storage_manifest.json",
            config.BASE_DIR / ".storage_manifest.json"
        ]
        for p in candidates:
            if p.exists():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if data:
                            return data
                except Exception as e:
                    logger.debug(f"Could not load manifest from {p}: {e}")
        return []

    def save_manifest(self, manifest: list[dict]):
        candidates = [
            MANIFEST_FILE,
            config.WORKSPACE_DIR / "default" / ".storage_manifest.json"
        ]
        for p in candidates:
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(manifest, f, indent=2)
            except Exception as e:
                logger.error(f"Failed to save manifest to {p}: {e}")

    def update_storage_doc(self, project_dir: Path, manifest: list[dict]):
        """Creates/updates TELEGRAM_STORAGE.md in workspace root for agy and shell access."""
        try:
            doc_path = project_dir / "TELEGRAM_STORAGE.md"
            lines = [
                f"# ☁️ Telegram Cloud Storage (Channel `{self.channel_id}`)\n",
                "Persistent cloud drive connected via Telegram. All files and uploads are preserved permanently here.",
                "\n### 📁 Available Files:\n"
            ]
            if not manifest:
                lines.append("_No files stored yet._\n")
            else:
                for item in manifest[-30:]:  # Last 30 files
                    fname = item.get("file_name", "unknown")
                    size = item.get("size_kb", 0)
                    caption = f" - {item['caption']}" if item.get("caption") else ""
                    lines.append(f"- **`{fname}`** ({size} KB){caption} | Path: `inbox/{fname}`")

            doc_path.write_text("\n".join(lines), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Could not update TELEGRAM_STORAGE.md: {e}")

    def record_file(
        self,
        file_name: str,
        file_id: str,
        size_kb: float,
        message_id: int,
        caption: str = "",
        project_dir: Path | None = None
    ):
        """Records an uploaded file to the manifest and workspace index."""
        manifest = self.load_manifest()
        # Remove duplicate name if already exists
        manifest = [x for x in manifest if x.get("file_name") != file_name]
        manifest.append({
            "file_name": file_name,
            "file_id": file_id,
            "size_kb": size_kb,
            "message_id": message_id,
            "caption": caption,
            "uploaded_at": datetime.utcnow().isoformat()
        })
        self.save_manifest(manifest)

        if project_dir:
            self.update_storage_doc(project_dir, manifest)

    async def backup_file(self, bot: Bot, file_path: Path, caption: str = "", project_dir: Path | None = None) -> bool:
        """Uploads an individual file to the Telegram Storage Channel and records it."""
        if not file_path.exists() or not file_path.is_file():
            return False

        try:
            size_kb = round(file_path.stat().st_size / 1024, 1)
            with open(file_path, "rb") as f:
                msg = await bot.send_document(
                    chat_id=self.channel_id,
                    document=f,
                    caption=f"📁 Cloud File: `{file_path.name}`\nSize: {size_kb} KB\n{caption}".strip()
                )
            
            file_id = msg.document.file_id if msg.document else ""
            self.record_file(
                file_name=file_path.name,
                file_id=file_id,
                size_kb=size_kb,
                message_id=msg.message_id,
                caption=caption,
                project_dir=project_dir
            )
            logger.info(f"File {file_path.name} backed up to storage channel {self.channel_id} (file_id={file_id[:10]}...)")
            return True
        except Exception as e:
            logger.error(f"Failed to backup {file_path} to storage channel: {e}")
            return False

    async def ensure_file_present(self, bot: Bot, file_name: str, dest_dir: Path) -> Path | None:
        """Downloads file from Telegram Cloud Storage if missing locally."""
        dest_path = dest_dir / file_name
        if dest_path.exists() and dest_path.is_file():
            return dest_path

        manifest = self.load_manifest()
        match = next((x for x in manifest if x.get("file_name") == file_name), None)
        if not match or not match.get("file_id"):
            return None

        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            tg_file = await bot.get_file(match["file_id"])
            await tg_file.download_to_drive(dest_path)
            logger.info(f"Restored file {file_name} from Telegram Storage Channel into {dest_path}")
            return dest_path
        except Exception as e:
            logger.error(f"Failed to restore {file_name} from storage channel: {e}")
            return None

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
                msg = await bot.send_document(
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
