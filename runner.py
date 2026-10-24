import asyncio
import json
import logging
import os
import shutil
import time
from pathlib import Path
from telegram import Bot
from telegram.error import TelegramError

import config
from security import mask_secrets
from git_manager import GitManager

logger = logging.getLogger("runner")

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
        Executes a task through agy, streaming live updates to a Telegram message.
        """
        self.is_running = True
        self.cancel_requested = False
        start_time = time.time()
        
        agy_bin = self.find_agy_binary()
        
        # Output lines buffer
        display_lines: list[str] = [f"🚀 Task shuru hua: `{prompt[:50]}`"]
        last_edit_time = 0.0
        lock = asyncio.Lock()

        async def update_telegram(final=False):
            nonlocal last_edit_time
            now = time.time()
            if not final and (now - last_edit_time) < 2.0:
                return

            last_edit_time = now
            # Strict rolling window: only show the latest 10 events
            events_to_show = display_lines[-10:]
            header = f"💻 `agy terminal` [{conversation_id[:8] if conversation_id else 'session'}]\n" + "─" * 30
            body = "\n".join(events_to_show)
            text = f"{header}\n{body}"

            masked = mask_secrets(text)
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=live_message_id,
                    text=masked,
                    parse_mode="Markdown"
                )
            except TelegramError as e:
                if "Message is not modified" not in str(e):
                    logger.debug(f"Telegram edit error: {e}")

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
            # 1. Automatic checkpoint before task
            GitManager.create_checkpoint(project_dir, prompt)

            if not agy_bin:
                # Fallback if agy is not installed: run as shell bash prompt safely
                display_lines.append("⚠️ `agy` binary cloud machine par install ho raha hai...")
                await update_telegram()
                
                # Attempt to install agy via official curl
                proc = await asyncio.create_subprocess_shell(
                    "curl -fsSL https://antigravity.google/cli/install.sh | bash",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await proc.communicate()
                agy_bin = self.find_agy_binary()

            if not agy_bin:
                # If still unavailable, run standard command or report clearly
                display_lines.append("❌ `agy` binary available nahi hai. Direct shell runner active hai.")
                await update_telegram(final=True)
                return {"status": "failed", "error": "agy binary not found"}

            # Build agy command line
            cmd = [
                agy_bin,
                "-p", prompt,
                "--dangerously-skip-permissions",
                "--output-format", "stream-json"
            ]

            if conversation_id:
                cmd.extend(["--conversation", conversation_id])

            env = os.environ.copy()
            if config.GEMINI_API_KEY:
                env["GEMINI_API_KEY"] = config.GEMINI_API_KEY

            logger.info(f"Running command: {' '.join(cmd)} in {project_dir}")
            
            self.current_process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(project_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env
            )

            # Read stream NDJSON line by line
            final_response = ""
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
                    
                    if ev_type == "step_update":
                        su = event.get("step_update", {})
                        stype = su.get("step_type")
                        
                        if stype == "agent_response":
                            dur = su.get("duration_seconds", 0)
                            delta = su.get("text_delta", "")
                            if dur > 0:
                                display_lines.append(f"🧠 Thought ({int(dur)}s)")
                            elif delta and "\n" in delta:
                                display_lines.append(f"💭 {delta.strip()[:60]}")
                                
                        elif stype == "tool":
                            tool_name = su.get("tool_name", "")
                            params = su.get("tool_info", {}).get("parameters", {})
                            tstate = su.get("state")
                            
                            if tool_name == "run_command":
                                cl = params.get("CommandLine", "")[:45]
                                if tstate == "ACTIVE":
                                    display_lines.append(f"⚡ Bash: `{cl}`")
                                elif tstate == "DONE":
                                    display_lines.append(f"✅ Bash complete: `{cl}`")
                            elif tool_name in ["read_file", "view_file"]:
                                p = params.get("Path", "")
                                display_lines.append(f"📖 Read: `{os.path.basename(p)}`")
                            elif tool_name in ["edit_file", "replace_file_content", "write_to_file"]:
                                p = params.get("Path", "")
                                display_lines.append(f"✏️ Edit: `{os.path.basename(p)}`")
                            elif tool_name == "search_web":
                                q = params.get("query", "")[:35]
                                display_lines.append(f"🌐 Search: `{q}`")
                            else:
                                display_lines.append(f"🔧 Tool: `{tool_name}`")
                                
                        await update_telegram()
                        
                    elif ev_type == "result":
                        res = event.get("result", {})
                        final_response = res.get("response", "")
                        
                except json.JSONDecodeError:
                    if len(line_str) > 0 and not line_str.startswith("{"):
                        display_lines.append(f"ℹ️ {line_str[:55]}")
                        await update_telegram()

            await self.current_process.wait()
            return_code = self.current_process.returncode

            elapsed_sec = int(time.time() - start_time)
            mins, secs = divmod(elapsed_sec, 60)
            time_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"

            if self.cancel_requested:
                display_lines.append("🛑 Kaam user ne rok diya (/stop).")
                await update_telegram(final=True)
                return {"status": "stopped", "time": time_str}

            if return_code == 0:
                display_lines.append(f"✅ Finished in {time_str}")
                await update_telegram(final=True)
                
                # Auto commit & push if successful
                commit_ok, commit_msg = GitManager.auto_commit_and_push(project_dir, prompt[:50])

                # Backup workspace snapshot to Telegram Storage Channel
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
                    "response": final_response,
                    "git_status": commit_msg
                }
            else:
                display_lines.append(f"❌ Kaam fail hua (Code {return_code})")
                await update_telegram(final=True)
                return {"status": "failed", "code": return_code, "time": time_str}

        except Exception as e:
            logger.exception("Error running task")
            display_lines.append(f"❌ Error: {str(e)[:100]}")
            await update_telegram(final=True)
            return {"status": "error", "error": str(e)}

        finally:
            self.is_running = False
            self.current_process = None
            keepalive_task.cancel()

    def stop_current_task(self) -> bool:
        """Cancels running agy execution."""
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
