
import asyncio
import os
import re
import sys
import uuid
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
# Apna Telegram user ID daalein (jaise {123456789}); khali chhodne par sabko allow karega
ALLOWED_USER_IDS: set[int] = set()

BASE_DIR = Path("uploaded_bots")
BASE_DIR.mkdir(exist_ok=True)

dp = Dispatcher()
running_procs: dict[str, asyncio.subprocess.Process] = {}
bots_registry: dict[str, dict] = {}


def is_allowed(user_id: int | None) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    return user_id in ALLOWED_USER_IDS


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return cleaned or "script.py"


def bot_action_keyboard(bot_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="▶ Run Now", callback_data=f"run:{bot_id}"),
                InlineKeyboardButton(text="🤖 My Bots", callback_data="my_bots"),
            ]
        ]
    )


@dp.message(CommandStart())
async def start_handler(message: Message) -> None:
    if not is_allowed(message.from_user.id if message.from_user else None):
        await message.answer("Aapko is bot ko use karne ki permission nahi hai.")
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🤖 My Bots", callback_data="my_bots")]
        ]
    )
    await message.answer(
        "Mujhe koi bhi <b>.py</b> file bhejein. Upload ke baad aap <b>Run Now</b> se usse chala sakenge.",
        parse_mode="HTML",
        reply_markup=kb,
    )


@dp.message(F.document)
async def document_handler(message: Message, bot: Bot) -> None:
    user_id = message.from_user.id if message.from_user else None
    if not is_allowed(user_id):
        await message.answer("Aapko is bot ko use karne ki permission nahi hai.")
        return

    doc = message.document
    if not doc or not doc.file_name or not doc.file_name.lower().endswith(".py"):
        await message.answer("Kripya sirf ek <b>.py</b> file bhejein.", parse_mode="HTML")
        return

    bot_id = uuid.uuid4().hex[:24]
    filename = safe_filename(doc.file_name)
    bot_folder = BASE_DIR / bot_id
    bot_folder.mkdir(parents=True, exist_ok=True)
    file_path = bot_folder / filename

    tg_file = await bot.get_file(doc.file_id)
    await bot.download_file(tg_file.file_path, destination=file_path)

    size_kb = round((doc.file_size or file_path.stat().st_size) / 1024, 1)
    bots_registry[bot_id] = {
        "owner_id": user_id,
        "filename": filename,
        "path": str(file_path),
        "size_kb": size_kb,
        "status": "Uploaded",
    }

    text = (
        "✅ <b>FILE UPLOADED!</b>\n\n"
        f"📄 <code>{filename}</code>\n"
        f"🆔 Bot ID: <code>#{bot_id}</code>\n"
        "🐍 Python\n"
        f"📦 Size: {size_kb} KB"
    )
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=bot_action_keyboard(bot_id),
    )


async def run_with_retries(status_msg: Message, bot_id: str, max_attempts: int = 3) -> None:
    info = bots_registry.get(bot_id)
    if not info:
        await status_msg.edit_text("❌ Bot record nahi mila.")
        return

    filename = info["filename"]
    file_path = info["path"]
    size_kb = info["size_kb"]

    old_proc = running_procs.get(bot_id)
    if old_proc and old_proc.returncode is None:
        old_proc.terminate()
        try:
            await asyncio.wait_for(old_proc.wait(), timeout=3)
        except asyncio.TimeoutError:
            old_proc.kill()

    for attempt in range(1, max_attempts + 1):
        info["status"] = f"Starting (Attempt {attempt}/{max_attempts})"
        await status_msg.edit_text(
            "🚀 <b>Starting Bot...</b>\n\n"
            f"📄 <code>{filename}</code>\n"
            "🐍 Python\n"
            f"🔄 Attempt: {attempt}/{max_attempts}",
            parse_mode="HTML",
        )

        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            file_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        running_procs[bot_id] = proc

        await asyncio.sleep(2.0)

        if proc.returncode is None:
            info["status"] = "Running"
            await status_msg.edit_text(
                "🟢 <b>Bot Live / Running!</b>\n\n"
                f"📄 <code>{filename}</code>\n"
                f"🆔 Bot ID: <code>#{bot_id}</code>\n"
                f"📦 Size: {size_kb} KB\n"
                f"🔄 Attempt: {attempt}/{max_attempts}",
                parse_mode="HTML",
                reply_markup=bot_action_keyboard(bot_id),
            )
            return

        _, stderr_data = await proc.communicate()
        err_text = stderr_data.decode(errors="replace").strip()
        last_line = err_text.splitlines()[-1] if err_text else "Process exited early"

        if attempt < max_attempts:
            await status_msg.edit_text(
                "⚠️ <b>Bot turant band ho gaya, dobara try kar raha hai...</b>\n\n"
                f"📄 <code>{filename}</code>\n"
                f"🔄 Attempt: {attempt}/{max_attempts}\n"
                f"❌ Error: <code>{last_line[:180]}</code>",
                parse_mode="HTML",
            )
            await asyncio.sleep(1.5)
        else:
            info["status"] = "Failed"
            await status_msg.edit_text(
                "❌ <b>Bot start nahi ho saka.</b>\n\n"
                f"📄 <code>{filename}</code>\n"
                f"🔄 Attempt: {attempt}/{max_attempts}\n"
                f"❌ Error: <code>{last_line[:250]}</code>",
                parse_mode="HTML",
                reply_markup=bot_action_keyboard(bot_id),
            )


@dp.callback_query(F.data.startswith("run:"))
async def run_callback_handler(callback: CallbackQuery) -> None:
    if not is_allowed(callback.from_user.id):
        await callback.answer("Permission nahi hai.", show_alert=True)
        return

    bot_id = callback.data.split(":", 1)[1]
    if bot_id not in bots_registry:
        await callback.answer("Bot nahi mila.", show_alert=True)
        return

    await callback.answer("Bot start ho raha hai...")
    asyncio.create_task(run_with_retries(callback.message, bot_id))


@dp.callback_query(F.data == "my_bots")
async def my_bots_handler(callback: CallbackQuery) -> None:
    if not is_allowed(callback.from_user.id):
        await callback.answer("Permission nahi hai.", show_alert=True)
        return

    user_bots = [
        (bid, data)
        for bid, data in bots_registry.items()
        if data["owner_id"] == callback.from_user.id
    ]
    if not user_bots:
        await callback.answer("Abhi koi bot upload nahi hua hai.", show_alert=True)
        return

    lines = ["🤖 <b>Aapke Bots:</b>\n"]
    for bid, data in user_bots[-10:]:
        lines.append(
            f"• <code>{data['filename']}</code> (<code>#{bid[:8]}</code>) — <b>{data['status']}</b>"
        )

    await callback.answer()
    await callback.message.answer("\n".join(lines), parse_mode="HTML")


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable set nahi hai.")
    bot = Bot(token=BOT_TOKEN)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
  
