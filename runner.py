import asyncio
import json
import logging
import os
import re
import shutil
import time
from pathlib import Path
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

import config
from security import mask_secrets
from git_manager import GitManager
from state import state

logger = logging.getLogger("runner")

ANSI_ESCAPE_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

def strip_ansi(text: str) -> str:
    """Removes ANSI color and control codes for clean Telegram display."""
    return ANSI_ESCAPE_RE.sub('', text)

def format_terminal_box(
    dir_name: str,
    prompt_line: str,
    output_lines: list[str],
    status_text: str,
    max_lines: int = 18
) -> str:
    """Builds a formatted terminal box suitable for Telegram code block."""
    header = f"┌── 💻 Terminal [{dir_name}] "
    header += "─" * max(1, 38 - len(header)) + "┐"

    total = len(output_lines)
    if total > max_lines:
        visible = output_lines[-max_lines:]
        body = [f"... [{total - max_lines} lines scrolled above] ..."] + visible
    else:
        body = output_lines if output_lines else ["(no output)"]

    screen_content = f"$ {prompt_line}\n" + "\n".join(body)
    # Telegram max limit safety
    if len(screen_content) > 3400:
        screen_content = screen_content[-3400:]

    footer = f"└── [{status_text}] "
    footer += "─" * max(1, 38 - len(footer)) + "┘"

    return f"```text\n{header}\n{screen_content}\n{footer}\n```"

class AgyRunner:
    def __init__(self):
        self.current_process: asyncio.subprocess.Process | None = None
        self.is_running = False
        self.cancel_requested = False

    def find_agy_binary(self) -> str | None:
        """Finds agy binary on host."""
        candidates = [
            shutil.which("agy"),
            "/home/piyush/.local/bin/agy",
            str(Path.home() / ".local" / "bin" / "agy"),
            "/usr/local/bin/agy",
            "/app/bin/agy"
        ]
        for c in candidates:
            if c and os.path.exists(c) and os.access(c, os.X_OK):
                return c
        return None

    async def run_task(
        self,
        prompt: str,
        project_dir: Path,
        bot: Bot,
        chat_id: int,
        live_message_id: int,
        conversation_id: str | None = None
    ) -> dict:
        """
        Executes a task through bash or agy, streaming real-time live updates
        directly inside the Telegram terminal window.
        """
        self.is_running = True
        self.cancel_requested = False
        start_time = time.time()

        # Keepalive ping task to prevent Render spin-down during execution
        async def keepalive():
            import aiohttp
            async with aiohttp.ClientSession() as session:
                while self.is_running:
                    await asyncio.sleep(60)
                    try:
                        await session.get(f"http://localhost:{config.PORT}/health")
                    except Exception:
                        pass

        keepalive_task = asyncio.create_task(keepalive())

        try:
            # Automatic checkpoint before task
            GitManager.create_checkpoint(project_dir, prompt)

            # Keep TELEGRAM_STORAGE.md synchronized so agy knows all available files
            try:
                from storage_manager import storage_manager
                manifest = storage_manager.load_manifest()
                storage_manager.update_storage_doc(project_dir, manifest)

                # If prompt mentions any file saved in Telegram storage that isn't on local disk, auto-restore it!
                inbox_dir = project_dir / "inbox"
                for item in manifest:
                    fn = item.get("file_name")
                    if fn and (fn in prompt or f"inbox/{fn}" in prompt) and not (inbox_dir / fn).exists():
                        await storage_manager.ensure_file_present(bot, fn, inbox_dir)
            except Exception as e:
                logger.warning(f"Storage manifest update/restore warning: {e}")

            # Smart Classification: Pure Bash commands run in Shell, everything else runs in agy AI!
            clean_p = prompt.strip()
            clean_p = re.sub(r"^(?:Task\s*\d+:|\d+[\.\)])\s*", "", clean_p).strip()

            # If user types bare 'agy' in text chat without prompt
            if clean_p.lower() == "agy":
                return await self._run_bare_agy_help(
                    bot=bot,
                    chat_id=chat_id,
                    live_message_id=live_message_id,
                    start_time=start_time
                )

            bash_binary_names = {
                "ls", "pwd", "df", "free", "uname", "whoami", "ps", "git",
                "python", "python3", "pip", "pip3", "cat", "mkdir", "echo",
                "find", "grep", "curl", "wget", "which", "head", "tail", "wc", "touch",
                "cd", "rm", "cp", "mv", "chmod", "chown", "tar", "zip", "unzip",
                "env", "export", "top", "htop", "tree", "node", "npm", "uptime", "id",
                "clear", "kill", "sed", "awk", "ping", "traceroute", "systemctl", "docker", "apt"
            }

            chat_words = {
                "karo", "batao", "dekh", "fix", "please", "kyu", "kya", "hai", "kaise", 
                "banao", "likho", "samjhao", "hlo", "hello", "hi", "hey", "help", "who", 
                "what", "how", "why", "write", "create", "make", "explain", "code", "generate"
            }

            first_word = clean_p.split()[0].lower() if clean_p else ""
            has_chat_words = any(w in clean_p.lower().split() for w in chat_words)
            has_ai_prefix = clean_p.lower().startswith(("ai ", "/ai ", "agy ", "/agy "))
            has_bash_prefix = clean_p.startswith(("$ ", "bash ", "sh ", "./"))

            # Determine whether it should run in bash shell
            if has_ai_prefix:
                is_bash = False
            elif has_bash_prefix:
                is_bash = True
            elif first_word in bash_binary_names and not has_chat_words:
                is_bash = True
            else:
                # Natural language, questions, greetings, AI instructions (no 'ai' prefix needed!)
                is_bash = False

            if is_bash:
                cmd_to_run = clean_p
                if cmd_to_run.startswith("$ "):
                    cmd_to_run = cmd_to_run[2:].strip()
                elif cmd_to_run.startswith("bash "):
                    cmd_to_run = cmd_to_run[5:].strip()
                elif cmd_to_run.startswith("sh "):
                    cmd_to_run = cmd_to_run[3:].strip()

                return await self._run_bash_stream(
                    cmd_to_run=cmd_to_run,
                    bot=bot,
                    chat_id=chat_id,
                    live_message_id=live_message_id,
                    start_time=start_time
                )
            else:
                ai_prompt = re.sub(r"^(?:ai|/ai|agy|/agy)\s*", "", clean_p, flags=re.IGNORECASE).strip()
                return await self._run_agy_stream(
                    prompt=ai_prompt or clean_p,
                    project_dir=project_dir,
                    bot=bot,
                    chat_id=chat_id,
                    live_message_id=live_message_id,
                    conversation_id=conversation_id,
                    start_time=start_time
                )

        finally:
            self.is_running = False
            self.current_process = None
            keepalive_task.cancel()

    async def _run_bare_agy_help(
        self,
        bot: Bot,
        chat_id: int,
        live_message_id: int,
        start_time: float
    ) -> dict:
        """Displays guidance when user types bare 'agy' without a prompt."""
        cwd = state.get_current_cwd()
        dir_name = cwd.name or "workspace"

        lines = [
            "ℹ️ 'agy' ek full interactive TUI hai jo text chat me TTY maangta hai.",
            "",
            "👉 Text chat me aise chalayein:",
            "  • ai <aapka prompt>  (e.g: ai ek bot script banao)",
            "  • agy -p \"<aapka prompt>\"",
            "",
            "📱 Full Interactive agy (Visual TUI) ke liye:",
            "  • Chat me /start dabakar 'Open Mini App' kholein!"
        ]

        box = format_terminal_box(
            dir_name=dir_name,
            prompt_line="agy",
            output_lines=lines,
            status_text="✅ Ready | Exit 0"
        )
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=live_message_id,
                text=mask_secrets(box),
                parse_mode="Markdown"
            )
        except Exception:
            pass

        return {"status": "success", "time": "0s", "response": ""}

    async def _run_bash_stream(
        self,
        cmd_to_run: str,
        bot: Bot,
        chat_id: int,
        live_message_id: int,
        start_time: float
    ) -> dict:
        """Executes bash commands streaming live output directly to the Telegram message."""
        cwd = state.get_current_cwd()
        dir_name = cwd.name or "workspace"

        cancel_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛑 Cancel", callback_data="stop_current_task")]
        ])

        output_lines: list[str] = []
        last_edit_time = 0.0

        async def update_msg(final=False, return_code: int = 0):
            nonlocal last_edit_time
            now = time.time()
            if not final and (now - last_edit_time) < 1.0:
                return
            last_edit_time = now

            elapsed = int(time.time() - start_time)
            time_str = f"{elapsed}s"

            if final:
                if self.cancel_requested:
                    status_text = f"🛑 Cancelled ({time_str})"
                elif return_code == 0:
                    status_text = f"✅ Done in {time_str} | Exit 0"
                else:
                    status_text = f"❌ Exit {return_code} ({time_str})"
                markup = None
            else:
                status_text = f"⏳ Running... ({time_str})"
                markup = cancel_markup

            box = format_terminal_box(
                dir_name=dir_name,
                prompt_line=cmd_to_run,
                output_lines=output_lines,
                status_text=status_text
            )
            masked = mask_secrets(box)

            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=live_message_id,
                    text=masked,
                    parse_mode="Markdown",
                    reply_markup=markup
                )
            except TelegramError as e:
                if "Message is not modified" not in str(e):
                    try:
                        await bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=live_message_id,
                            text=masked,
                            reply_markup=markup
                        )
                    except Exception:
                        pass

        # Handle cd command internally to persist directory state
        if cmd_to_run.startswith("cd ") or cmd_to_run == "cd":
            target_str = cmd_to_run[3:].strip() if cmd_to_run.startswith("cd ") else "~"
            ok, msg = state.change_directory(target_str)
            if ok:
                output_lines.append(f"(Directory changed to: {msg})")
                ret = 0
            else:
                output_lines.append(f"cd: {msg}")
                ret = 1
            await update_msg(final=True, return_code=ret)
            return {"status": "success" if ok else "failed", "time": "0s", "response": ""}

        env = os.environ.copy()
        if config.GEMINI_API_KEY:
            env["GEMINI_API_KEY"] = config.GEMINI_API_KEY
        env["PAGER"] = "cat"
        env["TERM"] = "xterm-256color"

        try:
            self.current_process = await asyncio.create_subprocess_shell(
                cmd_to_run,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env
            )

            # Initial render
            await update_msg(final=False)

            # Read stdout line by line in real-time
            while True:
                line_bytes = await self.current_process.stdout.readline()
                if not line_bytes:
                    break

                raw_line = line_bytes.decode("utf-8", errors="replace").rstrip("\r\n")
                if "\r" in raw_line:
                    raw_line = raw_line.split("\r")[-1]
                clean = strip_ansi(raw_line)
                output_lines.append(clean)
                await update_msg(final=False)

            await self.current_process.wait()
            ret_code = self.current_process.returncode or 0
            await update_msg(final=True, return_code=ret_code)

            elapsed_sec = int(time.time() - start_time)
            time_str = f"{elapsed_sec}s"

            # Output is already inside the terminal box, no need to duplicate message
            return {
                "status": "success" if ret_code == 0 else "failed",
                "time": time_str,
                "response": ""
            }

        except Exception as e:
            logger.exception("Error running bash task")
            output_lines.append(f"❌ Error: {str(e)}")
            await update_msg(final=True, return_code=1)
            return {"status": "error", "error": str(e), "response": ""}

    async def _run_agy_stream(
        self,
        prompt: str,
        project_dir: Path,
        bot: Bot,
        chat_id: int,
        live_message_id: int,
        conversation_id: str | None,
        start_time: float
    ) -> dict:
        """Runs full agy AI task with live terminal event streaming."""
        agy_bin = self.find_agy_binary()

        cancel_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛑 Cancel", callback_data="stop_current_task")]
        ])

        display_lines: list[str] = [f"🚀 Task: {prompt[:35]}"]
        last_edit_time = 0.0

        async def update_msg(final=False, status_override: str | None = None):
            nonlocal last_edit_time
            now = time.time()
            if not final and (now - last_edit_time) < 1.2:
                return
            last_edit_time = now

            elapsed = int(time.time() - start_time)
            mins, secs = divmod(elapsed, 60)
            time_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"

            cid_disp = conversation_id[:8] if conversation_id else "fresh"
            header = f"┌── 💻 agy terminal [{cid_disp}] "
            header += "─" * max(1, 38 - len(header)) + "┐"

            if final:
                status_text = status_override or (f"🛑 Cancelled ({time_str})" if self.cancel_requested else f"✅ Completed in {time_str}")
                markup = None
            else:
                status_text = status_override or f"⏳ Working... ({time_str})"
                markup = cancel_markup

            footer = f"└── [{status_text}] "
            footer += "─" * max(1, 38 - len(footer)) + "┘"

            events_to_show = display_lines[-15:]
            body = "\n".join(events_to_show)
            terminal_block = f"```text\n{header}\n{body}\n{footer}\n```"
            masked = mask_secrets(terminal_block)

            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=live_message_id,
                    text=masked,
                    parse_mode="Markdown",
                    reply_markup=markup
                )
            except TelegramError as e:
                if "Message is not modified" not in str(e):
                    try:
                        await bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=live_message_id,
                            text=masked,
                            reply_markup=markup
                        )
                    except Exception:
                        pass

        if not agy_bin:
            display_lines.append("❌ `agy` binary not found.")
            await update_msg(final=True, status_override="❌ Failed: agy missing")
            return {"status": "failed", "error": "agy binary not found", "response": "❌ `agy` binary nahi mila."}

        env = os.environ.copy()
        if config.GEMINI_API_KEY:
            env["GEMINI_API_KEY"] = config.GEMINI_API_KEY
        env["PAGER"] = "cat"

        cmd = [
            agy_bin,
            "-p", prompt,
            "--dangerously-skip-permissions",
            "--output-format", "stream-json"
        ]
        if conversation_id and conversation_id.strip():
            cmd.extend(["--conversation", conversation_id.strip()])

        logger.info(f"Running agy command: {' '.join(cmd)} in {project_dir}")

        self.current_process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(project_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env
        )

        final_response = ""
        agent_deltas = []

        while True:
            line_bytes = await self.current_process.stdout.readline()
            if not line_bytes:
                break

            line_str = line_bytes.decode("utf-8", errors="ignore").strip()
            if not line_str:
                continue

            try:
                event = json.loads(line_str)
                ev_type = event.get("event")

                if ev_type == "init":
                    real_conv = event.get("conversation_id", "")
                    if real_conv:
                        conversation_id = real_conv
                        try:
                            state.set_active_conversation_id(real_conv)
                        except Exception:
                            pass

                elif ev_type == "step_update":
                    su = event.get("step_update", {})
                    stype = su.get("step_type")

                    if stype == "agent_response":
                        dur = su.get("duration_seconds", 0)
                        delta = su.get("text_delta", "")
                        if delta:
                            agent_deltas.append(delta)
                            clean_d = delta.strip()
                            if clean_d and len(clean_d) > 3 and not clean_d.startswith("{"):
                                first_line = clean_d.split("\n")[0][:40]
                                prefix = f"💬 {first_line}"
                                if not display_lines or not display_lines[-1].startswith(prefix[:20]):
                                    display_lines.append(prefix)
                        if dur > 0:
                            display_lines.append(f"🧠 Thought ({int(dur)}s)")

                    elif stype == "tool":
                        tool_name = su.get("tool_name", "")
                        params = su.get("tool_info", {}).get("parameters", {})
                        tstate = su.get("state")

                        if tool_name == "run_command":
                            cl = params.get("CommandLine", "")[:40]
                            if tstate == "ACTIVE":
                                display_lines.append(f"⚡ $ {cl}")
                            elif tstate == "DONE":
                                display_lines.append(f"✅ $ {cl}")
                        elif tool_name in ["read_file", "view_file"]:
                            p = params.get("Path", "")
                            display_lines.append(f"📖 Read: {os.path.basename(p)}")
                        elif tool_name in ["edit_file", "replace_file_content", "write_to_file"]:
                            p = params.get("Path", "")
                            display_lines.append(f"✏️ Edit: {os.path.basename(p)}")
                        elif tool_name == "search_web":
                            q = params.get("query", "")[:30]
                            display_lines.append(f"🌐 Search: {q}")
                        else:
                            display_lines.append(f"🔧 Tool: {tool_name}")

                    await update_msg(final=False)

                elif ev_type == "result":
                    res = event.get("result", {})
                    final_response = res.get("response", "")

            except json.JSONDecodeError:
                if len(line_str) > 0 and not line_str.startswith("{"):
                    if "Authentication required" in line_str or "accounts.google.com/o/oauth2" in line_str:
                        display_lines.append("🔐 Auth required: /login ya /key karein.")
                        await update_msg(final=True, status_override="🔐 Auth Required")
                        self.stop_current_task()
                        return {
                            "status": "auth_required",
                            "time": "0s",
                            "response": "🔐 **Google Authentication Required!**\n\n1. Chat me `/login` likhein aur sign-in karein.\n2. Phir `/code <authorization_code>` bhejein.\n\n_Ya Gemini API Key set karein:_ `/key <AIzaSy...>`"
                        }
                    clean_line = line_str.replace("`", "").strip()[:50]
                    if not clean_line.startswith("warning: conversation"):
                        display_lines.append(f"ℹ️ {clean_line}")
                        await update_msg(final=False)

        await self.current_process.wait()
        return_code = self.current_process.returncode or 0

        elapsed_sec = int(time.time() - start_time)
        mins, secs = divmod(elapsed_sec, 60)
        time_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"

        if not final_response and agent_deltas:
            final_response = "".join(agent_deltas).strip()

        if self.cancel_requested:
            await update_msg(final=True, status_override=f"🛑 Stopped by user ({time_str})")
            return {"status": "stopped", "time": time_str, "response": "Task cancelled by user."}

        if return_code == 0:
            await update_msg(final=True, status_override=f"✅ Completed in {time_str}")
            GitManager.auto_commit_and_push(project_dir, prompt[:50])

            try:
                from storage_manager import storage_manager
                asyncio.create_task(
                    storage_manager.backup_workspace_snapshot(
                        bot=bot,
                        project_dir=project_dir,
                        tag=conversation_id[:8] if conversation_id else "task"
                    )
                )
            except Exception as e:
                logger.warning(f"Storage channel snapshot failed: {e}")

            return {
                "status": "success",
                "time": time_str,
                "response": final_response or "✅ Task completed successfully."
            }
        else:
            await update_msg(final=True, status_override=f"❌ Exit {return_code} ({time_str})")
            return {
                "status": "failed",
                "code": return_code,
                "time": time_str,
                "response": final_response or f"❌ Process exited with return code {return_code}"
            }

    def stop_current_task(self) -> bool:
        """Cancels running execution."""
        if self.current_process and self.is_running:
            self.cancel_requested = True
            try:
                self.current_process.terminate()
                return True
            except Exception:
                try:
                    self.current_process.kill()
                    return True
                except Exception:
                    pass
        return False

runner = AgyRunner()
