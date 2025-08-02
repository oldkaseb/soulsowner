import json
from datetime import datetime

def format_user_info(user):
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    username = f"@{user.username}" if user.username else "بدون یوزرنیم"
    return full_name, username

def format_request(user, category, text):
    full_name, username = format_user_info(user)
    return (
        f"📥 دسته: {category}\n"
        f"👤 نام: {full_name}\n"
        f"🆔 یوزرنیم: {username} | ID: {user.id}\n\n"
        f"📝 پیام:\n{text}"
    )

def save_log(user_id, category, text):
    try:
        with open("logs.json", "r", encoding="utf-8") as f:
            logs = json.load(f)
    except FileNotFoundError:
        logs = []

    logs.append({
        "user_id": user_id,
        "category": category,
        "text": text,
        "timestamp": datetime.now().isoformat()
    })

    with open("logs.json", "w", encoding="utf-8") as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)

def load_blocked_users():
    try:
        with open("blocked.json", "r") as f:
            return set(json.load(f))
    except FileNotFoundError:
        return set()

def save_blocked_users(blocked_set):
    with open("blocked.json", "w") as f:
        json.dump(list(blocked_set), f)
