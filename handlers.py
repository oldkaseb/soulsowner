from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from config import ADMIN_ID
from utils import format_request

request_state = {}  # user_id: "chat" or "call"

def get_main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📜 قوانین ارتباط با مدیریت", callback_data="rules")],
        [InlineKeyboardButton("🙋 درخواست ادمینی", callback_data="req_admin")]
    ])

def get_admin_type_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎙 ادمین کال", callback_data="admin_call")],
        [InlineKeyboardButton("💬 ادمین چت", callback_data="admin_chat")]
    ])

def rules_text():
    return "قوانین کلی ربات:\n...\n(در این نسخه فقط برای نمایش اولیه است)"

def get_rules_text_for(role: str):
    with open("rules_chat.txt" if role == "chat" else "rules_call.txt", "r", encoding="utf-8") as f:
        return f.read()

def setup_handlers(app: Client):
    @app.on_message(filters.command("start"))
    async def start_handler(client, message: Message):
        await message.reply("به ربات ارتباط با مدیریت خوش آمدید.\nیکی از گزینه‌های زیر را انتخاب کنید:", reply_markup=get_main_menu())

    @app.on_callback_query()
    async def callback_handler(client, callback):
        data = callback.data
        if data == "rules":
            await callback.message.edit_text(rules_text(), reply_markup=get_main_menu())

        elif data == "req_admin":
            await callback.message.edit_text("لطفاً نوع ادمینی که می‌خواین درخواست بدین رو انتخاب کنین:", reply_markup=get_admin_type_menu())

        elif data in ["admin_chat", "admin_call"]:
            role = "چت" if data == "admin_chat" else "کال"
            request_state[callback.from_user.id] = role.lower()
            await callback.message.edit_text(get_rules_text_for(role.lower()))
            await callback.message.reply("اگه با قوانین موافقی، لطفاً درخواست خودتو بنویس.")

    @app.on_message(filters.private & filters.text)
    async def text_handler(client, message: Message):
        user_id = message.from_user.id
        if user_id in request_state:
            role = request_state.pop(user_id)
            text = format_request(message.from_user, role, message.text)
            await client.send_message(ADMIN_ID, text)
            await message.reply("درخواست شما با موفقیت برای مدیریت ارسال شد.")
        else:
            await message.reply("برای ارسال درخواست، ابتدا گزینه 'درخواست ادمینی' را انتخاب کنید.")
