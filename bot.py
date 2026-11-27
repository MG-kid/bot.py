
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

ADMIN_ID = int(os.getenv("ADMIN_ID", "8372837217"))

DB_PATH = Path("file_vault.db")

dp = Dispatcher()

# User states: waiting_name | waiting_save_name | waiting_save_file | waiting_edit_name | waiting_edit_file | waiting_rename | waiting_find
user_state: dict[int, dict] = {}


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                full_name TEXT NOT NULL,
                username TEXT,
                status TEXT NOT NULL DEFAULT 'pending'
            )
            """
        )
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


def is_admin(user_id: int | None) -> bool:
    return bool(user_id and ADMIN_ID and user_id == ADMIN_ID)


def get_user(user_id: int) -> tuple[int, str, str | None, str] | None:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT user_id, full_name, username, status FROM users WHERE user_id = ?",
            (user_id,),
        )
        return cur.fetchone()


def upsert_pending_user(user_id: int, full_name: str, username: str | None) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO users (user_id, full_name, username, status)
            VALUES (?, ?, ?, 'pending')
            ON CONFLICT(user_id) DO UPDATE SET
                full_name = excluded.full_name,
                username = excluded.username,
                status = CASE WHEN users.status = 'approved' THEN 'approved' ELSE 'pending' END
            """,
            (user_id, full_name.strip(), username),
        )
        conn.commit()


def set_user_status(user_id: int, status: str) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE users SET status = ? WHERE user_id = ?",
            (status, user_id),
        )
        conn.commit()


def get_all_users() -> list[tuple[int, str, str | None, str, int]]:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            """
            SELECT
                u.user_id,
                u.full_name,
                u.username,
                u.status,
                COUNT(f.id) AS file_count
            FROM users u
            LEFT JOIN saved_files f ON f.owner_id = u.user_id
            GROUP BY u.user_id, u.full_name, u.username, u.status
            ORDER BY u.user_id ASC
            """
        )
        return cur.fetchall()


def is_approved_user(user_id: int | None) -> bool:
    if not user_id:
        return False
    if is_admin(user_id):
        return True
    row = get_user(user_id)
    return bool(row and row[3] == "approved")


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


def search_files(owner_id: int, query: str = "") -> list[tuple[int, str, str]]:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            """
            SELECT id, name, file_type FROM saved_files
            WHERE owner_id = ? AND LOWER(name) LIKE LOWER(?)
            ORDER BY name ASC
            LIMIT 20
            """,
            (owner_id, f"%{query.strip()}%"),
        )
        return cur.fetchall()


def get_file_by_id(row_id: int, owner_id: int | None = None) -> tuple[int, int, str, str, str] | None:
    with sqlite3.connect(DB_PATH) as conn:
        if owner_id is not None:
            cur = conn.execute(
                "SELECT id, owner_id, name, file_id, file_type FROM saved_files WHERE id = ? AND owner_id = ?",
                (row_id, owner_id),
            )
        else:
            cur = conn.execute(
                "SELECT id, owner_id, name, file_id, file_type FROM saved_files WHERE id = ?",
                (row_id,),
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


def delete_file_by_id(row_id: int) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute("DELETE FROM saved_files WHERE id = ?", (row_id,))
        conn.commit()
        return cur.rowcount > 0


def main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="➕ Save File", callback_data="act:save"),
            InlineKeyboardButton(text="🔎 Find / Download", callback_data="act:find"),
        ],
        [
            InlineKeyboardButton(text="✏️ Edit / Replace", callback_data="act:edit"),
            InlineKeyboardButton(text="🏷️ Rename", callback_data="act:rename"),
        ],
        [
            InlineKeyboardButton(text="📂 My Saved Files", callback_data="act:myfiles"),
        ],
    ]
    if is_admin(user_id):
        rows.append(
            [InlineKeyboardButton(text="🛡️ Admin Panel", callback_data="adm:panel")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Users & Saved Files", callback_data="adm:users")],
            [InlineKeyboardButton(text="🏠 Main Menu", callback_data="act:menu")],
        ]
    )


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


async def send_main_menu(target_message: Message, user_id: int, title: str = "📁 <b>File Vault Menu</b>") -> None:
    await target_message.answer(
        title,
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(user_id),
    )


@dp.message(CommandStart())
async def start_handler(message: Message) -> None:
    if not message.from_user:
        return

    user_id = message.from_user.id
    user_state.pop(user_id, None)

    if is_admin(user_id):
        await send_main_menu(message, user_id, "🛡️ <b>Admin Account Active</b>\nNeeche se option chunein:")
        return

    row = get_user(user_id)
    if row and row[3] == "approved":
        await send_main_menu(
            message,
            user_id,
            f"👋 Welcome back, <b>{row[1]}</b>!\nNeeche buttons se apna kaam karein:",
        )
        return

    if row and row[3] == "pending":
        await message.answer(
            "⏳ Aapki approval request already admin ko bheji ja chuki hai. Approval milne ka wait karein."
        )
        return

    user_state[user_id] = {"step": "waiting_name"}
    await message.answer(
        "👋 Namaste! Bot use karne ke liye pehle account banayein.\n\n"
        "Kripya apna <b>poora naam</b> likh kar bhejein:",
        parse_mode="HTML",
    )


@dp.message(Command("admin"))
async def admin_cmd_handler(message: Message) -> None:
    if not message.from_user or not is_admin(message.from_user.id):
        return
    await message.answer(
        "🛡️ <b>Admin Panel</b>\nYahan se aap users aur unki saved files manage kar sakte hain:",
        parse_mode="HTML",
        reply_markup=admin_panel_keyboard(),
    )


# ---------------- APPROVAL CALLBACKS (ADMIN) ----------------
@dp.callback_query(F.data.startswith("appr:"))
async def approve_user_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or not is_admin(callback.from_user.id):
        await callback.answer("Sirf admin ke liye.", show_alert=True)
        return

    target_user_id = int(callback.data.split(":", 1)[1])
    set_user_status(target_user_id, "approved")
    row = get_user(target_user_id)
    name = row[1] if row else str(target_user_id)

    if callback.message:
        await callback.message.edit_text(
            f"✅ Approved: <b>{name}</b> (<code>{target_user_id}</code>)",
            parse_mode="HTML",
        )

    try:
        await callback.bot.send_message(
            target_user_id,
            "🎉 Aapka account approve ho gaya hai! Ab aap bot use kar sakte hain.",
            reply_markup=main_menu_keyboard(target_user_id),
        )
    except Exception:
        pass

    await callback.answer("User approved!")


@dp.callback_query(F.data.startswith("rej:"))
async def reject_user_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or not is_admin(callback.from_user.id):
        await callback.answer("Sirf admin ke liye.", show_alert=True)
        return

    target_user_id = int(callback.data.split(":", 1)[1])
    set_user_status(target_user_id, "rejected")
    row = get_user(target_user_id)
    name = row[1] if row else str(target_user_id)

    if callback.message:
        await callback.message.edit_text(
            f"❌ Rejected: <b>{name}</b> (<code>{target_user_id}</code>)",
            parse_mode="HTML",
        )

    try:
        await callback.bot.send_message(
            target_user_id,
            "❌ Maaf kijiye, admin ne aapki request reject kar di hai.",
        )
    except Exception:
        pass

    await callback.answer("User rejected.")


# ---------------- ADMIN PANEL CALLBACKS ----------------
@dp.callback_query(F.data == "adm:panel")
async def admin_panel_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or not is_admin(callback.from_user.id):
        await callback.answer("Permission nahi hai.", show_alert=True)
        return

    await callback.message.edit_text(
        "🛡️ <b>Admin Panel</b>\nKisi user ki files check ya remove karne ke liye neeche click karein:",
        parse_mode="HTML",
        reply_markup=admin_panel_keyboard(),
    )
    await callback.answer()


@dp.callback_query(F.data == "adm:users")
async def admin_users_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or not is_admin(callback.from_user.id):
        await callback.answer("Permission nahi hai.", show_alert=True)
        return

    users = get_all_users()
    if not users:
        await callback.message.edit_text(
            "Abhi koi user register nahi hua hai.",
            reply_markup=admin_panel_keyboard(),
        )
        await callback.answer()
        return

    buttons = []
    for uid, name, uname, status, count in users:
        tag = f"@{uname}" if uname else f"ID:{uid}"
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"👤 {name} ({tag}) • [{status}] • {count} files",
                    callback_data=f"adm:ufiles:{uid}",
                )
            ]
        )
    buttons.append([InlineKeyboardButton(text="⬅️ Back", callback_data="adm:panel")])

    await callback.message.edit_text(
        "👥 <b>Registered Users</b> (kisi user par tap karke uski files dekhein):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("adm:ufiles:"))
async def admin_user_files_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or not is_admin(callback.from_user.id):
        await callback.answer("Permission nahi hai.", show_alert=True)
        return

    target_uid = int(callback.data.split(":", 2)[2])
    urow = get_user(target_uid)
    uname_display = urow[1] if urow else str(target_uid)
    files = search_files(target_uid, "")

    if not files:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Back to Users", callback_data="adm:users")]
            ]
        )
        await callback.message.edit_text(
            f"📂 <b>{uname_display}</b> ke paas koi saved file nahi hai.",
            parse_mode="HTML",
            reply_markup=kb,
        )
        await callback.answer()
        return

    buttons = []
    for fid, fname, ftype in files:
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"📄 {fname} ({ftype})",
                    callback_data=f"adm:view:{fid}",
                ),
                InlineKeyboardButton(
                    text="🗑️ Remove",
                    callback_data=f"adm:del:{fid}:{target_uid}",
                ),
            ]
        )
    buttons.append([InlineKeyboardButton(text="⬅️ Back to Users", callback_data="adm:users")])

    await callback.message.edit_text(
        f"📂 <b>{uname_display}</b> ki Saved Files:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("adm:view:"))
async def admin_view_file_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or not is_admin(callback.from_user.id):
        await callback.answer("Permission nahi hai.", show_alert=True)
        return

    row_id = int(callback.data.split(":", 2)[2])
    row = get_file_by_id(row_id, owner_id=None)
    if not row:
        await callback.answer("File nahi mili.", show_alert=True)
        return

    _, owner_id, name, file_id, file_type = row
    await callback.answer("Sending file...")
    await send_stored_file(
        callback.message,
        file_id=file_id,
        file_type=file_type,
        caption=f"🛡️ Admin View • <b>{name}</b> (Owner: <code>{owner_id}</code>)",
    )


@dp.callback_query(F.data.startswith("adm:del:"))
async def admin_delete_file_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or not is_admin(callback.from_user.id):
        await callback.answer("Permission nahi hai.", show_alert=True)
        return

    _, _, row_id_str, target_uid_str = callback.data.split(":", 3)
    row_id = int(row_id_str)
    target_uid = int(target_uid_str)

    deleted = delete_file_by_id(row_id)
    await callback.answer("File removed." if deleted else "Already removed.")

    files = search_files(target_uid, "")
    urow = get_user(target_uid)
    uname_display = urow[1] if urow else str(target_uid)

    if not files:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Back to Users", callback_data="adm:users")]
            ]
        )
        await callback.message.edit_text(
            f"✅ File remove ho gayi. <b>{uname_display}</b> ke paas ab koi file nahi bachi.",
            parse_mode="HTML",
            reply_markup=kb,
        )
        return

    buttons = [
        [
            InlineKeyboardButton(text=f"📄 {fname} ({ftype})", callback_data=f"adm:view:{fid}"),
            InlineKeyboardButton(text="🗑️ Remove", callback_data=f"adm:del:{fid}:{target_uid}"),
        ]
        for fid, fname, ftype in files
    ]
    buttons.append([InlineKeyboardButton(text="⬅️ Back to Users", callback_data="adm:users")])

    await callback.message.edit_text(
        f"✅ File remove ho gayi. <b>{uname_display}</b> ki Saved Files:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


# ---------------- USER ACTION BUTTONS ----------------
@dp.callback_query(F.data.startswith("act:"))
async def action_buttons_callback(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    user_id = callback.from_user.id

    if not is_approved_user(user_id):
        await callback.answer("Pehle admin se approval lena hoga.", show_alert=True)
        return

    action = callback.data.split(":", 1)[1]

    if action == "menu":
        user_state.pop(user_id, None)
        await callback.message.edit_text(
            "📁 <b>File Vault Menu</b>",
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(user_id),
        )
        await callback.answer()
        return

    if action == "save":
        user_state[user_id] = {"step": "waiting_save_name"}
        await callback.message.answer(
            "➕ File kis <b>naam</b> se save karni hai? Naam likh kar bhejein:",
            parse_mode="HTML",
        )
        await callback.answer()
        return

    if action == "find":
        user_state[user_id] = {"step": "waiting_find"}
        await callback.message.answer(
            "🔎 File ka <b>naam</b> (ya part of name) likh kar bhejein:",
            parse_mode="HTML",
        )
        await callback.answer()
        return

    if action == "edit":
        user_state[user_id] = {"step": "waiting_edit_name"}
        await callback.message.answer(
            "✏️ Kis saved file ko <b>replace/edit</b> karna hai? Uska naam likh kar bhejein:",
            parse_mode="HTML",
        )
        await callback.answer()
        return

    if action == "rename":
        user_state[user_id] = {"step": "waiting_rename"}
        await callback.message.answer(
            "🏷️ Rename karne ke liye is format mein likhein:\n<code>purana_naam | naya_naam</code>",
            parse_mode="HTML",
        )
        await callback.answer()
        return

    if action == "myfiles":
        matches = search_files(user_id, "")
        if not matches:
            await callback.message.answer(
                "📂 Aapki abhi koi saved file nahi hai.",
                reply_markup=main_menu_keyboard(user_id),
            )
            await callback.answer()
            return

        buttons = [
            [InlineKeyboardButton(text=f"📄 {name}", callback_data=f"dl:{row_id}")]
            for row_id, name, _ in matches
        ]
        buttons.append([InlineKeyboardButton(text="🏠 Menu", callback_data="act:menu")])
        await callback.message.answer(
            "📂 <b>Aapki Saved Files</b> (download ke liye tap karein):",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=b
      
