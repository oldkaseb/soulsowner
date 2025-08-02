from pyrogram import Client
from config import API_ID, API_HASH, BOT_TOKEN
from handlers import setup_handlers

app = Client("admin_request_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

setup_handlers(app)

print("ربات با موفقیت اجرا شد.")
app.run()
