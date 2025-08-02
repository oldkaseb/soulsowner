def format_request(user, request_type, text):
    mention = f"@{user.username}" if user.username else "No Username"
    return (
        f"📥 درخواست ادمینی ({request_type})\n"
        f"👤 از طرف: {mention}\n"
        f"🆔 ID: {user.id}\n\n"
        f"📝 پیام:\n{text}"
    )
