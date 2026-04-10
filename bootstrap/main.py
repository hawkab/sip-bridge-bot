#!/usr/bin/env python3
import asyncio
import logging

from bootstrap.config import CONFIG
from bootstrap.wiring import configure_logging
from integrations.email.imap_reader import MailGateway
from integrations.event_store.client import EventStoreClient
from integrations.telegram.adapter import run_telegram_transport
from integrations.tg200.adapter import start_reader as start_ys_reader
from integrations.tg200.client import YeastarSMSClient
from services.command_service import CommandService
from services.delivery_service import DeliveryHub
from services.event_router import send_startup_notification, start_cdr_monitor
from services.system_ops import get_app_version_text

configure_logging()
logger = logging.getLogger(__name__)


async def async_main() -> None:
    ys = YeastarSMSClient(CONFIG.TG_HOST, CONFIG.TG_PORT, CONFIG.TG_USER, CONFIG.TG_PASS)
    delivery = DeliveryHub(CONFIG)
    event_store = EventStoreClient(CONFIG)
    command_service = CommandService(ys)

    await start_ys_reader(ys, delivery, event_store)
    await start_cdr_monitor(delivery, event_store)

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
