import asyncio
import os
import sqlite3
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

BOT_TOKEN = os.getenv(
    "BOT_TOKEN",
    "9161726440:AAEnKjg4XZuhO-nmIKKUEKiKrA8gBMRIt9A",
).strip()
# Apna Telegram user ID daalein (jaise {123456789}); khali rakhne par sabko allow karega
ALLOWED_USER_IDS: set[int] = set()

DB_PATH = Path("file_vault.db")

dp = Dispatcher()
pending_save: dict[int, dict] = {}
pending_edit_name: dict[int, str] = {}


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS saved_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                file_id TEXT NOT NULL,
                file_type TEXT NOT NULL,
                UNIQUE(owner_id, name)
            )
            """
        )
        conn.commit()


def is_allowed(user_id: int | None) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    return user_id in ALLOWED_USER_IDS


def extract_file_info(message: Message) -> tuple[str, str] | None:
    if message.document:
        return message.document.file_id, "document"
    if message.photo:
        return message.photo[-1].file_id, "photo"
    if message.video:
        return message.video.file_id, "video"
    if message.audio:
        return message.audio.file_id, "audio"
    if message.voice:
        return message.voice.file_id, "voice"
    return None


def upsert_file(owner_id: int, name: str, file_id: str, file_type: str) -> None:
    clean_name = name.strip()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO saved_files (owner_id, name, file_id, file_type)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(owner_id, name) DO UPDATE SET
                file_id = excluded.file_id,
                file_type = excluded.file_type
            """,
            (owner_id, clean_name, file_id, file_type),
        )
        conn.commit()


def search_files(owner_id: int, query: str) -> list[tuple[int, str, str]]:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            """
            SELECT id, name, file_type FROM saved_files
            WHERE owner_id = ? AND LOWER(name) LIKE LOWER(?)
            ORDER BY name ASC
            LIMIT 15
            """,
            (owner_id, f"%{query.strip()}%"),
        )
        return cur.fetchall()


def get_file_by_id(owner_id: int, row_id: int) -> tuple[int, str, str, str] | None:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            """
            SELECT id, name, file_id, file_type FROM saved_files
            WHERE owner_id = ? AND id = ?
            """,
            (owner_id, row_id),
        )
        return cur.fetchone()


def rename_file(owner_id: int, old_name: str, new_name: str) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            """
            UPDATE saved_files SET name = ?
            WHERE owner_id = ? AND LOWER(name) = LOWER(?)
            """,
            (new_name.strip(), owner_id, old_name.strip()),
        )
        conn.commit()
        return cur.rowcount > 0


async def send_stored_file(
    message: Message, file_id: str, file_type: str, caption: str
) -> None:
    if file_type == "photo":
        await message.answer_photo(photo=file_id, caption=caption, parse_mode="HTML")
    elif file_type == "video":
        await message.answer_video(video=file_id, caption=caption, parse_mode="HTML")
    elif file_type == "audio":
        await message.answer_audio(audio=file_id, caption=caption, parse_mode="HTML")
    elif file_type == "voice":
        await message.answer_voice(voice=file_id, caption=caption, parse_mode="HTML")
    else:
        await message.answer_document(document=file_id, caption=caption, parse_mode="HTML")


@dp.message(CommandStart())
async def start_handler(message: Message) -> None:
    if not is_allowed(message.from_user.id if message.from_user else None):
        await message.answer("Permission nahi hai.")
        return

    text = (
        "📁 <b>Personal File Vault Bot</b>\n\n"
        "• <b>Save:</b> <code>/save apna_naam</code> likhein aur file bhejein (ya file ke caption mein naam likhein).\n"
        "• <b>Search & Download:</b> <code>/find naam</code> likhein.\n"
        "• <b>Rename:</b> <code>/rename purana_naam | naya_naam</code>\n"
        "• <b>Edit / Replace file:</b> <code>/edit naam</code> likhein aur nayi file bhej dein."
    )
    await message.answer(text, parse_mode="HTML")


@dp.message(Command("save"))
async def save_cmd_handler(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else None
    if not is_allowed(user_id):
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer("Use: <code>/save file_ka_naam</code>", parse_mode="HTML")
        return

    file_name = parts[1].strip()
    pending_save[user_id] = {"name": file_name}
    await message.answer(
        f"Ab woh file bhejein jise <b>{file_name}</b> naam se save karna hai.",
        parse_mode="HTML",
    )


@dp.message(Command("edit"))
async def edit_cmd_handler(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else None
    if not is_allowed(user_id):
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer("Use: <code>/edit saved_file_ka_naam</code>", parse_mode="HTML")
        return

    target_name = parts[1].strip()
    pending_edit_name[user_id] = target_name
    await message.answer(
        f"<b>{target_name}</b> ko replace karne ke liye nayi file bhejein.",
        parse_mode="HTML",
    )


@dp.message(Command("rename"))
async def rename_cmd_handler(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else None
    if not is_allowed(user_id):
        return

    raw = (message.text or "").split(maxsplit=1)
    if len(raw) < 2 or "|" not in raw[1]:
        await message.answer(
            "Use: <code>/rename purana_naam | naya_naam</code>",
            parse_mode="HTML",
        )
        return

    old_name, new_name = [x.strip() for x in raw[1].split("|", 1)]
    if not old_name or not new_name:
        await message.answer("Dono naam sahi se likhein.", parse_mode="HTML")
        return

    ok = rename_file(user_id, old_name, new_name)
    if ok:
        await message.answer(
            f"✅ Renamed: <code>{old_name}</code> ➡️ <code>{new_name}</code>",
            parse_mode="HTML",
        )
    else:
        await message.answer("❌ Is naam se koi saved file nahi mili.")


@dp.message(Command("find", "search"))
async def find_cmd_handler(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else None
    if not is_allowed(user_id):
        return

    parts = (message.text or "").split(maxsplit=1)
    query = parts[1].strip() if len(parts) > 1 else ""
    matches = search_files(user_id, query)

    if not matches:
        await message.answer("🔍 Koi matching file nahi mili.")
        return

    buttons = [
        [InlineKeyboardButton(text=f"📄 {name} (Download)", callback_data=f"dl:{row_id}")]
        for row_id, name, _ in matches
    ]
    await message.answer(
        "🔎 <b>Matching Files:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@dp.callback_query(F.data.startswith("dl:"))
async def download_callback(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    if not is_allowed(user_id):
        await callback.answer("Permission nahi hai.", show_alert=True)
        return

    row_id = int(callback.data.split(":", 1)[1])
    row = get_file_by_id(user_id, row_id)
    if not row:
        await callback.answer("File nahi mili.", show_alert=True)
        return

    _, name, file_id, file_type = row
    await callback.answer("Sending file...")
    await send_stored_file(
        callback.message,
        file_id=file_id,
        file_type=file_type,
        caption=f"📦 <b>{name}</b>",
    )


@dp.message(F.document | F.photo | F.video | F.audio | F.voice)
async def file_receive_handler(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else None
    if not is_allowed(user_id):
        return

    info = extract_file_info(message)
    if not info:
        return

    file_id, file_type = info

    if user_id in pending_edit_name:
        target_name = pending_edit_name.pop(user_id)
        upsert_file(user_id, target_name, file_id, file_type)
        await message.answer(
            f"✏️ File updated: <b>{target_name}</b>",
            parse_mode="HTML",
        )
        return

    if user_id in pending_save:
        custom_name = pending_save.pop(user_id)["name"]
        upsert_file(user_id, custom_name, file_id, file_type)
        await message.answer(
            f"✅ Saved as <b>{custom_name}</b>",
            parse_mode="HTML",
        )
        return

    if message.caption and message.caption.strip():
        cap_name = message.caption.strip()
        upsert_file(user_id, cap_name, file_id, file_type)
        await message.answer(
            f"✅ Saved as <b>{cap_name}</b>",
            parse_mode="HTML",
        )
        return

    await message.answer(
        "File mil gayi! Isko kisi naam se save karne ke liye likhein: <code>/save apna_naam</code>",
        parse_mode="HTML",
    )


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN set nahi hai.")
    init_db()
    bot = Bot(token=BOT_TOKEN)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
