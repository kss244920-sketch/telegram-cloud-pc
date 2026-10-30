import os
import json
import time
import threading
from pathlib import Path
from turso_client import TursoConnection

TURSO_URL = os.environ.get("TURSO_DATABASE_URL", "libsql://1-owmarco.aws-ap-south-1.turso.io")
TURSO_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJhIjoicnciLCJpYXQiOjE3OTMzNzA5NjQsImlkIjoiMDFhMThkM2QtMDQwMS03ODkyLTljY2YtNGQxMjViODQwMmRhIiwia2lkIjoiV3ZrOXBrR1dPdkdaTFEwdmJiSHpaTzFwVHVvRXVMb0RfNjFWejgwSy1XcyIsInJpZCI6ImViMjlkMzdlLTdhZWMtNGM3Ny1iNWJlLTU2ZjJkYmJiMmI2MyJ9.EWy-SZ2NtLCNxpHDp8Y9Ln2h6m3lzUExbNVPGciCRxCKBDP7T-TUZEx-zI8adakMDfYu1E9Al_a1IQOGyHt-Ag")

def get_conn():
    if TURSO_URL and TURSO_TOKEN:
        try:
            return TursoConnection(TURSO_URL, TURSO_TOKEN)
        except Exception:
            return None
    return None

class TursoCloudManager:
    def __init__(self, bot_name: str, base_dir: Path):
        self.bot_name = bot_name
        self.base_dir = Path(base_dir).resolve()

    def restore_all(self, file_map: dict):
        """Restores mapped files from Turso Cloud on container boot."""
        conn = get_conn()
        if not conn:
            return
        try:
            with conn:
                cur = conn.cursor()
                for key, filename in file_map.items():
                    fpath = self.base_dir / filename
                    cur.execute(
                        "SELECT value FROM bot_cloud_storage WHERE bot_name = ? AND key = ?",
                        (self.bot_name, key)
                    )
                    row = cur.fetchone()
                    if row and row.get("value"):
                        val = row["value"]
                        tmp = fpath.with_suffix(f".tmp.{os.getpid()}")
                        with open(tmp, "w", encoding="utf-8") as f:
                            f.write(val)
                        tmp.replace(fpath)
                        print(f"☁️ [TURSO CLOUD] Restored '{filename}' for {self.bot_name} successfully! ✅", flush=True)
        except Exception as e:
            print(f"⚠️ [TURSO RESTORE ERROR] {e}", flush=True)

    def sync_file_async(self, key: str, fpath: Path):
        """Asynchronously syncs file contents to Turso Cloud."""
        p = Path(fpath)
        if not p.exists():
            return
        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            self.set_async(key, content)
        except Exception:
            pass

    def set_async(self, key: str, value: str):
        def _worker(b_name, k, val):
            conn = get_conn()
            if not conn:
                return
            try:
                with conn:
                    cur = conn.cursor()
                    cur.execute(
                        "INSERT INTO bot_cloud_storage (bot_name, key, value, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(bot_name, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                        (b_name, k, val, time.time())
                    )
            except Exception:
                pass
        threading.Thread(target=_worker, args=(self.bot_name, key, value), daemon=True).start()
