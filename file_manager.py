import os
import shutil
import zipfile
import logging
from pathlib import Path
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

import config
from security import sanitize_filename, is_path_safe

logger = logging.getLogger("file_manager")

MAX_BOT_DOWNLOAD_MB = 20
MAX_BOT_SEND_MB = 50

class FileManager:
    @staticmethod
    def get_safe_destination(folder: Path, filename: str) -> Path:
        """Finds safe unique filename (e.g. error_log (2).txt)."""
        safe_name = sanitize_filename(filename)
        dest = folder / safe_name
        if not dest.exists():
            return dest

        stem = dest.stem
        suffix = dest.suffix
        counter = 2
        while True:
            candidate = folder / f"{stem} ({counter}){suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    @classmethod
    def save_file(cls, content: bytes, original_name: str, project_dir: Path) -> dict:
        """Saves incoming chat file to project's inbox folder."""
        inbox = project_dir / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)

        dest_path = cls.get_safe_destination(inbox, original_name)
        with open(dest_path, "wb") as f:
            f.write(content)

        file_size_kb = max(1, len(content) // 1024)
        is_zip = dest_path.suffix.lower() == ".zip"
        unzip_info = None

        # Check if secret file like .env
        if dest_path.name.startswith(".env") or "secret" in dest_path.name.lower():
            gitignore = project_dir / ".gitignore"
            existing_ignores = gitignore.read_text() if gitignore.exists() else ""
            if dest_path.name not in existing_ignores:
                with open(gitignore, "a") as f:
                    f.write(f"\n{dest_path.name}\n")

        # Handle zip files
        if is_zip:
            unzip_folder = inbox / f"extracted_{dest_path.stem}"
            unzip_folder.mkdir(parents=True, exist_ok=True)
            extracted_count = 0
            try:
                with zipfile.ZipFile(dest_path, "r") as zf:
                    for member in zf.infolist():
                        # Prevent Zip Slip directory traversal
                        target = (unzip_folder / member.filename).resolve()
                        if unzip_folder.resolve() in target.parents:
                            zf.extract(member, unzip_folder)
                            extracted_count += 1
                unzip_info = f"Zip khul gayi: {extracted_count} files nikli."
            except Exception as e:
                unzip_info = f"Zip extract karne me error: {e}"

        return {
            "name": dest_path.name,
            "path": dest_path,
            "rel_path": dest_path.relative_to(project_dir),
            "size_kb": file_size_kb,
            "is_zip": is_zip,
            "unzip_info": unzip_info
        }

    @staticmethod
    def build_file_browser(project_dir: Path, subpath: str = "") -> tuple[str, InlineKeyboardMarkup]:
        """Generates inline keyboard file browser for Telegram."""
        current_dir = (project_dir / subpath).resolve()
        if not is_path_safe(current_dir, project_dir):
            current_dir = project_dir

        rel = current_dir.relative_to(project_dir)
        header = f"📁 **Files in:** `{rel if str(rel) != '.' else 'Project Root'}`\n\n"
        
        buttons = []
        entries = sorted(list(current_dir.iterdir()), key=lambda x: (not x.is_dir(), x.name.lower()))

        # If not root, add Back button
        if current_dir != project_dir:
            parent_rel = current_dir.parent.relative_to(project_dir)
            buttons.append([InlineKeyboardButton("⬆️ Upar Jao", callback_data=f"fb_dir:{parent_rel}")])

        for item in entries[:20]:  # limit buttons per page
            if item.name.startswith(".git") or item.name == "__pycache__":
                continue
            item_rel = item.relative_to(project_dir)
            if item.is_dir():
                buttons.append([InlineKeyboardButton(f"📁 {item.name}/", callback_data=f"fb_dir:{item_rel}")])
            else:
                size_str = f"{item.stat().st_size // 1024}KB"
                buttons.append([InlineKeyboardButton(f"📄 {item.name} ({size_str})", callback_data=f"fb_get:{item_rel}")])

        return header, InlineKeyboardMarkup(buttons)

    @staticmethod
    def create_project_zip(project_dir: Path) -> Path:
        """Packages project into a zip file excluding git and secrets."""
        zip_path = project_dir.parent / f"{project_dir.name}_backup.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(project_dir):
                # Exclude .git, __pycache__, venv
                dirs[:] = [d for d in dirs if d not in [".git", "__pycache__", "venv", ".pytest_cache"]]
                for f in files:
                    if f.startswith(".env") or f.endswith(".key") or f.endswith(".zip"):
                        continue
                    file_p = Path(root) / f
                    arcname = file_p.relative_to(project_dir)
                    zf.write(file_p, arcname)
        return zip_path
