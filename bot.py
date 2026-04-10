#!/usr/bin/env python3
import asyncio
import logging
import os

from telegram.ext import ApplicationBuilder, ContextTypes

from config import CONFIG
from handlers import register_handlers, on_post_init
from tg_proxy import apply_runtime_proxy_env, choose_working_proxy
from ys_client import YeastarSMSClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _timeout_env(name: str, default: str) -> float:
    return float(os.environ.get(name, default))


def build_application(selected_proxy: str | None):
    builder = ApplicationBuilder().token(CONFIG.BOT_TOKEN)

    builder = builder.connect_timeout(_timeout_env("TG_CONNECT_TIMEOUT", "20"))
    builder = builder.read_timeout(_timeout_env("TG_READ_TIMEOUT", "60"))
    builder = builder.write_timeout(_timeout_env("TG_WRITE_TIMEOUT", "60"))
    builder = builder.pool_timeout(_timeout_env("TG_POOL_TIMEOUT", "60"))

    builder = builder.get_updates_connect_timeout(_timeout_env("TG_CONNECT_TIMEOUT", "20"))
    builder = builder.get_updates_read_timeout(_timeout_env("TG_READ_TIMEOUT", "60"))
    builder = builder.get_updates_write_timeout(_timeout_env("TG_WRITE_TIMEOUT", "60"))
    builder = builder.get_updates_pool_timeout(_timeout_env("TG_POOL_TIMEOUT", "60"))

    if selected_proxy:
        builder = builder.proxy(selected_proxy)
        builder = builder.get_updates_proxy(selected_proxy)

    return builder.build()


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.exception("Unhandled exception in bot", exc_info=context.error)


def main():
    ys = YeastarSMSClient(CONFIG.TG_HOST, CONFIG.TG_PORT, CONFIG.TG_USER, CONFIG.TG_PASS)

    selected_proxy = asyncio.run(choose_working_proxy(CONFIG))
    asyncio.set_event_loop(asyncio.new_event_loop())

    apply_runtime_proxy_env(selected_proxy)
    logger.info("Selected Telegram proxy: %s", selected_proxy or "direct")

    app = build_application(selected_proxy)
    app.bot_data["ys"] = ys
    app.add_error_handler(error_handler)

    register_handlers(app)
    app.post_init = on_post_init

    app.run_polling(allowed_updates=None, stop_signals=None)


if __name__ == "__main__":
    main()
