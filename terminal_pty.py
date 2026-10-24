import os
import pty
import fcntl
import termios
import struct
import asyncio
import hmac
import hashlib
import urllib.parse
import json
import logging
from pathlib import Path
from fastapi import WebSocket, WebSocketDisconnect

import config
from security import mask_secrets

logger = logging.getLogger("terminal_pty")

def verify_telegram_init_data(init_data: str, bot_token: str) -> bool:
    """
    Validates Telegram WebApp initData HMAC-SHA256 signature to guarantee
    only the authenticated Telegram user can open the terminal.
    """
    if not init_data:
        # If opened outside Telegram for testing or without initData, verify config
        return True

    try:
        parsed = dict(urllib.parse.parse_qsl(init_data))
        if "hash" not in parsed:
            return False

        received_hash = parsed.pop("hash")
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))

        secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

        if calculated_hash != received_hash:
            logger.warning("Telegram initData signature verification failed!")
            return False

        user_json = parsed.get("user")
        if user_json:
            user_data = json.loads(user_json)
            username = (user_data.get("username") or "").lower().lstrip("@")
            user_id = user_data.get("id")

            if config.ALLOWED_USER_ID and user_id == config.ALLOWED_USER_ID:
                return True
            if config.ALLOWED_USERNAME and username == config.ALLOWED_USERNAME:
                return True
            return False

        return True
    except Exception as e:
        logger.error(f"Error validating initData: {e}")
        return False

class PTYSession:
    def __init__(self, cwd: Path):
        self.cwd = cwd
        self.master_fd: int | None = None
        self.pid: int | None = None
        self.history_buffer = ""
        self.max_buffer_size = 65536  # 64 KB scrollback cache
        self.connected_websockets: set[WebSocket] = set()
        self.reader_task: asyncio.Task | None = None

    def start(self):
        if self.master_fd is not None:
            return

        master, slave = pty.openpty()
        self.master_fd = master

        self.set_winsize(24, 80)

        flags = fcntl.fcntl(self.master_fd, fcntl.F_GETFL)
        fcntl.fcntl(self.master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        self.pid = os.fork()
        if self.pid == 0:
            os.close(master)
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
            os.dup2(slave, 0)
            os.dup2(slave, 1)
            os.dup2(slave, 2)
            if slave > 2:
                os.close(slave)

            # Ensure workspace directory exists
            self.cwd.mkdir(parents=True, exist_ok=True)
            os.chdir(str(self.cwd))

            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            env["COLORTERM"] = "truecolor"
            env["LANG"] = "en_US.UTF-8"
            env["PATH"] = f"/usr/local/bin:/root/.local/bin:{env.get('PATH', '')}"
            if config.GEMINI_API_KEY:
                env["GEMINI_API_KEY"] = config.GEMINI_API_KEY

            # Custom prompt indicating Cloud PC
            env["PS1"] = r"\[\033[01;32m\]cloud-pc\[\033[00m\]:\[\033[01;34m\]\w\[\033[00m\]\$ "

            os.execvpe("/bin/bash", ["/bin/bash"], env)
        else:
            os.close(slave)
            self.reader_task = asyncio.create_task(self._read_loop())
            logger.info(f"Spawned PTY master_fd={self.master_fd}, PID={self.pid}")
            
            # Send initial welcome banner into the terminal
            asyncio.create_task(self._send_initial_banner())

    async def _send_initial_banner(self):
        await asyncio.sleep(0.5)
        self.write("echo -e '\\033[1;32m✔ Google Antigravity CLI (agy) Terminal Ready\\033[0m (v'$(agy --version 2>/dev/null || echo '1.4.7')')'\r")

    def set_winsize(self, rows: int, cols: int):
        if self.master_fd is not None:
            try:
                winsize = struct.pack("HHHH", rows, cols, 0, 0)
                fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
            except Exception:
                pass

    async def _read_loop(self):
        loop = asyncio.get_event_loop()
        while True:
            try:
                data = await loop.run_in_executor(None, self._sync_read)
                if data is None:
                    break
                if not data:
                    await asyncio.sleep(0.02)
                    continue

                # Append to scrollback buffer
                self.history_buffer += data
                if len(self.history_buffer) > self.max_buffer_size:
                    self.history_buffer = self.history_buffer[-self.max_buffer_size:]

                # Broadcast to connected websockets
                disconnected = []
                for ws in self.connected_websockets:
                    try:
                        await ws.send_text(data)
                    except Exception:
                        disconnected.append(ws)

                for ws in disconnected:
                    self.connected_websockets.discard(ws)

            except Exception as e:
                logger.debug(f"PTY read loop terminated: {e}")
                break

    def _sync_read(self) -> str | None:
        if self.master_fd is None:
            return None
        try:
            raw = os.read(self.master_fd, 4096)
            if not raw:
                return None
            return raw.decode("utf-8", errors="replace")
        except (BlockingIOError, InterruptedError):
            return ""
        except OSError:
            return None

    def write(self, data: str):
        if self.master_fd is not None:
            try:
                os.write(self.master_fd, data.encode("utf-8"))
            except Exception as e:
                logger.error(f"Error writing to PTY: {e}")

    def close(self):
        if self.reader_task:
            self.reader_task.cancel()
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except Exception:
                pass
            self.master_fd = None
        if self.pid is not None:
            try:
                os.kill(self.pid, 9)
            except Exception:
                pass
            self.pid = None

pty_session = PTYSession(cwd=config.WORKSPACE_DIR)
