import asyncio
import logging

from telegram.error import NetworkError

from bootstrap.wiring import build_application, error_handler
from bootstrap.config import CONFIG
from integrations.telegram.handlers import on_post_init, register_handlers
from integrations.telegram.proxy import apply_runtime_proxy_env, choose_working_proxy, remove_proxy_from_file

logger = logging.getLogger(__name__)


async def run_telegram_transport(ys, delivery, command_service) -> None:
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
