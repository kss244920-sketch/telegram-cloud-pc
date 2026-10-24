import os
import json
import asyncio
import logging
import shutil
from pathlib import Path
import aiohttp

import config
from state import state
from security import mask_secrets

logger = logging.getLogger("chat_service")

class ChatService:
    def __init__(self):
        pass

    def find_agy_binary(self) -> str | None:
        """Finds agy binary on host."""
        candidates = [
            shutil.which("agy"),
            "/usr/local/bin/agy",
            "/home/piyush/.local/bin/agy",
            str(Path.home() / ".local" / "bin" / "agy"),
            "/app/bin/agy"
        ]
        for c in candidates:
            if c and os.path.exists(c) and os.access(c, os.X_OK):
                return c
        return None

    async def generate_reply(
        self,
        prompt: str,
        conv_id: str | None = None,
        project_dir: Path | None = None
    ) -> str:
        """
        Generates a direct text-to-text response for chat mode.
        Tries Gemini API first if GEMINI_API_KEY is configured.
        Otherwise falls back to `agy -p` CLI runner.
        """
        conv_id = conv_id or state.active_conversation_id
        project_dir = project_dir or state.get_project_dir()

        # Add user message to state history
        state.add_chat_message(conv_id, "user", prompt)

        # 1. Try Gemini API if key is available
        if config.GEMINI_API_KEY:
            try:
                gemini_reply = await self._call_gemini_api(prompt, conv_id, project_dir)
                if gemini_reply:
                    masked = mask_secrets(gemini_reply)
                    state.add_chat_message(conv_id, "model", masked)
                    return masked
            except Exception as e:
                logger.warning(f"Direct Gemini API call failed, falling back to agy CLI: {e}")

        # 2. Call agy CLI in print mode
        reply = await self._call_agy_cli(prompt, conv_id, project_dir)
        masked = mask_secrets(reply)
        state.add_chat_message(conv_id, "model", masked)
        return masked

    async def _call_gemini_api(self, prompt: str, conv_id: str, project_dir: Path) -> str | None:
        """Calls Gemini REST API directly using configured GEMINI_API_KEY."""
        # Gather workspace context
        files_summary = []
        try:
            for p in list(project_dir.iterdir())[:20]:
                if not p.name.startswith("."):
                    files_summary.append(p.name + ("/" if p.is_dir() else ""))
        except Exception:
            pass

        workspace_info = f"Project: {project_dir.name}\nFiles in workspace: {', '.join(files_summary) if files_summary else 'empty'}"

        system_instruction = (
            "You are Telegram Cloud PC AI Assistant. You help the user manage their cloud workspace, "
            "inspect and write code, debug issues, and answer questions. Answer concisely in clean, helpful "
            "Hinglish or English. Format code with markdown code blocks.\n"
            f"Current Workspace Context:\n{workspace_info}"
        )

        history = state.get_chat_messages(conv_id)
        contents = []
        # Include past 10 turns
        for m in history[-10:-1]:  # exclude the last prompt which was just added
            role = "user" if m.get("role") == "user" else "model"
            text = m.get("text", "")
            if text:
                contents.append({"role": role, "parts": [{"text": text}]})

        contents.append({"role": "user", "parts": [{"text": prompt}]})

        # Try models in order of preference
        models = ["gemini-2.5-flash", "gemini-1.5-flash", "gemini-2.0-flash"]
        async with aiohttp.ClientSession() as session:
            for model_name in models:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={config.GEMINI_API_KEY}"
                payload = {
                    "system_instruction": {"parts": [{"text": system_instruction}]},
                    "contents": contents,
                    "generationConfig": {
                        "temperature": 0.7,
                        "maxOutputTokens": 2048,
                    }
                }
                try:
                    async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            candidates = data.get("candidates", [])
                            if candidates:
                                parts = candidates[0].get("content", {}).get("parts", [])
                                text = "".join(p.get("text", "") for p in parts)
                                if text.strip():
                                    return text.strip()
                        else:
                            err_text = await resp.text()
                            logger.debug(f"Model {model_name} returned status {resp.status}: {err_text}")
                except Exception as e:
                    logger.debug(f"Gemini API attempt with {model_name} failed: {e}")
                    continue

        return None

    async def _call_agy_cli(self, prompt: str, conv_id: str, project_dir: Path) -> str:
        """Runs agy in print (-p) mode."""
        agy_bin = self.find_agy_binary()
        if not agy_bin:
            return (
                "⚠️ `agy` CLI binary cloud machine par abhi available nahi hai aur GEMINI_API_KEY set nahi hai.\n\n"
                "👉 Kripya chat mein `/login` bhej kar Google account se login karein ya `/key <API_KEY>` provide karein."
            )

        cmd = [
            agy_bin,
            "-p", prompt,
            "--dangerously-skip-permissions"
        ]
        if conv_id:
            cmd.extend(["--conversation", conv_id])

        cwd = str(project_dir) if project_dir.exists() else str(config.WORKSPACE_DIR)

        env = os.environ.copy()
        if config.GEMINI_API_KEY:
            env["GEMINI_API_KEY"] = config.GEMINI_API_KEY
        env["PAGER"] = "cat"

        try:
            logger.info(f"Running agy chat prompt: {prompt[:60]} (conv: {conv_id})")
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=90.0)
            output = stdout.decode("utf-8", errors="ignore").strip()
            if not output:
                output = stderr.decode("utf-8", errors="ignore").strip()

            if not output:
                return "✅ Command execute hua lekin koi output return nahi hua."

            # If output has auth error
            if "login" in output.lower() or "not logged in" in output.lower() or "unauthenticated" in output.lower():
                return (
                    f"⚠️ Google Antigravity Auth zaroori hai:\n\n{output}\n\n"
                    "👉 Chat mein `/login` bhej kar login karein ya `/key <GEMINI_API_KEY>` set karein."
                )

            return output
        except asyncio.TimeoutError:
            return "⏳ AI response aane mein zyada samay lag gaya (Timeout 90s). Kripya dobara try karein."
        except Exception as e:
            logger.error(f"Failed to execute agy -p: {e}")
            return f"❌ Error: {str(e)}"

chat_service = ChatService()
