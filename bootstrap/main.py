#!/usr/bin/env python3
import asyncio
import logging

from bootstrap.config import CONFIG
from bootstrap.wiring import configure_logging
from integrations.email.imap_reader import MailGateway
from integrations.event_store.client import EventStoreClient
from integrations.telegram.adapter import run_telegram_transport
from integrations.transcription.lazy_pdf import LazyTranscriptionPdfRenderer
from integrations.http import create_http_client
from integrations.transcription.stereo import StereoCallTranscriber
from integrations.tg200.adapter import start_reader as start_ys_reader
from integrations.tg200.client import YeastarSMSClient
from services.command_service import CommandService
from integrations.event_store.sms_outbox import SmsOutboxClient
from workers.sms_outbox import SmsOutboxWorker
from integrations.event_store.voice_outbox import VoiceOutboxClient
from integrations.asterisk.voice_calls import AsteriskVoiceCalls
from workers.voice_outbox import VoiceOutboxWorker
from workers.voice_recordings import VoiceRecordingWorker
from services.delivery_service import DeliveryHub
from services.event_router import send_startup_notification, start_cdr_monitor
from services.system_ops import get_app_version_text

configure_logging()
logger = logging.getLogger(__name__)


async def async_main() -> None:
    async with create_http_client() as http:
        await run_bot(http)


async def run_bot(http) -> None:
    ys = YeastarSMSClient(CONFIG.TG_HOST, CONFIG.TG_PORT, CONFIG.TG_USER, CONFIG.TG_PASS, span_offset=CONFIG.SMS_SPAN_OFFSET)
    delivery = DeliveryHub(CONFIG)
    event_store = EventStoreClient(CONFIG, http)
    sms_outbox = SmsOutboxClient(CONFIG, http)
    voice_outbox = VoiceOutboxClient(CONFIG, http)
    command_service = CommandService(ys, sms_outbox, voice_outbox)
    transcriber = StereoCallTranscriber(CONFIG, http)
    transcription_pdf_renderer = LazyTranscriptionPdfRenderer(CONFIG)

    tasks = []
    try:
        tasks.append(await start_ys_reader(ys, delivery, event_store))
        tasks.append(await start_cdr_monitor(delivery, event_store, transcriber, transcription_pdf_renderer))
        tasks.append(asyncio.create_task(run_telegram_transport(ys, delivery, command_service), name="telegram-transport"))

        if sms_outbox.enabled:
            tasks.append(asyncio.create_task(SmsOutboxWorker(sms_outbox, ys, CONFIG, delivery).run_forever(), name="sms-outbox"))
        if voice_outbox.enabled:
            voice_calls = AsteriskVoiceCalls(CONFIG)
            tasks.append(asyncio.create_task(VoiceOutboxWorker(voice_outbox, voice_calls, delivery).run_forever(), name="voice-outbox"))
            tasks.append(asyncio.create_task(VoiceRecordingWorker(voice_calls, event_store, transcriber,
                transcription_pdf_renderer, delivery).run_forever(), name="voice-recordings"))
        if delivery.is_imap_enabled():
            mail_gateway = MailGateway(CONFIG, delivery, command_service)
            tasks.append(asyncio.create_task(mail_gateway.run_forever(), name="mail-gateway"))
        else:
            logger.info("Email inbound gateway is disabled")

        await asyncio.sleep(1)
        await send_startup_notification(delivery, get_app_version_text())
        await asyncio.gather(*tasks)
    finally:
        # Stop API users before the shared HTTP pool is closed by async_main.
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await transcriber.aclose()


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
