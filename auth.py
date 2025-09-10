from typing import Optional
from telegram import Update
from telegram.ext import ContextTypes
from config import CONFIG

def set_admin_chat_id(chat_id: int):
    try:
        CONFIG.ADMIN_CHAT_FILE.write_text(str(chat_id))
    except Exception:
        pass

def get_admin_chat_id() -> Optional[int]:
    try:
        return int(CONFIG.ADMIN_CHAT_FILE.read_text().strip())
    except Exception:
        return None

def _is_admin_user(update: Update) -> bool:
    u = update.effective_user
    return bool(u and u.username and u.username.lower() == CONFIG.ADMIN_LOGIN.lower())

def only_admin(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin_user(update):
            return
        if update.effective_chat:
            set_admin_chat_id(update.effective_chat.id)
        return await func(update, context)
    return wrapper
