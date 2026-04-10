#!/usr/bin/env python3
import asyncio
import logging
import os

from telegram.error import NetworkError
from telegram.ext import ApplicationBuilder, ContextTypes

from command_service import CommandService
from config import CONFIG
from delivery import DeliveryHub
from handlers import register_handlers, on_post_init, send_startup_notification, start_cdr_monitor, start_ys_reader
from mail_gateway import MailGateway
from tg_proxy import apply_runtime_proxy_env, choose_working_proxy, remove_proxy_from_file
from utils import get_app_version_text
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


async def run_telegram_transport(ys: YeastarSMSClient, delivery: DeliveryHub, command_service: CommandService) -> None:
    while True:
        selected_proxy = None
        app = None
        try:
            selected_proxy = await choose_working_proxy(CONFIG)
            apply_runtime_proxy_env(selected_proxy)
            logger.info("Selected Telegram proxy: %s", selected_proxy or "direct")

            app = build_application(selected_proxy)
            app.bot_data["ys"] = ys
            app.bot_data["delivery"] = delivery
            app.bot_data["command_service"] = command_service
            app.add_error_handler(error_handler)
            register_handlers(app)
            app.post_init = on_post_init

            await app.initialize()
            if app.post_init:
                await app.post_init(app)
            if app.updater:
                await app.updater.start_polling(allowed_updates=None)
            await app.start()

            logger.info("Telegram transport is running")
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise
        except NetworkError:
            logger.exception("Telegram transport failed")
            if selected_proxy:
                remove_proxy_from_file(CONFIG.TG_PROXY_FILE, selected_proxy)
        except Exception:
            logger.exception("Telegram transport failed")
            if selected_proxy:
                remove_proxy_from_file(CONFIG.TG_PROXY_FILE, selected_proxy)
        finally:
            delivery.set_telegram_app(None)
            apply_runtime_proxy_env(None)
            if app:
                try:
                    if app.updater:
                        await app.updater.stop()
                except Exception:
                    logger.exception("Failed to stop Telegram updater")
                try:
                    await app.stop()
                except Exception:
                    logger.exception("Failed to stop Telegram app")
                try:
                    await app.shutdown()
                except Exception:
                    logger.exception("Failed to shutdown Telegram app")

        logger.warning("Telegram transport will retry in 30 seconds")
        await asyncio.sleep(30)


async def async_main() -> None:
    ys = YeastarSMSClient(CONFIG.TG_HOST, CONFIG.TG_PORT, CONFIG.TG_USER, CONFIG.TG_PASS)
    delivery = DeliveryHub(CONFIG)
    command_service = CommandService(ys)

    await start_ys_reader(ys, delivery)
    await start_cdr_monitor(delivery)

    tasks = [
        asyncio.create_task(run_telegram_transport(ys, delivery, command_service), name="telegram-transport"),
    ]

    if delivery.is_imap_enabled():
        mail_gateway = MailGateway(CONFIG, delivery, command_service)
        tasks.append(asyncio.create_task(mail_gateway.run_forever(), name="mail-gateway"))
    else:
        logger.info("Email inbound gateway is disabled")

    await asyncio.sleep(1)
    await send_startup_notification(delivery, get_app_version_text())

    await asyncio.gather(*tasks)


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
