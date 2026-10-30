#!/usr/bin/env python3
"""
Telegram Cloud PC - Cloud Backup, Silent Pin & Disaster Recovery Engine.
"""

import os
import io
import time
import json
import zipfile
import logging
import threading
import datetime
from pathlib import Path
import requests

logger = logging.getLogger("CloudPCBackup")

BASE_DIR = Path(__file__).parent.resolve()
DEFAULT_AUDIT_CHANNEL = "-1003899151943"
STORAGE_CHANNEL = "-1004343308436"
FALLBACK_OWNER_ID = "8327850869"

TARGET_FILES = [
    "state.json",
    "config.py"
]

def get_bot_token():
    return os.getenv("BOT_TOKEN", "").strip()

def get_target_channel():
    return os.getenv("AUDIT_CHANNEL", "").strip() or os.getenv("STORAGE_CHANNEL_ID", "").strip() or DEFAULT_AUDIT_CHANNEL

def create_backup_archive():
    bio = io.BytesIO()
    now_ist = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
    ts_str = now_ist.strftime("%Y%m%d_%H%M%S")
    display_time = now_ist.strftime("%d-%b-%Y %I:%M:%S %p IST")
    
    archived = []
    with zipfile.ZipFile(bio, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for fname in TARGET_FILES:
            fp = BASE_DIR / fname
            if fp.exists():
                try:
                    with open(fp, "rb") as f:
                        data = f.read()
                    zf.writestr(fname, data)
                    archived.append((fname, len(data)))
                except Exception as e:
                    logger.warning(f"Error archiving {fname}: {e}")
    bio.seek(0)
    return bio.getvalue(), f"cloudpc_backup_{ts_str}.zip", archived, display_time

def dispatch_and_pin_backup(trigger_reason: str = "3-Hour Scheduled Restart Checkpoint") -> dict:
    token = get_bot_token()
    if not token:
        return {"success": False, "error": "No BOT_TOKEN"}
    
    zip_bytes, zip_filename, archived, display_time = create_backup_archive()
    if not zip_bytes or len(zip_bytes) < 30:
        return {"success": False, "error": "Empty archive"}
    
    caption = (
        f"📦 <b>TELEGRAM CLOUD PC AUTO CLOUD BACKUP &amp; AUDIT</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• 🕒 <b>Timestamp:</b> <code>{display_time}</code>\n"
        f"• 📁 <b>Files Saved:</b> <code>{len(archived)} items</code>\n"
        f"• 🔄 <b>Event:</b> <code>{trigger_reason}</code>\n"
        f"• 📊 <b>Archive:</b> <code>{zip_filename}</code> ({len(zip_bytes):,} bytes)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 <i>Silently pinned as disaster recovery checkpoint. Auto-restores on restart.</i>"
    )

    targets = [get_target_channel(), DEFAULT_AUDIT_CHANNEL, STORAGE_CHANNEL, FALLBACK_OWNER_ID]
    for ch in targets:
        try:
            files = {'document': (zip_filename, zip_bytes, 'application/zip')}
            data = {'chat_id': ch, 'caption': caption, 'parse_mode': 'HTML'}
            r = requests.post(f"https://api.telegram.org/bot{token}/sendDocument", data=data, files=files, timeout=25).json()
            if not r.get("ok"):
                continue
            msg_id = r["result"]["message_id"]
            requests.post(
                f"https://api.telegram.org/bot{token}/pinChatMessage",
                json={"chat_id": ch, "message_id": msg_id, "disable_notification": True},
                timeout=10
            )
            logger.info(f"Successfully uploaded & pinned backup #{msg_id} to chat {ch} ✅")
            return {"success": True, "chat_id": ch, "message_id": msg_id}
        except Exception as e:
            logger.warning(f"Upload to {ch} failed: {e}")
    return {"success": False, "error": "Failed all targets"}

def auto_restore_latest() -> bool:
    token = get_bot_token()
    if not token:
        return False
    
    targets = [get_target_channel(), DEFAULT_AUDIT_CHANNEL, STORAGE_CHANNEL]
    for ch in targets:
        try:
            r = requests.get(f"https://api.telegram.org/bot{token}/getChat", params={"chat_id": ch}, timeout=15).json()
            if not r.get("ok"):
                continue
            pinned = r.get("result", {}).get("pinned_message")
            if not pinned or not pinned.get("document"):
                continue
            doc = pinned["document"]
            doc_name = doc.get("file_name", "")
            if not doc_name.startswith("cloudpc_backup_") or not doc_name.endswith(".zip"):
                continue
            
            fid = doc.get("file_id")
            gf = requests.get(f"https://api.telegram.org/bot{token}/getFile", params={"file_id": fid}, timeout=15).json()
            if not gf.get("ok"):
                continue
            fpath = gf["result"]["file_path"]
            down = requests.get(f"https://api.telegram.org/file/bot{token}/{fpath}", timeout=35)
            if down.status_code != 200:
                continue
            
            with zipfile.ZipFile(io.BytesIO(down.content), "r") as zf:
                restored = 0
                for name in zf.namelist():
                    if name in TARGET_FILES:
                        dest = BASE_DIR / name
                        with open(dest, "wb") as f_out:
                            f_out.write(zf.read(name))
                        restored += 1
            logger.info(f"🚀 Telegram Cloud PC Auto-Recovery SUCCESS! Restored {restored} files from cloud pin ✅")
            return True
        except Exception as e:
            logger.warning(f"Auto-restore check error on {ch}: {e}")
    return False

def start_backup_daemon(interval_seconds: int = 15 * 60):
    def _worker():
        try:
            auto_restore_latest()
        except Exception:
            pass
        time.sleep(60)
        while True:
            try:
                dispatch_and_pin_backup(trigger_reason="15-Minute Sync Checkpoint")
            except Exception:
                pass
            time.sleep(interval_seconds)
    t = threading.Thread(target=_worker, daemon=True, name="CloudPCBackupDaemon")
    t.start()
    return t
