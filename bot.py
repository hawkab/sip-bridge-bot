#!/usr/bin/env python3
import logging
from telegram.request import HTTPXRequest
from telegram.ext import ApplicationBuilder, ContextTypes
from config import CONFIG
from ys_client import YeastarSMSClient
from handlers import register_handlers, on_post_init

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

def build_tg_request() -> HTTPXRequest:
    # Значения можно не выносить в env, но удобно:
    # TG_CONNECT_TIMEOUT, TG_READ_TIMEOUT, TG_WRITE_TIMEOUT, TG_POOL_TIMEOUT
    import os
    return HTTPXRequest(
        connect_timeout=float(os.environ.get("TG_CONNECT_TIMEOUT", "20")),
        read_timeout=float(os.environ.get("TG_READ_TIMEOUT", "60")),
        write_timeout=float(os.environ.get("TG_WRITE_TIMEOUT", "60")),
        pool_timeout=float(os.environ.get("TG_POOL_TIMEOUT", "60")),
    )

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Главное: больше не будет “No error handlers…”
    logging.exception("Unhandled exception in bot", exc_info=context.error)

def main():
    ys = YeastarSMSClient(CONFIG.TG_HOST, CONFIG.TG_PORT, CONFIG.TG_USER, CONFIG.TG_PASS)

    app = ApplicationBuilder().token(CONFIG.BOT_TOKEN).request(build_tg_request()).build()
    app.bot_data["ys"] = ys
    app.add_error_handler(error_handler)
    
    register_handlers(app)
    app.post_init = on_post_init

    app.run_polling(allowed_updates=None, stop_signals=None)

if __name__ == "__main__":
    main()
