# -*- coding: utf-8 -*-
"""
Telegram Bot – aiogram v3.7 + asyncpg (single file)

ENV (Railway):
  BOT_TOKEN="..."
  DATABASE_URL="postgresql://user:pass@host:port/dbname"
  ADMIN_ID="123456, 987654"  # یک یا چند آیدی با کاما/فاصله
"""

import asyncio
import logging
import os
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any

import asyncpg
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    Message,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    CallbackQuery,
    InputMediaPhoto,
    InputMediaVideo,
)
from aiogram.client.default import DefaultBotProperties

# -------------------- Config & Logging --------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_ID_RAW = os.getenv("ADMIN_ID", os.getenv("ADMIN_SEED_IDS", "")).strip()
ADMIN_IDS_SEED = {int(n) for n in ADMIN_ID_RAW.replace(",", " ").split() if n.strip().isdigit()}

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN env var is required")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL env var is required")

DB_POOL: Optional[asyncpg.Pool] = None
BOT_USERNAME: str = ""

# -------------------- Texts --------------------
WELCOME_TEXT = """سلام! 👋
یکی از بخش‌ها را انتخاب کنید تا دقیق‌تر بفهمم چه کاری دارید:"""
MAIN_MENU_TEXT = "یکی از گزینه‌ها را انتخاب کنید:"

# Buttons
BTN_SECTION_BOTS   = "🤖 گفت‌وگو درباره ربات‌ها"
#BTN_SECTION_SOULS  = "💬 گروه Souls"
BTN_SECTION_VSERV  = "🛍️ خدمات مجازی"
BTN_SECTION_FREE   = "🗣️ گفت‌وگوی آزاد"

BTN_GROUP_ADMIN_CHAT = "درخواست ادمین چت"
BTN_GROUP_ADMIN_CALL = "درخواست ادمین کال"

BTN_SEND_REQUEST = "✅ می‌پذیرم و ارسال درخواست"   # فقط برای Souls
BTN_CANCEL       = "❌ انصراف"
BTN_SEND_AGAIN   = "✉️ ارسال پیام مجدد"          # بعد از ارسال موفق کاربر
BTN_QUICK_SEND   = "✉️ ارسال پیام"               # برای bots/vserv/free

BTN_REPLY        = "✉️ پاسخ"
BTN_REPLY_AGAIN  = "✉️ پاسخِ مجدد"                # بعد از ارسال موفق ادمین

# Callback data prefixes
CB_MAIN    = "main"
CB_SEC     = "sec"      # sec|bots / sec|souls / sec|vserv / sec|free
CB_SOULS   = "souls"    # souls|chat / souls|call
CB_ACTION  = "act"      # act|send|<kind> or act|cancel|<kind>
CB_AGAIN   = "again"    # again|start
CB_REPLY   = "reply"    # reply|<user_id>

# -------------------- FSM --------------------
class SendToAdmin(StatesGroup):
    waiting_for_text = State()

class Broadcast(StatesGroup):       # به کاربران
    waiting_for_message = State()

class GroupBroadcast(StatesGroup):  # به گروه‌ها
    waiting_for_message = State()

class AdminReply(StatesGroup):
    waiting_for_any = State()

class SetRules(StatesGroup):
    waiting_for_text = State()

class GroupReply(StatesGroup):
    waiting_for_link = State()
    waiting_for_content = State()

# -------------------- DB --------------------
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
    last_name  TEXT,
    username   TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rules (
    section TEXT NOT NULL,     -- souls|bots|vserv
    kind    TEXT NOT NULL,     -- chat|call|general
    text    TEXT NOT NULL,
    PRIMARY KEY (section, kind)
);

CREATE TABLE IF NOT EXISTS msg_log (
    id BIGSERIAL PRIMARY KEY,
    from_user BIGINT NOT NULL,
    to_user   BIGINT,
    direction TEXT NOT NULL,   -- user_to_admin | admin_to_user | broadcast | group_broadcast
    content   TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS groups (
    chat_id   BIGINT PRIMARY KEY,
    title     TEXT,
    username  TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    added_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

DEFAULT_RULES: List[Tuple[str, str, str]] = [
    ("souls", "chat", "قوانین چت گروه Souls: محترم باشید و از اسپم خودداری کنید."),
    ("souls", "call", "قوانین کال گروه Souls: هماهنگی زمان و رعایت ادب الزامی است."),
    ("bots",  "general", "برای گفت‌وگو درباره ربات‌ها: نام ربات، مشکل/درخواست و اسکرین‌شات را ذکر کنید."),
    ("vserv", "general", "لطفاً قبل از سفارش، نوع سرویس، جزئیات و زمان‌بندی را واضح بنویسید."),
]

VIRTUAL_SERVICES_LIST = (
    "🔹 فروش سرویس تلگرام پریمیوم گیفتی (بدون ورود به اکانت)\n"
    "🔹 پخش لینک در پیوی (سندر)\n"
    "🔹 ممبر فیک تضمینی\n"
    "🔹 ممبر واقعی آپلودری اخلاقی و غیراخلاقی\n"
    "🔹 ویو و ری‌اکت کانال\n"
    "🔹 ربات امنیت و موزیک\n"
    "🔹 ساخت و استارت انواع ربات‌ها\n"
    "🔹 انواع خدمات سایر اپلیکیشن‌ها"
)

async def init_db():
    global DB_POOL
    DB_POOL = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    async with DB_POOL.acquire() as conn:
        await conn.execute(CREATE_SQL)
        # seed default rules
        for section, kind, text in DEFAULT_RULES:
            await conn.execute(
                """INSERT INTO rules(section, kind, text) VALUES($1,$2,$3)
                   ON CONFLICT (section, kind) DO NOTHING""",
                section, kind, text,
            )
        # load local rules files if exist
        try:
            chat_p = Path("rules_chat.txt")
            call_p = Path("rules_call.txt")
            if chat_p.exists():
                t = chat_p.read_text(encoding="utf-8").strip()
                if t:
                    await conn.execute(
                        """INSERT INTO rules(section,kind,text) VALUES('souls','chat',$1)
                           ON CONFLICT (section,kind) DO UPDATE SET text=EXCLUDED.text""",
                        t,
                    )
            if call_p.exists():
                t = call_p.read_text(encoding="utf-8").strip()
                if t:
                    await conn.execute(
                        """INSERT INTO rules(section,kind,text) VALUES('souls','call',$1)
                           ON CONFLICT (section,kind) DO UPDATE SET text=EXCLUDED.text""",
                        t,
                    )
        except Exception as e:
            logging.warning("could not load local rules files: %s", e)

        # seed admins from env
        if ADMIN_ID_RAW:
            nums = [n for n in ADMIN_ID_RAW.replace(",", " ").split() if n.isdigit()]
            for uid in map(int, nums):
                await conn.execute(
                    """INSERT INTO users(user_id, is_admin, blocked)
                       VALUES($1, TRUE, FALSE)
                       ON CONFLICT (user_id) DO UPDATE SET is_admin=EXCLUDED.is_admin""",
                    uid,
                )

# --- DB helpers ---
async def upsert_user(m: Message):
    assert DB_POOL is not None
    async with DB_POOL.acquire() as conn:
        await conn.execute(
            """INSERT INTO users(user_id, is_admin, blocked, first_name, last_name, username)
               VALUES($1, FALSE, FALSE, $2, $3, $4)
               ON CONFLICT (user_id) DO UPDATE SET
                 first_name=EXCLUDED.first_name,
                 last_name =EXCLUDED.last_name,
                 username  =EXCLUDED.username""",
            m.from_user.id, m.from_user.first_name, m.from_user.last_name, m.from_user.username,
        )

async def upsert_user_profile(user_id: int, first_name: Optional[str], last_name: Optional[str], username: Optional[str]):
    assert DB_POOL is not None
    async with DB_POOL.acquire() as conn:
        await conn.execute(
            """INSERT INTO users(user_id, is_admin, blocked, first_name, last_name, username)
               VALUES($1, FALSE, FALSE, $2, $3, $4)
               ON CONFLICT (user_id) DO UPDATE SET
                 first_name=EXCLUDED.first_name,
                 last_name =EXCLUDED.last_name,
                 username  =EXCLUDED.username""",
            user_id, first_name, last_name, username
        )

async def _auto_delete(chat_id: int, message_id: int, delay: int = 30):
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass  # دسترسی حذف نداشتیم یا پیام قبلاً پاک شده

async def get_user(user_id: int) -> Optional[User]:
    assert DB_POOL is not None
    async with DB_POOL.acquire() as conn:
        row = await conn.fetchrow("SELECT user_id, is_admin, blocked FROM users WHERE user_id=$1", user_id)
    return User(row[0], row[1], row[2]) if row else None

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
            """INSERT INTO rules(section, kind, text) VALUES($1,$2,$3)
               ON CONFLICT (section, kind) DO UPDATE SET text=EXCLUDED.text""",
            section, kind, text,
        )

async def log_message(from_user: int, to_user: Optional[int], direction: str, content: str):
    assert DB_POOL is not None
    async with DB_POOL.acquire() as conn:
        await conn.execute(
            "INSERT INTO msg_log(from_user, to_user, direction, content) VALUES($1,$2,$3,$4)",
            from_user, to_user, direction, content,
        )

# گروه‌ها
async def upsert_group(chat_id: int, title: Optional[str], username: Optional[str], active: bool = True):
    assert DB_POOL is not None
    async with DB_POOL.acquire() as conn:
        await conn.execute(
            """INSERT INTO groups(chat_id, title, username, is_active)
               VALUES($1,$2,$3,$4)
               ON CONFLICT (chat_id) DO UPDATE
                 SET title=EXCLUDED.title, username=EXCLUDED.username,
                     is_active=EXCLUDED.is_active, updated_at=NOW()""",
            chat_id, title, username, active
        )

async def get_group_ids(active_only: bool = True) -> List[int]:
    assert DB_POOL is not None
    async with DB_POOL.acquire() as conn:
        rows = await conn.fetch(
            "SELECT chat_id FROM groups" + (" WHERE is_active=TRUE" if active_only else "")
        )
    return [r[0] for r in rows]

async def list_groups(limit: int = 50) -> List[Tuple[int, str]]:
    assert DB_POOL is not None
    async with DB_POOL.acquire() as conn:
        rows = await conn.fetch(
            "SELECT chat_id, COALESCE(title, username, chat_id::text) AS name "
            "FROM groups WHERE is_active=TRUE ORDER BY updated_at DESC LIMIT $1",
            limit
        )
    return [(r[0], r[1]) for r in rows]

# -------------------- Keyboards --------------------
def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        #[InlineKeyboardButton(text=BTN_SECTION_SOULS, callback_data=f"{CB_SEC}|souls")],
        [InlineKeyboardButton(text=BTN_SECTION_BOTS,  callback_data=f"{CB_SEC}|bots")],
        [InlineKeyboardButton(text=BTN_SECTION_VSERV, callback_data=f"{CB_SEC}|vserv")],
        [InlineKeyboardButton(text=BTN_SECTION_FREE,  callback_data=f"{CB_SEC}|free")],
    ])

def souls_submenu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=BTN_GROUP_ADMIN_CHAT, callback_data=f"{CB_SOULS}|chat")],
        [InlineKeyboardButton(text=BTN_GROUP_ADMIN_CALL, callback_data=f"{CB_SOULS}|call")],
        [InlineKeyboardButton(text="⬅️ بازگشت", callback_data=f"{CB_MAIN}|menu")],
    ])

def after_rules_kb(kind: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=BTN_SEND_REQUEST, callback_data=f"{CB_ACTION}|send|{kind}")],
        [InlineKeyboardButton(text=BTN_CANCEL,       callback_data=f"{CB_ACTION}|cancel|{kind}")],
    ])

def quick_send_kb(kind: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=BTN_QUICK_SEND, callback_data=f"{CB_ACTION}|send|{kind}")],
        [InlineKeyboardButton(text="⬅️ بازگشت", callback_data=f"{CB_MAIN}|menu")],
    ])

def send_again_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=BTN_SEND_AGAIN, callback_data=f"{CB_AGAIN}|start")],
        [InlineKeyboardButton(text="🏠 منوی اصلی", callback_data=f"{CB_MAIN}|menu")],
    ])

def admin_reply_kb(user_id: int) -> InlineKeyboardMarkup:
    # فقط «پاسخ» در مرحله‌ی اول
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=BTN_REPLY, callback_data=f"{CB_REPLY}|{user_id}")],
    ])

def admin_reply_again_kb(user_id: int) -> InlineKeyboardMarkup:
    # بعد از ارسال موفق، «پاسخِ مجدد»
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=BTN_REPLY_AGAIN, callback_data=f"{CB_REPLY}|{user_id}")],
    ])

# -------------------- Helpers --------------------
def _normalize_fa(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    return s.replace("ي", "ی").replace("ك", "ک")

def contains_malek(text: str) -> bool:
    t = _normalize_fa(text or "")
    return "مالک" in t  # شامل حالت‌های «مالکش/مالکشو/...»

async def disable_markup(call: CallbackQuery):
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

# --- admin check: message vs callback ---
async def _check_and_seed_admin(user_id: int) -> bool:
    if user_id in ADMIN_IDS_SEED:
        u = await get_user(user_id)
        if not (u and u.is_admin):
            await set_admin(user_id, True)
        return True
    u = await get_user(user_id)
    return bool(u and u.is_admin)

async def require_admin_msg(m: Message) -> bool:
    await upsert_user(m)
    ok = await _check_and_seed_admin(m.from_user.id)
    if not ok:
        await m.answer("⛔ این دستور مخصوص ادمین‌هاست.")
    return ok

async def require_admin_call(call: CallbackQuery) -> bool:
    u = call.from_user
    await upsert_user_profile(u.id, u.first_name, u.last_name, u.username)
    ok = await _check_and_seed_admin(u.id)
    if not ok:
        await call.message.answer("⛔ این دستور مخصوص ادمین‌هاست.")
    return ok

# -------------------- Album helpers --------------------
# توجه: آلبوم فقط برای photo/video پشتیبانی می‌شود (طبق محدودیت Telegram).
_album_buffer_users: Dict[tuple, List[Dict[str, Any]]] = {}
_album_tasks_users: Dict[tuple, asyncio.Task] = {}
_album_buffer_groups: Dict[tuple, List[Dict[str, Any]]] = {}
_album_tasks_groups: Dict[tuple, asyncio.Task] = {}

_album_buffer_u2a: Dict[tuple, List[Dict[str, Any]]] = {}
_album_tasks_u2a: Dict[tuple, asyncio.Task] = {}

_album_buffer_admin_reply: Dict[tuple, List[Dict[str, Any]]] = {}
_album_tasks_admin_reply: Dict[tuple, asyncio.Task] = {}

def _collect_item_from_message(m: Message) -> Optional[Dict[str, Any]]:
    # برای آلبوم: photo/video کافیست. سایر انواع به صورت تکی handled می‌شوند.
    if m.photo:
        return {'type': 'photo', 'file_id': m.photo[-1].file_id}
    if m.video:
        return {'type': 'video', 'file_id': m.video.file_id}
    return None

async def _send_media_group(bot: Bot, chat_id: int, items: List[Dict[str, Any]], caption, caption_entities):
    media = []
    first = True
    for it in items:
        if it['type'] == 'photo':
            media.append(InputMediaPhoto(media=it['file_id'], caption=caption if first else None, caption_entities=caption_entities if first else None))
        elif it['type'] == 'video':
            media.append(InputMediaVideo(media=it['file_id'], caption=caption if first else None, caption_entities=caption_entities if first else None))
        first = False
    if media:
        await bot.send_media_group(chat_id, media)

# -------------------- Bot & Dispatcher --------------------
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# -------------------- User commands (private) --------------------
@dp.message(Command("start"))
async def cmd_start(m: Message, state: FSMContext):
    if m.chat.type != "private":
        return
    await upsert_user(m)
    u = await get_user(m.from_user.id)
    if u and u.blocked:
        return await m.answer("شما مسدود شده‌اید.")
    await state.clear()
    await m.answer(WELCOME_TEXT, reply_markup=main_menu_kb())

@dp.message(Command("menu"))
async def cmd_menu(m: Message, state: FSMContext):
    if m.chat.type != "private":
        return
    await state.clear()
    await m.answer(MAIN_MENU_TEXT, reply_markup=main_menu_kb())

@dp.message(Command("whoami"))
async def cmd_whoami(m: Message):
    if m.chat.type != "private":
        return
    await upsert_user(m)
    u = await get_user(m.from_user.id)
    is_admin = (u.is_admin if u else False)
    uname = ("@" + m.from_user.username) if m.from_user.username else "-"
    full_name = " ".join(filter(None, [m.from_user.first_name, m.from_user.last_name])) or "-"
    await m.answer(
        "اطلاعات شما:\n"
        f"🆔 ID: <code>{m.from_user.id}</code>\n"
        f"👤 نام: {full_name}\n"
        f"📛 یوزرنیم: {uname}\n"
        f"🔐 ادمین: {'✅' if is_admin else '❌'}"
    )

@dp.message(Command("seedadmin"))
async def cmd_seedadmin(m: Message):
    if m.chat.type != "private":
        return
    ids = await get_admin_ids()
    if ids:
        return await m.answer("⛔ قبلاً ادمین ثبت شده. برای اضافه‌کردن بقیه از دستور /addadmin استفاده کنید.")
    await set_admin(m.from_user.id, True)
    await m.answer("✅ شما به‌عنوان اولین ادمین ثبت شدید. برای دیدن وضعیت، /whoami را بزنید.")

# -------------------- Admin: broadcasts to USERS --------------------
@dp.message(Command("broadcast"))
async def cmd_broadcast(m: Message, state: FSMContext):
    if m.chat.type != "private" or not await require_admin_msg(m):
        return
    await state.set_state(Broadcast.waiting_for_message)
    await m.answer("پیام/فایل/آلبوم برای *کاربران* را بفرستید. لغو: /cancel")

@dp.message(Broadcast.waiting_for_message)
async def on_broadcast_to_users(m: Message, state: FSMContext):
    if m.text and m.text.startswith("/") and m.text != "/cancel":
        return
    if m.chat.type != "private" or not await require_admin_msg(m):
        return

    if m.media_group_id:
        key = (m.from_user.id, m.media_group_id)
        buf = _album_buffer_users.get(key, [])
        item = _collect_item_from_message(m)
        if item:
            buf.append(item); _album_buffer_users[key] = buf

        async def _flush():
            await asyncio.sleep(2)
            items = _album_buffer_users.pop(key, [])
            caption, ents = m.caption or '', m.caption_entities
            assert DB_POOL is not None
            async with DB_POOL.acquire() as conn:
                rows = await conn.fetch("SELECT user_id FROM users WHERE blocked=FALSE")
            chat_ids = [r[0] for r in rows]
            sent = 0
            for uid in chat_ids:
                try:
                    await _send_media_group(bot, uid, items, caption, ents)
                    sent += 1
                except Exception:
                    pass
            await state.clear()
            await m.answer(f"✅ آلبوم برای {sent} کاربر ارسال شد.")
        t = _album_tasks_users.get(key)
        if t and not t.done():
            t.cancel()
        _album_tasks_users[key] = asyncio.create_task(_flush())
        return

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
    await m.answer(f"✅ ارسال شد برای {sent} کاربر.")

# -------------------- Admin: broadcasts to GROUPS --------------------
@dp.message(Command("groupsend"))
async def cmd_groupsend(m: Message, state: FSMContext):
    if m.chat.type != "private" or not await require_admin_msg(m):
        return
    await state.set_state(GroupBroadcast.waiting_for_message)
    await m.answer("پیام/فایل/آلبوم برای *تمام گروه‌ها* را بفرستید. لغو: /cancel")

@dp.message(GroupBroadcast.waiting_for_message)
async def on_broadcast_to_groups(m: Message, state: FSMContext):
    if m.text and m.text.startswith("/") and m.text != "/cancel":
        return
    if m.chat.type != "private" or not await require_admin_msg(m):
        return

    if m.media_group_id:
        key = (m.from_user.id, m.media_group_id)
        buf = _album_buffer_groups.get(key, [])
        item = _collect_item_from_message(m)
        if item:
            buf.append(item); _album_buffer_groups[key] = buf

        async def _flush():
            await asyncio.sleep(2)
            items = _album_buffer_groups.pop(key, [])
            caption, ents = m.caption or '', m.caption_entities
            chat_ids = await get_group_ids(active_only=True)
            sent = 0
            for gid in chat_ids:
                try:
                    await _send_media_group(bot, gid, items, caption, ents)
                    sent += 1
                except Exception:
                    pass
            await state.clear()
            await m.answer(f"✅ آلبوم برای {sent} گروه ارسال شد.")
        t = _album_tasks_groups.get(key)
        if t and not t.done():
            t.cancel()
        _album_tasks_groups[key] = asyncio.create_task(_flush())
        return

    chat_ids = await get_group_ids(active_only=True)
    sent = 0
    for gid in chat_ids:
        try:
            await bot.copy_message(chat_id=gid, from_chat_id=m.chat.id, message_id=m.message_id)
            await log_message(m.from_user.id, gid, "group_broadcast", m.caption or m.text or m.content_type)
            sent += 1
        except Exception:
            continue
    await state.clear()
    await m.answer(f"✅ ارسال شد برای {sent} گروه.")

@dp.message(Command("replygroup"))
async def cmd_replygroup(m: Message, state: FSMContext):
    if not await require_admin_msg(m):
        return
    await state.set_state(GroupReply.waiting_for_link)
    await m.answer("لینک پیام گروه را ارسال کنید. لغو: /cancel")

@dp.message(GroupReply.waiting_for_link)
async def on_link_received(m: Message, state: FSMContext):
    if not await require_admin_msg(m):
        return
    link = m.text.strip()
    parts = link.split("/")
    if len(parts) < 5 or not parts[-1].isdigit():
        return await m.answer("❌ لینک نامعتبر است. فرمت صحیح: https://t.me/groupname/12345")

    msg_id = int(parts[-1])
    await state.update_data(message_id=msg_id)
    await state.set_state(GroupReply.waiting_for_content)
    await m.answer("✅ لینک دریافت شد. حالا رسانه یا متن پاسخ را ارسال کنید. لغو: /cancel")

@dp.message(GroupReply.waiting_for_content)
async def on_reply_content(m: Message, state: FSMContext):
    if not await require_admin_msg(m):
        return
    data = await state.get_data()
    msg_id = data.get("message_id")
    GROUP_CHAT_ID = -1002761402663  # آیدی عددی گروه خصوصی

    try:
        # ارسال رسانه با copy_message
        await bot.copy_message(
            chat_id=GROUP_CHAT_ID,
            from_chat_id=m.chat.id,
            message_id=m.message_id,
            reply_to_message_id=msg_id
        )
        await m.answer("✅ پاسخ ارسال شد.")
    except Exception as e:
        await m.answer(f"❌ خطا در ارسال پاسخ: {e}")
    await state.clear()

@dp.message(Command("cancel"))
async def cmd_cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("⛔ عملیات لغو شد.")


@dp.message(Command("listgroups"))
async def cmd_listgroups(m: Message):
    if m.chat.type != "private" or not await require_admin_msg(m):
        return
    items = await list_groups(limit=50)
    if not items:
        return await m.answer("هیچ گروه فعالی ثبت نشده است.")
    lines = [f"• {name} — <code>{cid}</code>" for cid, name in items]
    await m.answer("گروه‌های ثبت‌شده (تا ۵۰ مورد اخیر):\n" + "\n".join(lines))

@dp.message(Command("stats"))
async def cmd_stats(m: Message):
    if m.chat.type != "private" or not await require_admin_msg(m):
        return
    assert DB_POOL is not None
    async with DB_POOL.acquire() as conn:
        total_users  = await conn.fetchval("SELECT COUNT(*) FROM users")
        total_groups = await conn.fetchval("SELECT COUNT(*) FROM groups WHERE is_active=TRUE")
    await m.answer(f"📊 کاربران: {total_users}\n👥 گروه‌های فعال: {total_groups}")

@dp.message(Command("addadmin"))
async def cmd_addadmin(m: Message, command: CommandObject):
    if m.chat.type != "private" or not await require_admin_msg(m):
        return
    if not command.args or not command.args.strip().isdigit():
        return await m.answer("فرمت: /addadmin <user_id>")
    await set_admin(int(command.args.strip()), True)
    await m.answer(f"✅ کاربر {command.args.strip()} به عنوان ادمین اضافه شد.")

@dp.message(Command("deladmin"))
async def cmd_deladmin(m: Message, command: CommandObject):
    if m.chat.type != "private" or not await require_admin_msg(m):
        return
    if not command.args or not command.args.strip().isdigit():
        return await m.answer("فرمت: /deladmin <user_id>")
    await set_admin(int(command.args.strip()), False)
    await m.answer(f"✅ دسترسی ادمینی کاربر {command.args.strip()} حذف شد.")

@dp.message(Command("block"))
async def cmd_block(m: Message, command: CommandObject):
    if m.chat.type != "private" or not await require_admin_msg(m):
        return
    if not command.args or not command.args.strip().isdigit():
        return await m.answer("فرمت: /block <user_id>")
    await set_block(int(command.args.strip()), True)
    await m.answer(f"🚫 کاربر {command.args.strip()} بلاک شد.")

@dp.message(Command("unblock"))
async def cmd_unblock(m: Message, command: CommandObject):
    if m.chat.type != "private" or not await require_admin_msg(m):
        return
    if not command.args or not command.args.strip().isdigit():
        return await m.answer("فرمت: /unblock <user_id>")
    await set_block(int(command.args.strip()), False)
    await m.answer(f"♻️ کاربر {command.args.strip()} آنبلاک شد.")

@dp.message(Command("reply"))
async def cmd_reply(m: Message, state: FSMContext, command: CommandObject):
    if m.chat.type != "private" or not await require_admin_msg(m):
        return
    if not command.args or not command.args.strip().isdigit():
        return await m.answer("فرمت: /reply <user_id>")
    target_id = int(command.args.strip())
    await state.set_state(AdminReply.waiting_for_any)
    await state.update_data(target_id=target_id)
    await m.answer(f"در حال پاسخ به کاربر {target_id}. لطفاً پیام/فایل/آلبوم را بفرستید. لغو: /cancel")

# inline reply (buttons)
@dp.callback_query(F.data.startswith(f"{CB_REPLY}|"))
async def cb_reply(call: CallbackQuery, state: FSMContext):
    if call.message.chat.type != "private":
        return
    if not await require_admin_call(call):
        return
    _, uid = call.data.split("|", 1)
    await state.set_state(AdminReply.waiting_for_any)
    await state.update_data(target_id=int(uid))
    await call.message.answer(f"در حال پاسخ به کاربر {uid}. لطفاً پیام/فایل/آلبوم را بفرستید. لغو: /cancel")
    await call.answer()
    await disable_markup(call)

@dp.message(AdminReply.waiting_for_any)
async def on_admin_reply_any(m: Message, state: FSMContext):
    if m.text and m.text.startswith("/") and m.text != "/cancel":
        return
    if m.chat.type != "private" or not await require_admin_msg(m):
        return

    data = await state.get_data()
    target_id = int(data.get("target_id"))

    # آلبوم (عکس/ویدیو)
    if m.media_group_id:
        key = (m.from_user.id, target_id, m.media_group_id)
        buf = _album_buffer_admin_reply.get(key, [])
        item = _collect_item_from_message(m)
        if item:
            buf.append(item); _album_buffer_admin_reply[key] = buf

        async def _flush():
            await asyncio.sleep(2)
            items = _album_buffer_admin_reply.pop(key, [])
            await _send_media_group(bot, target_id, items, m.caption or '', m.caption_entities)
            await log_message(m.from_user.id, target_id, "admin_to_user", f"album({len(items)})")
            await m.answer("✅ ارسال شد.", reply_markup=admin_reply_again_kb(target_id))
            await state.clear()
        t = _album_tasks_admin_reply.get(key)
        if t and not t.done():
            t.cancel()
        _album_tasks_admin_reply[key] = asyncio.create_task(_flush())
        return

    # تک‌پیام (همه‌ی انواع: ویس/ویدیو نوت/عکس/فیلم/داک/لینک/...)
    try:
        await bot.copy_message(chat_id=target_id, from_chat_id=m.chat.id, message_id=m.message_id)
        await log_message(m.from_user.id, target_id, "admin_to_user", m.caption or m.text or m.content_type)
        await m.answer("✅ ارسال شد.", reply_markup=admin_reply_again_kb(target_id))
    except Exception:
        await m.answer("❌ ارسال نشد. شاید کاربر پیوی ربات را باز نکرده.")
    await state.clear()

# -------------------- Rules setters --------------------
class SetRules(StatesGroup):
    waiting_for_text = State()

@dp.message(Command("setrules"))
async def cmd_setrules(m: Message, state: FSMContext, command: CommandObject):
    if m.chat.type != "private" or not await require_admin_msg(m):
        return
    if not command.args:
        return await m.answer("فرمت: /setrules <section> <kind>\nمثال: /setrules souls chat")
    args = command.args.strip().split()
    if len(args) != 2:
        return await m.answer("باید دقیقاً دو آرگومان بدهید: section و kind (مثلاً: souls chat)")
    section, kind = args
    if section not in {"souls", "bots", "vserv"}:
        return await m.answer("section نامعتبر است. یکی از: souls, bots, vserv")
    await state.set_state(SetRules.waiting_for_text)
    await state.update_data(section=section, kind=kind)
    await m.answer(f"متن جدید قوانین برای {section}/{kind} را بفرستید. لغو: /cancel")

@dp.message(Command("setchat"))
async def cmd_setchat(m: Message, state: FSMContext):
    if m.chat.type != "private" or not await require_admin_msg(m):
        return
    await state.set_state(SetRules.waiting_for_text)
    await state.update_data(section="souls", kind="chat")
    await m.answer("متن قوانین جدید برای «چت گروه Souls» را بفرستید. لغو: /cancel")

@dp.message(Command("setcall"))
async def cmd_setcall(m: Message, state: FSMContext):
    if m.chat.type != "private" or not await require_admin_msg(m):
        return
    await state.set_state(SetRules.waiting_for_text)
    await state.update_data(section="souls", kind="call")
    await m.answer("متن قوانین جدید برای «کال گروه Souls» را بفرستید. لغو: /cancel")

@dp.message(Command("setvserv"))
async def cmd_setvserv(m: Message, state: FSMContext):
    if m.chat.type != "private" or not await require_admin_msg(m):
        return
    await state.set_state(SetRules.waiting_for_text)
    await state.update_data(section="vserv", kind="general")
    await m.answer("متن قوانین/شرایط «خدمات مجازی» را بفرستید. لغو: /cancel")

@dp.message(SetRules.waiting_for_text)
async def on_set_rules_text(m: Message, state: FSMContext):
    if m.text and m.text.startswith("/") and m.text != "/cancel":
        return
    if m.chat.type != "private" or not await require_admin_msg(m):
        return
    data = await state.get_data()
    await set_rules(data["section"], data["kind"], m.html_text)
    await state.clear()
    await m.answer("✅ قوانین ذخیره شد.")

@dp.message(Command("cancel"))
async def cmd_cancel(m: Message, state: FSMContext):
    if m.chat.type != "private":
        return
    await state.clear()
    await m.answer("لغو شد.")

# -------------------- User flows (callbacks) --------------------
@dp.callback_query(F.data.startswith(f"{CB_MAIN}|"))
async def on_back_to_menu(call: CallbackQuery, state: FSMContext):
    if call.message.chat.type != "private":
        return
    await disable_markup(call)
    await state.clear()
    await call.message.answer(MAIN_MENU_TEXT, reply_markup=main_menu_kb())
    await call.answer()

@dp.callback_query(F.data.startswith(f"{CB_SEC}|"))
async def on_section(call: CallbackQuery):
    if call.message.chat.type != "private":
        return
    await disable_markup(call)
    _, section = call.data.split("|", 1)

    if section == "souls":
        await call.message.answer("بخش گروه Souls – نوع درخواست را انتخاب کنید:", reply_markup=souls_submenu_kb())

    elif section == "bots":
        rules = await get_rules("bots", "general")
        text = f"{rules}\n\nبرای ارسال پیام درباره ربات‌ها، روی دکمه‌ی زیر بزنید و توضیحات خود را بفرستید."
        await call.message.answer(text, reply_markup=quick_send_kb("bots"))

    elif section == "vserv":
        rules = await get_rules("vserv", "general")
        text = (
            "🛍️ لیست خدمات مجازی:\n"
            f"{VIRTUAL_SERVICES_LIST}\n\n"
            f"{rules}\n\n"
            "برای ثبت درخواست، روی «ارسال پیام» بزنید و سرویس/تعداد/لینک‌ها/زمان‌بندی را بنویسید."
        )
        await call.message.answer(text, reply_markup=quick_send_kb("vserv"))

    elif section == "free":
        text = (
            "🗣️ گفت‌وگوی آزاد\n"
            "سؤال یا موضوع آزادت رو بنویس؛ من به ادمین می‌رسونم و از همین‌جا جواب می‌گیری."
        )
        await call.message.answer(text, reply_markup=quick_send_kb("free"))

    await call.answer()

@dp.callback_query(F.data.startswith(f"{CB_SOULS}|"))
async def on_souls_kind(call: CallbackQuery):
    if call.message.chat.type != "private":
        return
    await disable_markup(call)
    _, kind = call.data.split("|", 1)  # chat or call
    rules = await get_rules("souls", kind)
    await call.message.answer(rules, reply_markup=after_rules_kb(kind))
    await call.answer()

@dp.callback_query(F.data.startswith(f"{CB_ACTION}|"))
async def on_action(call: CallbackQuery, state: FSMContext):
    if call.message.chat.type != "private":
        return
    await disable_markup(call)
    _, action, kind = call.data.split("|", 2)
    if action == "send":
        await state.set_state(SendToAdmin.waiting_for_text)
        await state.update_data(kind=kind)  # bots/vserv/free/chat/call
        await call.message.answer("لطفاً پیام/فایل/آلبوم خود را ارسال کنید. لغو: /cancel")
    else:
        await state.clear()
        await call.message.answer("لغو شد.")
    await call.answer()

@dp.callback_query(F.data.startswith(f"{CB_AGAIN}|"))
async def on_send_again(call: CallbackQuery, state: FSMContext):
    if call.message.chat.type != "private":
        return
    await disable_markup(call)
    await state.set_state(SendToAdmin.waiting_for_text)
    await call.message.answer("متن یا فایل جدید را بفرستید. لغو: /cancel")
    await call.answer()

# -------------------- User -> Admin message (only in state) --------------------
@dp.message(SendToAdmin.waiting_for_text)
async def on_user_message_to_admin(m: Message, state: FSMContext):
    if m.text and m.text.startswith("/") and m.text != "/cancel":
        return
    if m.chat.type != "private":
        return

    u = await get_user(m.from_user.id)
    if u and u.blocked:
        return await m.answer("شما مسدود شده‌اید.")

    data = await state.get_data()
    kind = data.get("kind", "general")  # bots / vserv / free / chat / call
    admin_ids = await get_admin_ids()
    if not admin_ids:
        return await m.answer("فعلاً ادمینی ثبت نشده.")

    full_name = " ".join(filter(None, [m.from_user.first_name, m.from_user.last_name])) or "-"
    uname = ("@" + m.from_user.username) if m.from_user.username else "-"
    info_text = (
        f"📬 پیام جدید از <a href=\"tg://user?id={m.from_user.id}\">{full_name}</a>\n"
        f"🆔 ID: <code>{m.from_user.id}</code>\n"
        f"👤 Username: {uname}\n"
        f"بخش: {kind}\n\n— برای پاسخ از دکمهٔ زیر استفاده کنید —"
    )

    # آلبوم عکس/ویدیو
    if m.media_group_id:
        key = (m.from_user.id, m.media_group_id)
        buf = _album_buffer_u2a.get(key, [])
        item = _collect_item_from_message(m)
        if item:
            buf.append(item); _album_buffer_u2a[key] = buf

        async def _flush():
            await asyncio.sleep(2)
            items = _album_buffer_u2a.pop(key, [])
            caption, ents = m.caption or '', m.caption_entities
            for aid in admin_ids:
                try:
                    kb = admin_reply_kb(m.from_user.id)
                    await bot.send_message(aid, info_text, reply_markup=kb)
                    await _send_media_group(bot, aid, items, caption, ents)
                except Exception:
                    pass
            await log_message(m.from_user.id, None, "user_to_admin", f"album({len(items)})")
            await state.clear()
            await m.answer("✅ درخواست شما برای ادمین‌ها ارسال شد.", reply_markup=send_again_kb())
        t = _album_tasks_u2a.get(key)
        if t and not t.done():
            t.cancel()
        _album_tasks_u2a[key] = asyncio.create_task(_flush())
        return

    # تک‌پیام (همه انواع)
    for aid in admin_ids:
        try:
            kb = admin_reply_kb(m.from_user.id)
            await bot.send_message(aid, info_text, reply_markup=kb)
            await bot.copy_message(chat_id=aid, from_chat_id=m.chat.id, message_id=m.message_id, reply_markup=kb)
        except Exception:
            pass

    await log_message(m.from_user.id, None, "user_to_admin", m.caption or m.text or m.content_type)
    await state.clear()
    await m.answer("✅ درخواست شما برای ادمین‌ها ارسال شد.", reply_markup=send_again_kb())

# -------------------- Group behavior & registration --------------------
@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def group_gate(m: Message):
    await upsert_group(
        chat_id=m.chat.id,
        title=getattr(m.chat, "title", None),
        username=getattr(m.chat, "username", None),
        active=True
    )

    text = (m.text or m.caption or "")
    if contains_malek(text):
        btns = None
        if BOT_USERNAME:
            btns = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="پیام به منشی مالک",
                    url=f"https://t.me/{BOT_USERNAME}?start=start"
                )]
            ])

        # ⬇️ پیام ربات
        sent = await m.reply(
            "سلام، من منشی مالک هستم. می‌تونی پیوی من پیام بدی و من به مالک برسونمش.",
            reply_markup=btns
        )
        # ⬇️ حذف خودکار همون پیام بعد از ۳۰ ثانیه
        asyncio.create_task(_auto_delete(sent.chat.id, sent.message_id, delay=30))

# فقط پی‌وی: فالبک غیر دستوری (وقتی در حالت خاصی نیستیم)
@dp.message(F.chat.type == "private", F.text, ~F.text.regexp(r"^/"))
async def private_fallback(m: Message, state: FSMContext):
    if await state.get_state():
        return
    await m.answer("برای شروع از /menu استفاده کنید.")

# -------------------- Entrypoint --------------------
async def main():
    global BOT_USERNAME, DB_POOL
    await init_db()
    me = await bot.get_me()
    BOT_USERNAME = me.username or ""
    logging.info(f"Bot connected as @{BOT_USERNAME}")
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        if DB_POOL:
            await DB_POOL.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Bot stopped.")






