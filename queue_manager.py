import asyncio
import uuid
import logging
from datetime import datetime
from pathlib import Path
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

import config
from runner import runner
from state import state

logger = logging.getLogger("queue_manager")

class QueueManager:
    def __init__(self):
        self.is_processing = False
        self.next_round_items = []
        self.active_message_id: int | None = None
        self.active_chat_id: int | None = None

    def add_item(self, title: str, item_type: str = "text", file_path: str | None = None, caption: str = "") -> dict:
        item = {
            "id": str(uuid.uuid4())[:8],
            "title": title,
            "type": item_type,
            "file_path": file_path,
            "caption": caption,
            "status": "pending",  # pending, running, done, failed
            "error_msg": "",
            "duration": "",
            "created_at": datetime.utcnow().isoformat(),
        }

        # If already processing a PROCEED run, add to next round
        if self.is_processing:
            self.next_round_items.append(item)
            return {"item": item, "is_next_round": True}

        state.queue.append(item)
        state.save()
        return {"item": item, "is_next_round": False}

    def clear(self):
        state.queue = []
        self.next_round_items = []
        state.save()

    def remove_item(self, item_id: str) -> bool:
        initial_len = len(state.queue)
        state.queue = [x for x in state.queue if x["id"] != item_id]
        state.save()
        return len(state.queue) < initial_len

    def move_item(self, item_id: str, direction: str) -> bool:
        """Move item up or down in queue."""
        q = state.queue
        for i, item in enumerate(q):
            if item["id"] == item_id:
                if direction == "up" and i > 0:
                    q[i], q[i - 1] = q[i - 1], q[i]
                    state.save()
                    return True
                elif direction == "down" and i < len(q) - 1:
                    q[i], q[i + 1] = q[i + 1], q[i]
                    state.save()
                    return True
        return False

    def build_queue_text_and_markup(self, is_edit_mode: bool = False) -> tuple[str, InlineKeyboardMarkup]:
        q = state.queue
        count = len(q)
        lines = [f"📋 **Kaam ki list ({count} items)**\n"]

        if not q:
            lines.append("_Abhi list khali hai. Kuch bhi bhein (message, file, link) aur wo yahan add ho jayega._")
            buttons = []
            if self.next_round_items:
                lines.append(f"\n⏳ **Agle round ke liye:** {len(self.next_round_items)} items ready hain.")
                buttons.append([InlineKeyboardButton("▶️ Agle Round Ko Add Karo", callback_data="q_pull_next")])
            return "\n".join(lines), InlineKeyboardMarkup(buttons)

        buttons = []
        for i, item in enumerate(q, 1):
            status_icon = {
                "pending": "",
                "running": "⏳ ",
                "done": "✅ ",
                "failed": "❌ ",
            }.get(item["status"], "")

            dur = f" ({item['duration']})" if item.get("duration") else ""
            lines.append(f"{i}. {status_icon}\"{item['title']}\"{dur}")

            if is_edit_mode:
                row = [
                    InlineKeyboardButton(f"❌ #{i}", callback_data=f"q_del:{item['id']}"),
                    InlineKeyboardButton("⬆️", callback_data=f"q_up:{item['id']}"),
                    InlineKeyboardButton("⬇️", callback_data=f"q_down:{item['id']}"),
                    InlineKeyboardButton("⚡ Chalao", callback_data=f"q_run_one:{item['id']}"),
                ]
                buttons.append(row)

        if not is_edit_mode:
            buttons.append([
                InlineKeyboardButton("▶️ PROCEED: Sab Kuch Chalao", callback_data="q_proceed")
            ])
            buttons.append([
                InlineKeyboardButton("✏️ Edit List", callback_data="q_edit_toggle"),
                InlineKeyboardButton("🗑️ Clear List", callback_data="q_clear"),
            ])
            buttons.append([
                InlineKeyboardButton("💬 Txt Chat Mode", callback_data="switch_mode:chat")
            ])
        else:
            buttons.append([
                InlineKeyboardButton("🔙 Done Editing", callback_data="q_edit_toggle")
            ])

        return "\n".join(lines), InlineKeyboardMarkup(buttons)

    async def process_queue(self, bot: Bot, chat_id: int, project_dir: Path):
        """Processes all items in the queue sequentially."""
        if self.is_processing:
            return "Pehle se ek run chal raha hai! Duplicate run block kiya gaya."

        if not state.queue:
            return "Queue khali hai! Pehle kuch kaam bhejein."

        self.is_processing = True
        total = len(state.queue)
        completed_count = 0
        failed_count = 0
        start_time = datetime.utcnow()

        # Send initial status message that will be live edited
        status_msg = await bot.send_message(
            chat_id=chat_id,
            text=f"🔄 **Kaam shuru ho raha hai... (0/{total})**",
            parse_mode="Markdown"
        )
        self.active_message_id = status_msg.message_id
        self.active_chat_id = chat_id

        summary_details = []

        try:
            for index, item in enumerate(state.queue, 1):
                if runner.cancel_requested:
                    item["status"] = "failed"
                    item["error_msg"] = "Cancelled by user"
                    break

                item["status"] = "running"
                task_start = datetime.utcnow()

                # Build prompt for agy
                task_prompt = item["title"]
                if item.get("file_path"):
                    task_prompt += f"\nFile location: {item['file_path']}"
                if item.get("caption"):
                    task_prompt += f"\nUser Instruction: {item['caption']}"

                # Update status message
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_msg.message_id,
                    text=f"⚙️ **Chal raha hai ({index}/{total})**: `{item['title'][:40]}`\n\nLive logs neeche stream ho rahe hain...",
                    parse_mode="Markdown"
                )

                # Execute through runner
                res = await runner.run_task(
                    prompt=task_prompt,
                    project_dir=project_dir,
                    bot=bot,
                    chat_id=chat_id,
                    live_message_id=status_msg.message_id,
                    conversation_id=state.active_conversation_id
                )

                task_dur = res.get("time", "few sec")
                item["duration"] = task_dur

                if res.get("status") == "success":
                    item["status"] = "done"
                    completed_count += 1
                    summary_details.append(f"✅ {item['title']} ({task_dur})")
                else:
                    item["status"] = "failed"
                    failed_count += 1
                    err = res.get("error", "Unknown error")
                    summary_details.append(f"❌ {item['title']} ({task_dur}) - {err[:50]}")

            state.save()

            # Final summary message
            total_elapsed = datetime.utcnow() - start_time
            mins, secs = divmod(int(total_elapsed.total_seconds()), 60)
            elapsed_str = f"{mins} min {secs} sec" if mins > 0 else f"{secs} sec"

            final_text = (
                f"🎉 **Kaam Khatam Hua! ({completed_count}/{total} items, {elapsed_str})**\n\n"
                "**Details:**\n" + "\n".join(summary_details) + "\n\n"
            )

            # Check if new items were queued during the run
            if self.next_round_items:
                state.queue = self.next_round_items
                self.next_round_items = []
                state.save()
                final_text += f"🔔 Run ke dauran {len(state.queue)} naye items queue me jud gaye!\nUnhe chalane ke liye `/queue` ya PROCEED dabayein."
            else:
                # Clear finished queue items
                state.queue = []
                state.save()

            await bot.send_message(
                chat_id=chat_id,
                text=final_text,
                parse_mode="Markdown"
            )

        finally:
            self.is_processing = False
            self.active_message_id = None
            self.active_chat_id = None

queue_manager = QueueManager()
