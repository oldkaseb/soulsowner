# -*- coding: utf-8 -*-
"""
Single-file Telegram bot (aiogram v3) with PostgreSQL (asyncpg)

ENV VARS (Railway):
  BOT_TOKEN="..."
  DATABASE_URL="postgresql://user:pass@host:port/dbname"
  ADMIN_ID="123456, 987654"    # می‌تواند یک یا چند آیدی باشد (با کاما/فاصله)

Notes:
- API_ID و API_HASH برای این کد لازم نیست (مخصوص Pyrogram/Telethon هستند).
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
ADMIN_ID_RAW = os.getenv("ADMIN_ID", os.getenv("ADMIN_SEED_IDS", "")).strip()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN env var is required")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL env var is required")

# Globals
DB_POOL: Optional[asyncpg.Pool] = None
BOT_USERNAME: str = ""

# -------------------- Text Constants (fa-IR) --------------------
WELCOME_TEXT = """سلام! 👋
به ربات ارتباطی خوش اومدی. یکی از بخش‌ها رو انتخاب کن:"""

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
    waiting_for_message = State()  # broadcast to USERS (any content, incl. albums)

class GroupBroadcast(StatesGroup):
    waiting_for_message = State()  # broadcast to GROUPS (any content, incl. albums)

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
    direction TEXT NOT NULL,   -- user_to_admin | admin_to_user | broadcast | group_broadcast
    content   TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS groups (
    chat_id  BIGINT PRIMARY KEY,
    title    TEXT,
    username TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    added_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

DEFAULT_RULES: List[Tuple[str, str, str]] = [
    ("group", "chat", """قوانین ادمین‌های چت:

1. مهم‌ترین قانون، رعایت ادب در برابر ممبرهاست تا بی‌احترامی یا گستاخی نبینید. شوخی‌ها فقط در نجوا انجام شود.

2. هر ادمین چت موظف است روزانه حداقل 800 پیام ارسال کند. در صورت نرسیدن به این آمار:
   - بار اول: اخطار
   - بار دوم: اخطار دوم
   - بار سوم: عزل در صورت نداشتن دلیل منطقی

3. در برخورد با ممبر بی‌ادب (توهین، فحاشی):
   - مرحله اول: اخطار
   - مرحله دوم: سکوت
   - مرحله سوم: بن در پیوی
   سپس، تمام پیام‌های بحث پاک‌سازی و شات برای گارد ارسال شود.

4. در صورت بروز بحث میان ادمین‌ها، فقط مالک یا ادمین ارشد اجازه دخالت دارد. ارائه شهادت فقط در پیوی مالک یا ارشد انجام شود.

5. هنگام ورود به گروه باید علامت ✅ و هنگام اف شدن باید علامت ❌ جهت اطلاع به مالک ارسال شود.

6. چت نباید بدون ادمین باشد. در صورت اف شدن، باید چت به ادمین بعدی تحویل داده شود و در گارد اعلام شود.

7. ادمین چت موظف است در بازی‌های کال شرکت کرده و ممبرها را تگ کند تا به شرکت در بازی ترغیب شوند.

8. هیچ‌کس به‌جز مالک گروه اجازه ویژه دائم یا رهایی کاربران را ندارد.

9. استفاده مداوم از ربات‌های چالش و بازی جهت فعال نگه داشتن فضا الزامی است.

10. مسائل شخصی نباید به گروه منتقل شود.

11. در تایم عضوگیری، حضور ادمین‌ها الزامی است. در صورت غیبت، باید با مالک هماهنگ شود.

12. ادمین چت دسترسی به کال ندارد و نباید در وظایف ادمین کال دخالت کند. مدیریت چت بر عهده شماست."""),
    ("group", "call", """قوانین ادمین‌های کال:

1. رعایت ادب در برابر ممبرها الزامی است. بی‌احترامی به هیچ وجه پذیرفته نیست.

2. هر ادمین کال موظف است حداقل 5 ساعت در روز در کال حضور مؤثر داشته باشد، با ممبرها گفتگو کند، خوش‌آمد بگوید و از همه درخواست مایک کند.

3. ران کردن بازی‌ها به‌ویژه بازی شب مهم‌ترین وظیفه است. بازی شب ساعت 10:30 ران می‌شود و حضور از ساعت 10 الزامی است.

4. برخورد با ممبر بی‌ادب (توهین، فحاشی):
   - مرحله اول: بستن مایک و آرام‌سازی
   - در صورت تکرار: بن با ربات از کف گروه

5. در صورت بروز بحث میان ادمین‌ها، فقط مالک یا ادمین ارشد حق دخالت دارد. شهادت صرفاً در پیوی مالک یا ارشد ارائه شود.

6. هنگام ورود به گروه باید علامت ✅ و هنگام اف شدن باید علامت ❌ جهت اطلاع به مالک ارسال شود.

7. هر ادمین کال دارای تایتل اختصاصی است که باید هنگام حضور در کال از آن استفاده کند. تایتل‌ها باید ذخیره شده و دقیق درج شوند.

8. کال نباید بدون ادمین باشد. در صورت اف شدن، باید به ادمین بعدی تحویل داده شده و این موضوع در گارد اعلام شود. ادمین بعدی نیز باید تأیید کند و تایتل جدید درج نماید.

9. ادمین کال موظف است در بازی‌ها حضور فعال داشته و همراه با ادمین‌های چت، ممبرها را به شرکت در بازی تشویق کند.

10. ادمین کال حق ویژه کردن کاربران را ندارد. در صورت نیاز، باید از ادمین چت درخواست کند و مطابق با قوانین اقدام کند.

11. هر ادمین کال باید روزانه حداقل 300 پیام دعوت به کال ارسال کند (با تگ یا ریپلای).

12. مسائل شخصی نباید به گروه منتقل شود.

13. در تایم عضوگیری، حضور الزامی است. در صورت عدم توانایی، باید با مالک هماهنگ شود.

14. ادمین‌های کال نباید در کار ادمین‌های چت دخالت کنند. مسئولیت کال فقط بر عهده شماست."""),
    ("bots", "general", "قوانین ارتباط با ربات‌ها: ابتدا شناسه ربات و مشکل را دقیق بنویسید."),
    ("vserv", "general", "قوانین خدمات مجازی: نوع سرویس و توضیحات کامل را ارسال کنید."),
]

async def init_db():
    global DB_POOL
    DB_POOL = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    async with DB_POOL.acquire() as conn:
        await conn.execute(CREATE_SQL)
        # default rules
        for section, kind, text in DEFAULT_RULES:
            await conn.execute(
                """
                INSERT INTO rules(section, kind, text)
                VALUES($1,$2,$3)
                ON CONFLICT (section, kind) DO NOTHING
                """,
                section, kind, text,
            )
        # seed admins
        if ADMIN_ID_RAW:
            nums = [n for n in ADMIN_ID_RAW.replace(",", " ").split() if n.isdigit()]
            for uid in map(int, nums):
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

# ---- groups table helpers ----
async def upsert_group(chat_id: int, title: Optional[str], username: Optional[str], active: bool = True):
    assert DB_POOL is not None
    async with DB_POOL.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO groups(chat_id, title, username, is_active)
            VALUES($1,$2,$3,$4)
            ON CONFLICT (chat_id) DO UPDATE
            SET title=EXCLUDED.title, username=EXCLUDED.username, is_active=EXCLUDED.is_active, updated_at=NOW()
            """,
            chat_id, title, username, active
        )

async def get_group_ids(active_only: bool = True) -> List[int]:
    assert DB_POOL is not None
    async with DB_POOL.acquire() as conn:
        if active_only:
            rows = await conn.fetch("SELECT chat_id FROM groups WHERE is_active=TRUE")
        else:
            rows = await conn.fetch("SELECT chat_id FROM groups")
    return [r[0] for r in rows]

async def list_groups(limit: int = 50) -> List[Tuple[int, str]]:
    assert DB_POOL is not None
    async with DB_POOL.acquire() as conn:
        rows = await conn.fetch(
            "SELECT chat_id, COALESCE(title, username, chat_id::text) AS name FROM groups WHERE is_active=TRUE ORDER BY updated_at DESC LIMIT $1",
            limit
        )
    return [(r[0], r[1]) for r in rows]

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

# -------------------- Album Buffers --------------------
# key: (admin_id, media_group_id)
_album_buffer_users: Dict[tuple, List[Dict[str, Any]]] = {}
_album_tasks_users: Dict[tuple, asyncio.Task] = {}

_album_buffer_groups: Dict[tuple, List[Dict[str, Any]]] = {}
_album_tasks_groups: Dict[tuple, asyncio.Task] = {}

# -------------------- Bot Setup --------------------
bot = Bot(BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()

# -------------------- Public Commands (Private only) --------------------
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

@dp.message(Command("help"))
async def cmd_help(m: Message):
    if m.chat.type != "private":
        return
    text = (
        "دستورات کاربری:\n"
        "/start /menu /help\n\n"
        "دستورات ادمین:\n"
        "/broadcast – پیام همگانی به کاربران (همۀ انواع فایل/آلبوم)\n"
        "/groupsend – پیام به تمام گروه‌ها (همۀ انواع فایل/آلبوم)\n"
        "/listgroups – لیست گروه‌های ثبت‌شده\n"
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

# -------------------- Admin Guard --------------------
async def require_admin(message: Message) -> bool:
    u = await get_user(message.from_user.id)
    if not (u and u.is_admin):
        await message.answer("⛔ این دستور مخصوص ادمین‌هاست.")
        return False
    return True

# -------------------- Admin Commands: Users Broadcast --------------------
@dp.message(Command("broadcast"))
async def cmd_broadcast(m: Message, state: FSMContext):
    if m.chat.type != "private":
        return
    if not await require_admin(m):
        return
    await state.set_state(Broadcast.waiting_for_message)
    await m.answer("پیام/فایل/آلبوم مورد نظر برای ارسال همگانی به *کاربران* را بفرستید. لغو: /cancel")

async def _send_media_group_to_chats(chat_ids: List[int], items: List[Dict[str, Any]], caption, caption_entities):
    sent = 0
    for cid in chat_ids:
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
            await bot.send_media_group(cid, media)
            sent += 1
        except Exception:
            continue
    return sent

@dp.message(Broadcast.waiting_for_message)
async def on_broadcast_to_users(m: Message, state: FSMContext):
    if m.chat.type != "private" or not await require_admin(m):
        return

    # Handle albums
    if m.media_group_id:
        key = (m.from_user.id, m.media_group_id)
        buf = _album_buffer_users.get(key, [])
        item = None
        if m.photo:    item = {'type': 'photo', 'file_id': m.photo[-1].file_id}
        elif m.video:  item = {'type': 'video', 'file_id': m.video.file_id}
        elif m.document: item = {'type': 'document', 'file_id': m.document.file_id}
        elif m.animation: item = {'type': 'animation', 'file_id': m.animation.file_id}
        elif m.audio:  item = {'type': 'audio', 'file_id': m.audio.file_id}
        if item:
            buf.append(item)
            _album_buffer_users[key] = buf

        async def _flush():
            await asyncio.sleep(2)
            items = _album_buffer_users.pop(key, [])
            caption = m.caption or ''
            caption_entities = m.caption_entities
            # recipients: users (not blocked)
            assert DB_POOL is not None
            async with DB_POOL.acquire() as conn:
                rows = await conn.fetch("SELECT user_id FROM users WHERE blocked=FALSE")
            chat_ids = [r[0] for r in rows]
            sent = await _send_media_group_to_chats(chat_ids, items, caption, caption_entities)
            await state.clear()
            await m.answer(f"✅ آلبوم برای {sent} کاربر ارسال شد.")

        t = _album_tasks_users.get(key)
        if t and not t.done():
            t.cancel()
        _album_tasks_users[key] = asyncio.create_task(_flush())
        return

    # Single message copy
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

# -------------------- Admin Commands: GROUPS Broadcast --------------------
@dp.message(Command("groupsend"))
async def cmd_groupsend(m: Message, state: FSMContext):
    if m.chat.type != "private":
        return
    if not await require_admin(m):
        return
    await state.set_state(GroupBroadcast.waiting_for_message)
    await m.answer("پیام/فایل/آلبوم مورد نظر برای ارسال به *همه گروه‌ها* را بفرستید. لغو: /cancel")

@dp.message(GroupBroadcast.waiting_for_message)
async def on_broadcast_to_groups(m: Message, state: FSMContext):
    if m.chat.type != "private" or not await require_admin(m):
        return

    # Albums
    if m.media_group_id:
        key = (m.from_user.id, m.media_group_id)
        buf = _album_buffer_groups.get(key, [])
        item = None
        if m.photo:    item = {'type': 'photo', 'file_id': m.photo[-1].file_id}
        elif m.video:  item = {'type': 'video', 'file_id': m.video.file_id}
        elif m.document: item = {'type': 'document', 'file_id': m.document.file_id}
        elif m.animation: item = {'type': 'animation', 'file_id': m.animation.file_id}
        elif m.audio:  item = {'type': 'audio', 'file_id': m.audio.file_id}
        if item:
            buf.append(item)
            _album_buffer_groups[key] = buf

        async def _flush():
            await asyncio.sleep(2)
            items = _album_buffer_groups.pop(key, [])
            caption = m.caption or ''
            caption_entities = m.caption_entities
            chat_ids = await get_group_ids(active_only=True)
            sent = await _send_media_group_to_chats(chat_ids, items, caption, caption_entities)
            await state.clear()
            await m.answer(f"✅ آلبوم برای {sent} گروه ارسال شد.")

        t = _album_tasks_groups.get(key)
        if t and not t.done():
            t.cancel()
        _album_tasks_groups[key] = asyncio.create_task(_flush())
        return

    # Single message copy to each group
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

@dp.message(Command("listgroups"))
async def cmd_listgroups(m: Message):
    if m.chat.type != "private":
        return
    if not await require_admin(m):
        return
    items = await list_groups(limit=50)
    if not items:
        return await m.answer("هیچ گروه فعالی ثبت نشده است.")
    lines = [f"• {name} — <code>{cid}</code>" for cid, name in items]
    await m.answer("گروه‌های ثبت‌شده (تا ۵۰ مورد اخیر):\n" + "\n".join(lines))

# -------------------- Admin Commands: misc --------------------
@dp.message(Command("stats"))
async def cmd_stats(m: Message):
    if m.chat.type != "private":
        return
    if not await require_admin(m):
        return
    assert DB_POOL is not None
    async with DB_POOL.acquire() as conn:
        total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
        total_groups = await conn.fetchval("SELECT COUNT(*) FROM groups WHERE is_active=TRUE")
    await m.answer(f"📊 کاربران: {total_users}\n👥 گروه‌های فعال: {total_groups}")

@dp.message(Command("addadmin"))
async def cmd_addadmin(m: Message, command: CommandObject):
    if m.chat.type != "private":
        return
    if not await require_admin(m):
        return
    if not command.args or not command.args.strip().isdigit():
        return await m.answer("فرمت: /addadmin <user_id>")
    await set_admin(int(command.args.strip()), True)
    await m.answer(f"✅ کاربر {command.args.strip()} به عنوان ادمین اضافه شد.")

@dp.message(Command("deladmin"))
async def cmd_deladmin(m: Message, command: CommandObject):
    if m.chat.type != "private":
        return
    if not await require_admin(m):
        return
    if not command.args or not command.args.strip().isdigit():
        return await m.answer("فرمت: /deladmin <user_id>")
    await set_admin(int(command.args.strip()), False)
    await m.answer(f"✅ دسترسی ادمینی کاربر {command.args.strip()} حذف شد.")

@dp.message(Command("block"))
async def cmd_block(m: Message, command: CommandObject):
    if m.chat.type != "private":
        return
    if not await require_admin(m):
        return
    if not command.args or not command.args.strip().isdigit():
        return await m.answer("فرمت: /block <user_id>")
    await set_block(int(command.args.strip()), True)
    await m.answer(f"🚫 کاربر {command.args.strip()} بلاک شد.")

@dp.message(Command("unblock"))
async def cmd_unblock(m: Message, command: CommandObject):
    if m.chat.type != "private":
        return
    if not await require_admin(m):
        return
    if not command.args or not command.args.strip().isdigit():
        return await m.answer("فرمت: /unblock <user_id>")
    await set_block(int(command.args.strip()), False)
    await m.answer(f"♻️ کاربر {command.args.strip()} آنبلاک شد.")

@dp.message(Command("reply"))
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

@dp.message(Command("setrules"))
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

@dp.message(Command("setchat"))
async def cmd_setchat(m: Message, state: FSMContext):
    if m.chat.type != "private":
        return
    if not await require_admin(m):
        return
    await state.set_state(SetRules.waiting_for_text)
    await state.update_data(section="group", kind="chat")
    await m.answer("متن قوانین جدید برای «چت گروه» را بفرستید. لغو: /cancel")

@dp.message(Command("setcall"))
async def cmd_setcall(m: Message, state: FSMContext):
    if m.chat.type != "private":
        return
    if not await require_admin(m):
        return
    await state.set_state(SetRules.waiting_for_text)
    await state.update_data(section="group", kind="call")
    await m.answer("متن قوانین جدید برای «کال گروه» را بفرستید. لغو: /cancel")

@dp.message(Command("setvserv"))
async def cmd_setvserv(m: Message, state: FSMContext):
    if m.chat.type != "private":
        return
    if not await require_admin(m):
        return
    await state.set_state(SetRules.waiting_for_text)
    await state.update_data(section="vserv", kind="general")
    await m.answer("متن قوانین/شرایط «خدمات مجازی» را بفرستید. لغو: /cancel")

@dp.message(Command("cancel"))
async def cmd_cancel(m: Message, state: FSMContext):
    if m.chat.type != "private":
        return
    await state.clear()
    await m.answer("لغو شد.")

# -------------------- States Handlers --------------------
@dp.message(AdminReply.waiting_for_text)
async def on_admin_reply(m: Message, state: FSMContext):
    if m.chat.type != "private" or not await require_admin(m):
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
    if m.chat.type != "private" or not await require_admin(m):
        return
    data = await state.get_data()
    await set_rules(data["section"], data["kind"], m.html_text)
    await state.clear()
    await m.answer("✅ قوانین ذخیره شد.")

# -------------------- Group Behavior + Registration --------------------
@dp.message()
async def group_gate(m: Message):
    # ثبت گروه‌ها به‌محض دریافت هر پیام از گروه
    if m.chat.type in ("group", "supergroup"):
        await upsert_group(
            chat_id=m.chat.id,
            title=getattr(m.chat, "title", None),
            username=getattr(m.chat, "username", None),
            active=True
        )
        text = (m.text or m.caption or "")
        if "مالک" in text:
            btns = None
            if BOT_USERNAME:
                btns = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="شروع گفتگو در پی‌وی", url=f"https://t.me/{BOT_USERNAME}?start=start")]
                ])
            await m.reply("سلام! برای ارتباط مستقیم، لطفاً به پی‌وی ربات پیام بدید. 👇", reply_markup=btns)
        return

    # در پی‌وی اگر پیام دستور نبود، یک راهنما بده
    if m.chat.type == "private" and not (m.text or "").startswith("/"):
        await m.answer("برای شروع از /menu استفاده کنید.")

# -------------------- Entrypoint --------------------
async def main():
    global BOT_USERNAME, DB_POOL
    await init_db()
    me = await bot.get_me()
    BOT_USERNAME = me.username or ""
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
