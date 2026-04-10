import logging
import os

from bootstrap.config import CONFIG
from telegram.ext import ApplicationBuilder, ContextTypes


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def timeout_env(name: str, default: str) -> float:
    return float(os.environ.get(name, default))


def build_application(selected_proxy: str | None):
    builder = ApplicationBuilder().token(CONFIG.BOT_TOKEN)

    builder = builder.connect_timeout(timeout_env("TG_CONNECT_TIMEOUT", "20"))
    builder = builder.read_timeout(timeout_env("TG_READ_TIMEOUT", "60"))
    builder = builder.write_timeout(timeout_env("TG_WRITE_TIMEOUT", "60"))
    builder = builder.pool_timeout(timeout_env("TG_POOL_TIMEOUT", "60"))

    builder = builder.get_updates_connect_timeout(timeout_env("TG_CONNECT_TIMEOUT", "20"))
    builder = builder.get_updates_read_timeout(timeout_env("TG_READ_TIMEOUT", "60"))
    builder = builder.get_updates_write_timeout(timeout_env("TG_WRITE_TIMEOUT", "60"))
    builder = builder.get_updates_pool_timeout(timeout_env("TG_POOL_TIMEOUT", "60"))

    if selected_proxy:
        builder = builder.proxy(selected_proxy)
        builder = builder.get_updates_proxy(selected_proxy)

    return builder.build()


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.exception("Unhandled exception in bot", exc_info=context.error)
