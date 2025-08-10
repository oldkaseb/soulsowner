# -*- coding: utf-8 -*-
"""
Single-file Telegram bot (aiogram v3) with PostgreSQL (asyncpg)

ENV VARS:
  BOT_TOKEN="..."
  DATABASE_URL="postgresql://user:pass@host:port/dbname"
Optional:
  ADMIN_SEED_IDS="123456,987654"   # comma-separated Telegram numeric IDs

Highlights:
- مدیریت قوانین:
    /setchat  → قوانین چت گروه
    /setcall  → قوانین کال گروه
    /setvserv → قوانین/شرایط خدمات مجازی
- پیام همگانی /broadcast → ارسال هر نوع پیام + آلبوم (media group) با کپشن
- آمار /stats، افزودن/حذف ادمین، بلاک/آن‌بلاک، پاسخ به کاربر
- دکمه «✉️ ارسال پیام مجدد» زیر پاسخ ادمین
- رفتار گروه: فقط به پیام‌هایی که شامل «مالک» هستند پاسخ می‌دهد؛ سایر پیام‌های گروه نادیده.
"""

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Optional, List, Tuple, Dict, Any

import asyncpg
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    Message,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    CallbackQuery,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaDocument,
    InputMediaAnimation,
    InputMediaAudio,
)
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

# -------------------- Config & Logging --------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_SEED_IDS = os.getenv("ADMIN_SEED_IDS", "").strip()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN env var is required")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL env var is required")

# Globals for easy access in handlers
DB_POOL: Optional[asyncpg.Pool] = None
BOT_USERNAME: str = ""

# -------------------- Text Constants (fa-IR) --------------------
WELCOME_TEXT = (
    "سلام! 👋\n"
    "به ربات ارتباطی خوش اومدی. یکی از بخش‌ها رو انتخاب کن:"
)
MAIN_MENU_TEXT = "یکی از گزینه‌ها را انتخاب کنید:"

# Sections
BTN_SECTION_GROUP = "ارتباط با ادمین‌های گروه"
BTN_SECTION_BOTS = "ارتباط با ربات‌های من"
BTN_SECTION_VSERV = "خدمات مجازی"

# Group requests
BTN_GROUP_ADMIN_CHAT = "درخواست ادمین چت"
BTN_GROUP_ADMIN_CALL = "درخواست ادمین کال"

# Actions after rules
BTN_SEND_REQUEST = "📨 ارسال درخواست"
BTN_CANCEL = "❌ انصراف"
BTN_SEND_AGAIN = "✉️ ارسال پیام مجدد"

# -------------------- Callback Data --------------------
CB_MAIN = "main"
CB_SECTION = "sec"      # sec|group / sec|bots / sec|vserv
CB_GSUB = "gsub"        # gsub|chat / gsub|call
CB_GACTION = "gact"     # gact|send|chat  or gact|send|call or gact|cancel
CB_SEND_AGAIN = "again" # again|start

# -------------------- FSM States --------------------
class SendToAdmin(StatesGroup):
    waiting_for_text = State()

class Broadcast(StatesGroup):
    waiting_for_message = State()  # accepts any content incl. albums

class AdminReply(StatesGroup):
    waiting_for_text = State()

class SetRules(StatesGroup):
    waiting_for_text = State()

# -------------------- DB Layer --------------------
@dataclass
class User:
    user_id: int
    is_admin: bool
    blocked: bool

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    blocked  BOOLEAN NOT NULL DEFAULT FALSE,
    first_name TEXT,
    last_name TEXT,
    username TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rules (
    section TEXT NOT NULL,     -- group|bots|vserv
    kind    TEXT NOT NULL,     -- chat|call|general
    text    TEXT NOT NULL,
    PRIMARY KEY (section, kind)
);

CREATE TABLE IF NOT EXISTS msg_log (
    id BIGSERIAL PRIMARY KEY,
    from_user BIGINT NOT NULL,
    to_user   BIGINT,
    direction TEXT NOT NULL,   -- user_to_admin | admin_to_user | broadcast
    content   TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

DEFAULT_RULES: List[Tuple[str, str, str]] = [
    ("group", "chat", "قوانین درخواست ادمین چت:\n1) محترمانه باشید\n2) موضوع را واضح بنویسید"),
    ("group", "call", "قوانین درخواست ادمین کال:\n1) زمان‌بندی را هماهنگ کنید\n2) تماس بی‌مورد نگیرید"),
    ("bots", "general", "قوانین ارتباط با ربات‌ها: ابتدا شناسه ربات و مشکل را دقیق بنویسید."),
    ("vserv", "general", "قوانین خدمات مجازی: نوع سرویس و توضیحات کامل را ارسال کنید."),
]

async def init_db():
    global DB_POOL
    DB_POOL = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    async with DB_POOL.acquire() as conn:
        await conn.execute(CREATE_SQL)
        for section, kind, text in DEFAULT_RULES:
            await conn.execute(
                """
                INSERT INTO rules(section, kind, text)
                VALUES($1,$2,$3)
                ON CONFLICT (section, kind) DO NOTHING
                """,
                section, kind, text,
            )
        if ADMIN_SEED_IDS:
            ids = [int(x) for x in ADMIN_SEED_IDS.split(',') if x.strip().isdigit()]
            for uid in ids:
                await conn.execute(
                    """
                    INSERT INTO users(user_id, is_admin, blocked)
                    VALUES($1, TRUE, FALSE)
                    ON CONFLICT (user_id) DO UPDATE SET is_admin=EXCLUDED.is_admin
                    """,
                    uid,
                )

async def upsert_user(m: Message):
    assert DB_POOL is not None
    async with DB_POOL.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users(user_id, is_admin, blocked, first_name, last_name, username)
            VALUES($1, FALSE, FALSE, $2, $3, $4)
            ON CONFLICT (user_id) DO UPDATE SET first_name=EXCLUDED.first_name,
                                             last_name=EXCLUDED.last_name,
                                             username=EXCLUDED.username
            """,
            m.from_user.id,
            m.from_user.first_name,
            m.from_user.last_name,
            m.from_user.username,
        )

async def get_user(user_id: int) -> Optional[User]:
    assert DB_POOL is not None
    async with DB_POOL.acquire() as conn:
        row = await conn.fetchrow("SELECT user_id, is_admin, blocked FROM users WHERE user_id=$1", user_id)
        if row:
            return User(user_id=row[0], is_admin=row[1], blocked=row[2])
        return None

async def set_admin(user_id: int, is_admin: bool):
    assert DB_POOL is not None
    async with DB_POOL.acquire() as conn:
        await conn.execute(
            "INSERT INTO users(user_id, is_admin, blocked) VALUES($1, $2, FALSE) "
            "ON CONFLICT (user_id) DO UPDATE SET is_admin=EXCLUDED.is_admin",
            user_id, is_admin,
        )

async def set_block(user_id: int, blocked: bool):
    assert DB_POOL is not None
    async with DB_POOL.acquire() as conn:
        await conn.execute(
            "INSERT INTO users(user_id, is_admin, blocked) VALUES($1, FALSE, $2) "
            "ON CONFLICT (user_id) DO UPDATE SET blocked=EXCLUDED.blocked",
            user_id, blocked,
        )

async def get_admin_ids() -> List[int]:
    assert DB_POOL is not None
    async with DB_POOL.acquire() as conn:
        rows = await conn.fetch("SELECT user_id FROM users WHERE is_admin=TRUE")
    return [r[0] for r in rows]

async def get_rules(section: str, kind: str) -> str:
    assert DB_POOL is not None
    async with DB_POOL.acquire() as conn:
        row = await conn.fetchrow("SELECT text FROM rules WHERE section=$1 AND kind=$2", section, kind)
        return row[0] if row else "هنوز قانونی ثبت نشده است."

async def set_rules(section: str, kind: str, text: str):
    assert DB_POOL is not None
    async with DB_POOL.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO rules(section, kind, text) VALUES($1,$2,$3)
            ON CONFLICT (section, kind) DO UPDATE SET text=EXCLUDED.text
            """,
            section, kind, text,
        )

async def log_message(from_user: int, to_user: Optional[int], direction: str, content: str):
    assert DB_POOL is not None
    async with DB_POOL.acquire() as conn:
        await conn.execute(
            "INSERT INTO msg_log(from_user, to_user, direction, content) VALUES($1,$2,$3,$4)",
            from_user, to_user, direction, content,
        )

async def get_stats() -> str:
    assert DB_POOL is not None
    async with DB_POOL.acquire() as conn:
        total = await conn.fetchval("SELECT COUNT(*) FROM users")
        blocked = await conn.fetchval("SELECT COUNT(*) FROM users WHERE blocked=TRUE")
        admins = await conn.fetchval("SELECT COUNT(*) FROM users WHERE is_admin=TRUE")
        msgs = await conn.fetchval("SELECT COUNT(*) FROM msg_log")
    return (
        f"📊 آمار:\n"
        f"👥 کاربران: {total}\n"
        f"🚫 بلاک‌شده: {blocked}\n"
        f"🛡️ ادمین‌ها: {admins}\n"
        f"✉️ پیام‌ها: {msgs}"
    )

# -------------------- Keyboards --------------------
def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=BTN_SECTION_GROUP, callback_data=f"{CB_SECTION}|group")],
        [InlineKeyboardButton(text=BTN_SECTION_BOTS,  callback_data=f"{CB_SECTION}|bots")],
        [InlineKeyboardButton(text=BTN_SECTION_VSERV, callback_data=f"{CB_SECTION}|vserv")],
    ])

def group_submenu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=BTN_GROUP_ADMIN_CHAT, callback_data=f"{CB_GSUB}|chat")],
        [InlineKeyboardButton(text=BTN_GROUP_ADMIN_CALL, callback_data=f"{CB_GSUB}|call")],
        [InlineKeyboardButton(text="⬅️ بازگشت", callback_data=f"{CB_MAIN}|menu")],
    ])

def after_rules_kb(kind: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=BTN_SEND_REQUEST, callback_data=f"{CB_GACTION}|send|{kind}")],
        [InlineKeyboardButton(text=BTN_CANCEL, callback_data=f"{CB_GACTION}|cancel|{kind}")],
    ])

def send_again_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=BTN_SEND_AGAIN, callback_data=f"{CB_SEND_AGAIN}|start")]
    ])

# -------------------- Helpers --------------------
async def disable_markup(call: CallbackQuery):
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

async def ensure_not_blocked(user_id: int) -> bool:
    u = await get_user(user_id)
    return not (u and u.blocked)

# -------------------- Album Buffer for Broadcast --------------------
# key: (admin_id, media_group_id)
_album_buffer: Dict[tuple, List[Dict[str, Any]]] = {}
_album_tasks: Dict[tuple, asyncio.Task] = {}

# -------------------- Bot Setup --------------------
bot = Bot(BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()

@dp.startup()
async def on_startup(_: Dispatcher):
    global BOT_USERNAME
    await init_db()
    me = await bot.get_me()
    BOT_USERNAME = me.username or ""
    logging.info("Bot started as @%s", BOT_USERNAME)

@dp.shutdown()
async def on_shutdown(_: Dispatcher):
    global DB_POOL
    if DB_POOL:
        await DB_POOL.close()
        logging.info("DB pool closed.")

# -------------------- Public Commands (Private only) --------------------
@dp.message(Command("start"))
async def cmd_start(m: Message, state: FSMContext):
    if m.chat.type != "private":
        return
    await upsert_user(m)
    if not await ensure_not_blocked(m.from_user.id):
        return await m.answer("شما مسدود شده‌اید.")
    await state.clear()
    await m.answer(WELCOME_TEXT, reply_markup=main_menu_kb())

@dp.message(Command("menu"))
async def cmd_menu(m: Message, state: FSMContext):
    if m.chat.type != "private":
        return
    await state.clear()
    await m.answer(MAIN_MENU_TEXT, reply_markup=main_menu_kb())

@dp.message(Command("help"))
async def cmd_help(m: Message):
    if m.chat.type != "private":
        return
    text = (
        "دستورات کاربری:\n"
        "/start /menu /help\n\n"
        "دستورات ادمین:\n"
        "/broadcast – پیام همگانی (همۀ انواع فایل/آلبوم)\n"
        "/stats – آمار دقیق\n"
        "/addadmin <user_id> – افزودن ادمین\n"
        "/deladmin <user_id> – حذف ادمین\n"
        "/block <user_id> – بلاک\n"
        "/unblock <user_id> – آنبلاک\n"
        "/setchat – تغییر قوانین چت گروه\n"
        "/setcall – تغییر قوانین کال گروه\n"
        "/setvserv – ست‌کردن قوانین خدمات مجازی\n"
        "/reply <user_id> – پاسخ به کاربر\n"
    )
    await m.answer(text)

# -------------------- Admin Guards --------------------
async def require_admin(message: Message) -> bool:
    u = await get_user(message.from_user.id)
    if not (u and u.is_admin):
        await message.answer("⛔ این دستور مخصوص ادمین‌هاست.")
        return False
    return True

# -------------------- Admin Commands (Private only) --------------------
@dp.message(Command("broadcast"))
async def cmd_broadcast(m: Message, state: FSMContext):
    if m.chat.type != "private":
        return
    if not await require_admin(m):
        return
    await state.set_state(Broadcast.waiting_for_message)
    await m.answer("پیام/فایل/آلبوم مورد نظر برای ارسال همگانی را بفرستید. لغو: /cancel")

@dp.message(Command("stats")))
async def cmd_stats(m: Message):
    if m.chat.type != "private":
        return
    if not await require_admin(m):
        return
    await m.answer(await get_stats())

@dp.message(Command("addadmin")))
async def cmd_addadmin(m: Message, command: CommandObject):
    if m.chat.type != "private":
        return
    if not await require_admin(m):
        return
    if not command.args or not command.args.strip().isdigit():
        return await m.answer("فرمت: /addadmin <user_id>")
    await set_admin(int(command.args.strip()), True)
    await m.answer(f"✅ کاربر {command.args.strip()} به عنوان ادمین اضافه شد.")

@dp.message(Command("deladmin")))
async def cmd_deladmin(m: Message, command: CommandObject):
    if m.chat.type != "private":
        return
    if not await require_admin(m):
        return
    if not command.args or not command.args.strip().isdigit():
        return await m.answer("فرمت: /deladmin <user_id>")
    await set_admin(int(command.args.strip()), False)
    await m.answer(f"✅ دسترسی ادمینی کاربر {command.args.strip()} حذف شد.")

@dp.message(Command("block")))
async def cmd_block(m: Message, command: CommandObject):
    if m.chat.type != "private":
        return
    if not await require_admin(m):
        return
    if not command.args or not command.args.strip().isdigit():
        return await m.answer("فرمت: /block <user_id>")
    await set_block(int(command.args.strip()), True)
    await m.answer(f"🚫 کاربر {command.args.strip()} بلاک شد.")

@dp.message(Command("unblock")))
async def cmd_unblock(m: Message, command: CommandObject):
    if m.chat.type != "private":
        return
    if not await require_admin(m):
        return
    if not command.args or not command.args.strip().isdigit():
        return await m.answer("فرمت: /unblock <user_id>")
    await set_block(int(command.args.strip()), False)
    await m.answer(f"♻️ کاربر {command.args.strip()} آنبلاک شد.")

@dp.message(Command("reply")))
async def cmd_reply(m: Message, state: FSMContext, command: CommandObject):
    if m.chat.type != "private":
        return
    if not await require_admin(m):
        return
    if not command.args or not command.args.strip().isdigit():
        return await m.answer("فرمت: /reply <user_id>")
    target_id = int(command.args.strip())
    await state.set_state(AdminReply.waiting_for_text)
    await state.update_data(target_id=target_id)
    await m.answer(f"متن پاسخ برای کاربر {target_id} را بفرستید. لغو: /cancel")

@dp.message(Command("setrules")))
async def cmd_setrules(m: Message, state: FSMContext, command: CommandObject):
    if m.chat.type != "private":
        return
    if not await require_admin(m):
        return
    if not command.args:
        return await m.answer("فرمت: /setrules <section> <kind> ==> سپس متن قوانین را بفرستید.\nمثال: /setrules group chat")
    args = command.args.strip().split()
    if len(args) != 2:
        return await m.answer("باید دقیقا دو آرگومان بدهید: section و kind. مثال: group chat")
    section, kind = args[0], args[1]
    if section not in {"group", "bots", "vserv"}:
        return await m.answer("section نامعتبر است. یکی از: group, bots, vserv")
    await state.set_state(SetRules.waiting_for_text)
    await state.update_data(section=section, kind=kind)
    await m.answer(f"متن جدید قوانین برای {section} / {kind} را بفرستید. لغو: /cancel")

@dp.message(Command("setchat")))
async def cmd_setchat(m: Message, state: FSMContext):
    if m.chat.type != "private":
        return
    if not await require_admin(m):
        return
    await state.set_state(SetRules.waiting_for_text)
    await state.update_data(section="group", kind="chat")
    await m.answer("متن قوانین جدید برای «چت گروه» را بفرستید. لغو: /cancel")

@dp.message(Command("setcall")))
async def cmd_setcall(m: Message, state: FSMContext):
    if m.chat.type != "private":
        return
    if not await require_admin(m):
        return
    await state.set_state(SetRules.waiting_for_text)
    await state.update_data(section="group", kind="call")
    await m.answer("متن قوانین جدید برای «کال گروه» را بفرستید. لغو: /cancel")

@dp.message(Command("setvserv")))
async def cmd_setvserv(m: Message, state: FSMContext):
    if m.chat.type != "private":
        return
    if not await require_admin(m):
        return
    await state.set_state(SetRules.waiting_for_text)
    await state.update_data(section="vserv", kind="general")
    await m.answer("متن قوانین/شرایط «خدمات مجازی» را بفرستید. لغو: /cancel")

@dp.message(Command("cancel")))
async def cmd_cancel(m: Message, state: FSMContext):
    if m.chat.type != "private":
        return
    await state.clear()
    await m.answer("لغو شد.")

# -------------------- State Handlers (Private) --------------------
async def _send_media_group_to_all(items: List[Dict[str, Any]], caption, caption_entities, sender_id):
    assert DB_POOL is not None
    async with DB_POOL.acquire() as conn:
        rows = await conn.fetch("SELECT user_id FROM users WHERE blocked=FALSE")
    recipients = [r[0] for r in rows]
    sent = 0
    for uid in recipients:
        try:
            media = []
            first = True
            for it in items:
                if it['type'] == 'photo':
                    media.append(InputMediaPhoto(media=it['file_id'], caption=caption if first else None, caption_entities=caption_entities if first else None))
                elif it['type'] == 'video':
                    media.append(InputMediaVideo(media=it['file_id'], caption=caption if first else None, caption_entities=caption_entities if first else None))
                elif it['type'] == 'document':
                    media.append(InputMediaDocument(media=it['file_id'], caption=caption if first else None, caption_entities=caption_entities if first else None))
                elif it['type'] == 'animation':
                    media.append(InputMediaAnimation(media=it['file_id'], caption=caption if first else None, caption_entities=caption_entities if first else None))
                elif it['type'] == 'audio':
                    media.append(InputMediaAudio(media=it['file_id'], caption=caption if first else None, caption_entities=caption_entities if first else None))
                first = False
            await bot.send_media_group(uid, media)
            sent += 1
        except Exception:
            continue
    return sent

@dp.message(Broadcast.waiting_for_message)
async def on_broadcast_any(m: Message, state: FSMContext):
    if m.chat.type != "private":
        return
    if not await require_admin(m):
        return

    # Album (media group)
    if m.media_group_id:
        key = (m.from_user.id, m.media_group_id)
        buf = _album_buffer.get(key, [])
        item = None
        if m.photo:
            item = {'type': 'photo', 'file_id': m.photo[-1].file_id}
        elif m.video:
            item = {'type': 'video', 'file_id': m.video.file_id}
        elif m.document:
            item = {'type': 'document', 'file_id': m.document.file_id}
        elif m.animation:
            item = {'type': 'animation', 'file_id': m.animation.file_id}
        elif m.audio:
            item = {'type': 'audio', 'file_id': m.audio.file_id}
        if item:
            buf.append(item)
            _album_buffer[key] = buf

        async def _flush_album():
            await asyncio.sleep(2)  # wait for rest of album
            items = _album_buffer.pop(key, [])
            caption = m.caption or ''
            caption_entities = m.caption_entities
            sent = await _send_media_group_to_all(items, caption, caption_entities, m.from_user.id)
            await state.clear()
            try:
                await m.answer(f"✅ آلبوم برای {sent} نفر ارسال شد.")
            except Exception:
                pass

        t = _album_tasks.get(key)
        if t and not t.done():
            t.cancel()
        _album_tasks[key] = asyncio.create_task(_flush_album())
        return

    # Single message (any type) – preserves caption/files
    assert DB_POOL is not None
    async with DB_POOL.acquire() as conn:
        rows = await conn.fetch("SELECT user_id FROM users WHERE blocked=FALSE")
    recipients = [r[0] for r in rows]
    sent = 0
    for uid in recipients:
        try:
            await bot.copy_message(chat_id=uid, from_chat_id=m.chat.id, message_id=m.message_id)
            await log_message(m.from_user.id, uid, "broadcast", m.caption or m.text or m.content_type)
            sent += 1
        except Exception:
            continue
    await state.clear()
    await m.answer(f"✅ ارسال شد برای {sent} نفر.")

@dp.message(AdminReply.waiting_for_text)
async def on_admin_reply(m: Message, state: FSMContext):
    if m.chat.type != "private":
        return
    if not await require_admin(m):
        return
    data = await state.get_data()
    target_id = int(data.get("target_id"))
    try:
        await bot.send_message(target_id, f"پاسخ ادمین:\n\n{m.html_text}", reply_markup=send_again_kb())
        await log_message(m.from_user.id, target_id, "admin_to_user", m.html_text)
        await m.answer("✅ ارسال شد.")
    except Exception:
        await m.answer("❌ ارسال نشد. شاید کاربر پیوی ربات را باز نکرده.")
    await state.clear()

@dp.message(SetRules.waiting_for_text)
async def on_set_rules_text(m: Message, state: FSMContext):
    if m.chat.type != "private":
        return
    if not await require_admin(m):
        return
    data = await state.get_data()
    section = data.get("section")
    kind = data.get("kind")
    await set_rules(section, kind, m.html_text)
    await state.clear()
    await m.answer("✅ قوانین ذخیره شد.")

# -------------------- Callback Query Handlers (Private only) --------------------
@dp.callback_query(F.data.startswith(f"{CB_MAIN}|"))
async def on_main_nav(call: CallbackQuery, state: FSMContext):
    if call.message.chat.type != "private":
        return
    await disable_markup(call)
    await state.clear()
    await call.message.answer(MAIN_MENU_TEXT, reply_markup=main_menu_kb())
    await call.answer()

@dp.callback_query(F.data.startswith(f"{CB_SECTION}|"))
async def on_section(call: CallbackQuery):
    if call.message.chat.type != "private":
        return
    if not await ensure_not_blocked(call.from_user.id):
        await call.answer("مسدود شده‌اید.", show_alert=True)
        return
    await disable_markup(call)

    _, section = call.data.split("|", 1)
    if section == "group":
        await call.message.answer("بخش گروه – نوع درخواست را انتخاب کنید:", reply_markup=group_submenu_kb())
    elif section == "bots":
        rules = await get_rules("bots", "general")
        await call.message.answer(rules, reply_markup=after_rules_kb("general"))
    elif section == "vserv":
        rules = await get_rules("vserv", "general")
        await call.message.answer(rules, reply_markup=after_rules_kb("general"))
    await call.answer()

@dp.callback_query(F.data.startswith(f"{CB_GSUB}|"))
async def on_group_sub(call: CallbackQuery):
    if call.message.chat.type != "private":
        return
    if not await ensure_not_blocked(call.from_user.id):
        await call.answer("مسدود شده‌اید.", show_alert=True)
        return
    await disable_markup(call)

    _, kind = call.data.split("|", 1)  # chat or call
    rules = await get_rules("group", kind)
    await call.message.answer(rules, reply_markup=after_rules_kb(kind))
    await call.answer()

@dp.callback_query(F.data.startswith(f"{CB_GACTION}|"))
async def on_group_action(call: CallbackQuery, state: FSMContext):
    if call.message.chat.type != "private":
        return
    if not await ensure_not_blocked(call.from_user.id):
        await call.answer("مسدود شده‌اید.", show_alert=True)
        return

    await disable_markup(call)
    _, action, kind = call.data.split("|", 2)
    if action == "send":
        await state.set_state(SendToAdmin.waiting_for_text)
        await state.update_data(kind=kind)
        await call.message.answer("لطفاً متن درخواست خود را ارسال کنید. لغو: /cancel")
    else:
        await state.clear()
        await call.message.answer("لغو شد.")
    await call.answer()

@dp.callback_query(F.data.startswith(f"{CB_SEND_AGAIN}|"))
async def on_send_again(call: CallbackQuery, state: FSMContext):
    if call.message.chat.type != "private":
        return
    if not await ensure_not_blocked(call.from_user.id):
        await call.answer("مسدود شده‌اید.", show_alert=True)
        return

    await disable_markup(call)
    await state.set_state(SendToAdmin.waiting_for_text)
    await call.message.answer("متن جدید را بفرستید. لغو: /cancel")
    await call.answer()

# -------------------- User-to-Admin Flow (Private only) --------------------
@dp.message(SendToAdmin.waiting_for_text)
async def on_user_message_to_admin(m: Message, state: FSMContext):
    if m.chat.type != "private":
        return
    if not await ensure_not_blocked(m.from_user.id):
        return await m.answer("شما مسدود شده‌اید.")

    data = await state.get_data()
    kind = data.get("kind", "general")
    admin_ids = await get_admin_ids()
    if not admin_ids:
        await m.answer("فعلاً ادمینی ثبت نشده.")
        return

    preview = (
        f"📬 درخواست جدید از <code>{m.from_user.id}</code>\n"
        f"نوع: {kind}\n\n"
        f"{m.html_text}\n\n"
        f"برای پاسخ: /reply {m.from_user.id}"
    )

    sent_to = 0
    for aid in admin_ids:
        try:
            await bot.send_message(aid, preview)
            sent_to += 1
        except Exception:
            pass

    await log_message(m.from_user.id, None, "user_to_admin", m.html_text)
    await state.clear()
    if sent_to:
        await m.answer("✅ درخواست شما برای ادمین‌ها ارسال شد.", reply_markup=send_again_kb())
    else:
        await m.answer("❌ هیچ ادمینی در دسترس نیست.")

# -------------------- Group Behavior --------------------
# فقط وقتی پیام شامل «مالک» باشد، در گروه جواب می‌دهیم؛ بقیه سکوت.
@dp.message()
async def group_gate(m: Message):
    if m.chat.type == "private":
        # پیام‌های آزاد در پی‌وی؛ اگر دستور نیست، راهنمای کوتاه:
        if not (m.text or "").startswith("/"):
            await m.answer("برای شروع از /menu استفاده کنید.")
        return

    if m.chat.type in ("group", "supergroup"):
        text = (m.text or m.caption or "")
        if "مالک" in text:
            btns = None
            if BOT_USERNAME:
                btns = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="شروع گفتگو در پی‌وی", url=f"https://t.me/{BOT_USERNAME}?start=start")
                ]])
            await m.reply("سلام! برای ارتباط مستقیم، لطفاً به پی‌وی ربات پیام بدید. 👇", reply_markup=btns)
        # اگر «مالک» نباشد، هیچ پاسخی نده.
        return

# -------------------- Entrypoint --------------------
async def main():
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Bot stopped.")
