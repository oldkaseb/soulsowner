from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from config import ADMIN_ID
from utils import format_request, save_log, format_user_info, load_blocked_users, save_blocked_users

user_state = {}  # user_id: {"category": "انتقاد", "step": "awaiting_text"}
blocked_users = load_blocked_users()
request_categories = {
    "suggestion": "انتقاد و پیشنهاد",
    "admin_request": "درخواست ادمینی",
    "sponsorship": "پیشنهاد اسپانسری",
    "complaint": "شکایت",
    "confession": "اعتراف"
}

def get_main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 انتقاد و پیشنهاد", callback_data="cat_suggestion")],
        [InlineKeyboardButton("🙋 درخواست ادمینی", callback_data="cat_admin_request")],
        [InlineKeyboardButton("💰 پیشنهاد اسپانسری", callback_data="cat_sponsorship")],
        [InlineKeyboardButton("⚠️ شکایت", callback_data="cat_complaint")],
        [InlineKeyboardButton("😶 اعتراف", callback_data="cat_confession")],
        [InlineKeyboardButton("📜 قوانین ارتباط با مدیریت", callback_data="rules_main")]
    ])

def get_admin_type_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎙 ادمین کال", callback_data="admin_call")],
        [InlineKeyboardButton("💬 ادمین چت", callback_data="admin_chat")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")]
    ])

def back_button():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="back_main")]])

def get_rules_text_for(role: str):
    file = "rules_chat.txt" if role == "chat" else "rules_call.txt"
    with open(file, "r", encoding="utf-8") as f:
        return f.read()

def setup_handlers(app: Client):

    @app.on_message(filters.command("start"))
    async def start_handler(client, message: Message):
        if message.from_user.id in blocked_users:
            return await message.reply("دسترسی شما به این ربات مسدود شده است.")
        await message.reply("به ربات ارتباط با مدیریت خوش آمدید. لطفاً یک گزینه انتخاب کنید:", reply_markup=get_main_menu())

    @app.on_callback_query()
    async def callback_handler(client, callback: CallbackQuery):
        user_id = callback.from_user.id
        data = callback.data

        if user_id in blocked_users:
            return await callback.message.edit_text("دسترسی شما به این ربات مسدود شده است.")

        if data.startswith("cat_"):
            category_key = data.split("_")[1]
            if category_key == "admin_request":
                await callback.message.edit_text("نوع ادمینی را انتخاب کنید:", reply_markup=get_admin_type_menu())
            else:
                user_state[user_id] = {"category": request_categories[category_key]}
                await callback.message.edit_text(f"لطفاً پیام خود را در زمینه «{request_categories[category_key]}» بنویسید.", reply_markup=back_button())

        elif data == "admin_call":
            user_state[user_id] = {"category": "ادمین کال"}
            await callback.message.edit_text(get_rules_text_for("call"))
            await callback.message.reply("در صورت موافقت با قوانین بالا، لطفاً درخواست خود را ارسال کنید.")

        elif data == "admin_chat":
            user_state[user_id] = {"category": "ادمین چت"}
            await callback.message.edit_text(get_rules_text_for("chat"))
            await callback.message.reply("در صورت موافقت با قوانین بالا، لطفاً درخواست خود را ارسال کنید.")

        elif data == "rules_main":
            await callback.message.edit_text("قوانین کلی ارتباط با مدیریت:\nارتباط شما ثبت و به صورت خصوصی بررسی می‌شود.\nبرای برخی بخش‌ها قوانین اختصاصی نمایش داده خواهد شد.", reply_markup=back_button())

        elif data == "back_main":
            await callback.message.edit_text("به منوی اصلی برگشتید:", reply_markup=get_main_menu())

        elif data.startswith("block_"):
            to_block = int(data.split("_")[1])
            blocked_users.add(to_block)
            save_blocked_users(blocked_users)
            await callback.message.reply(f"کاربر {to_block} بلاک شد.")

    @app.on_message(filters.private & filters.text)
    async def text_handler(client, message: Message):
        user_id = message.from_user.id

        if user_id in blocked_users:
            return await message.reply("دسترسی شما به این ربات مسدود شده است.")

        if user_id not in user_state:
            return await message.reply("لطفاً ابتدا یک گزینه از منوی اصلی انتخاب کنید.", reply_markup=get_main_menu())

        category = user_state[user_id]["category"]
        del user_state[user_id]

        full_text = format_request(message.from_user, category, message.text)
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚫 بلاک کاربر", callback_data=f"block_{user_id}")]
        ])

        await client.send_message(ADMIN_ID, full_text, reply_markup=buttons)
        await message.reply("درخواست شما با موفقیت برای مدیریت ارسال شد. منتظر پاسخ بمانید.")
        save_log(user_id, category, message.text)

    @app.on_message(filters.reply & filters.user(ADMIN_ID))
    async def reply_handler(client, message: Message):
        if not message.reply_to_message or "ID:" not in message.reply_to_message.text:
            return await message.r
