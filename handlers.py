from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton
from config import ADMIN_ID
from utils import format_request, save_log, format_user_info, load_blocked_users, save_blocked_users

user_state = {}
blocked_users = load_blocked_users()
reply_state = {}  # admin_id: user_id → برای حالت پاسخ‌دهی هدایت‌شده

request_categories = {
    "suggestion": "انتقاد و پیشنهاد",
    "admin_request": "درخواست ادمینی",
    "sponsorship": "پیشنهاد اسپانسری",
    "complaint": "شکایت",
    "confession": "اعتراف",
    "freechat": "گفت‌وگوی آزاد"
}

def get_main_menu_inline():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 انتقاد و پیشنهاد", callback_data="cat_suggestion")],
        [InlineKeyboardButton("🙋 درخواست ادمینی", callback_data="cat_admin_request")],
        [InlineKeyboardButton("💰 پیشنهاد اسپانسری", callback_data="cat_sponsorship")],
        [InlineKeyboardButton("⚠️ شکایت", callback_data="cat_complaint")],
        [InlineKeyboardButton("😶 اعتراف", callback_data="cat_confession")],
        [InlineKeyboardButton("🗣 گفت‌وگوی آزاد", callback_data="cat_freechat")]
    ])

def get_reply_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("🏠 بازگشت به منوی اصلی")]],
        resize_keyboard=True,
        one_time_keyboard=False
    )

def get_admin_type_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎙 ادمین کال", callback_data="admin_call")],
        [InlineKeyboardButton("💬 ادمین چت", callback_data="admin_chat")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")]
    ])

def get_rules_text_for(role: str):
    file = "rules_chat.txt" if role == "chat" else "rules_call.txt"
    with open(file, "r", encoding="utf-8") as f:
        return f.read()

def setup_handlers(app: Client):

    @app.on_message(filters.command("start"))
    async def start_handler(client, message: Message):
        if message.from_user.id in blocked_users:
            return await message.reply("❌ شما توسط مدیریت بلاک شده‌اید.")
        await message.reply(
            "به ربات ارتباط با مدیریت خوش آمدید.\nیکی از گزینه‌های زیر را انتخاب کنید:",
            reply_markup=get_main_menu_inline()
        )

    @app.on_message(filters.text & filters.private & ~filters.command(["start", "stats"]))
    async def text_handler(client, message: Message):
        user_id = message.from_user.id
        text = message.text.strip()

        # پاسخ از ادمین در حالت هدایت‌شده
        if message.from_user.id == ADMIN_ID and ADMIN_ID in reply_state:
            target_user = reply_state.pop(ADMIN_ID)
            try:
                await client.send_message(target_user, f"📩 پاسخ مدیریت:\n{message.text}")
                await message.reply("✅ پاسخ برای کاربر ارسال شد.")
            except Exception as e:
                await message.reply(f"❌ خطا در ارسال پاسخ: {e}")
            return

        # بازگشت به منو
        if text == "🏠 بازگشت به منوی اصلی":
            return await message.reply(
                "بازگشت به منوی اصلی:",
                reply_markup=get_main_menu_inline()
            )

        # اگر بلاک‌شده بود
        if user_id in blocked_users:
            return await message.reply("❌ دسترسی شما به این ربات مسدود شده است.")

        # کاربر هیچ گزینه‌ای انتخاب نکرده
        if user_id not in user_state:
            return await message.reply(
                "لطفاً ابتدا یک گزینه از منو انتخاب کنید.",
                reply_markup=get_main_menu_inline()
            )

        category = user_state[user_id]["category"]
        del user_state[user_id]

        full_text = format_request(message.from_user, category, text)
        buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✉️ پاسخ به کاربر", callback_data=f"replyto_{user_id}"),
                InlineKeyboardButton("🚫 بلاک کاربر", callback_data=f"block_{user_id}")
            ]
        ])

        await client.send_message(ADMIN_ID, full_text, reply_markup=buttons)
        await message.reply("✅ پیام شما با موفقیت برای مدیریت ارسال شد. لطفاً منتظر پاسخ باشید.")
        save_log(user_id, category, text)

    @app.on_callback_query()
    async def callback_handler(client, callback: CallbackQuery):
        user_id = callback.from_user.id
        data = callback.data

        if user_id in blocked_users:
            return await callback.message.edit_text("❌ دسترسی شما به این ربات مسدود شده است.")

        # دکمه‌های دسته‌بندی
        if data.startswith("cat_"):
            cat_key = data.replace("cat_", "")
            if cat_key == "admin_request":
                await callback.message.edit_text("لطفاً نوع ادمین مورد نظر را انتخاب کنید:", reply_markup=get_admin_type_menu())
            else:
                user_state[user_id] = {"category": request_categories[cat_key]}
                await callback.message.edit_text(
                    f"لطفاً پیام خود را در بخش «{request_categories[cat_key]}» بنویسید."
                )
                await callback.message.reply("منتظر پیام شما هستم...", reply_markup=get_reply_keyboard())

        # انتخاب نوع ادمین
        elif data in ["admin_call", "admin_chat"]:
            role = "call" if data == "admin_call" else "chat"
            user_state[user_id] = {"category": f"ادمین {'کال' if role == 'call' else 'چت'}"}
            await callback.message.edit_text(get_rules_text_for(role))
            await callback.message.reply("در صورت موافقت با قوانین بالا، لطفاً درخواست خود را ارسال کنید.", reply_markup=get_reply_keyboard())

        elif data == "back_main":
            await callback.message.edit_text("بازگشت به منوی اصلی:", reply_markup=get_main_menu_inline())

        # بلاک کاربر
        elif data.startswith("block_"):
            block_id = int(data.split("_")[1])
            blocked_users.add(block_id)
            save_blocked_users(blocked_users)
            await callback.message.reply(f"✅ کاربر `{block_id}` بلاک شد.")

        # پاسخ به کاربر
        elif data.startswith("replyto_"):
            target_user = int(data.split("_")[1])
            reply_state[ADMIN_ID] = target_user
            await callback.message.reply("✉️ لطفاً پاسخ خود را بنویس. این پیام به کاربر ارسال خواهد شد.")

    @app.on_message(filters.command("stats") & filters.user(ADMIN_ID))
    async def stats_handler(client, message: Message):
        try:
            import json
            with open("logs.json", "r", encoding="utf-8") as f:
                logs = json.load(f)
        except:
            logs = []

        total = len(logs)
        blocked = len(blocked_users)
        await message.reply(f"📊 آمار:\n- کل درخواست‌ها: {total}\n- کاربران بلاک‌شده: {blocked}")
