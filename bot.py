#!/usr/bin/env python3
from telegram.ext import ApplicationBuilder
from config import CONFIG
from ys_client import YeastarSMSClient
from handlers import register_handlers, on_post_init


def main():
    ys = YeastarSMSClient(CONFIG.TG_HOST, CONFIG.TG_PORT, CONFIG.TG_USER, CONFIG.TG_PASS)

    app = ApplicationBuilder().token(CONFIG.BOT_TOKEN).build()
    app.bot_data["ys"] = ys

    register_handlers(app)
    app.post_init = on_post_init

    app.run_polling(allowed_updates=None, stop_signals=None)

if __name__ == "__main__":
    main()
