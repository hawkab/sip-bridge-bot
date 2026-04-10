import asyncio
import time

from domain.events import CdrGroupEvent, SMSReceivedEvent
from integrations.asterisk.cdr_monitor import CDRMonitor
from integrations.asterisk.recordings import resolve_recording_path
from services.delivery_service import DeliveryHub
from services.formatters.cdr import format_cdr_group
from services.formatters.sms import format_sms


async def handle_cdr_group_notification(delivery: DeliveryHub, rows: list[dict]) -> None:
    event = CdrGroupEvent(rows=rows)
    msg = format_cdr_group(event.rows)
    if not msg:
        return
    answered_record = next((record for record in event.rows if record.get('disposition') == "ANSWERED"), None)
    attachment_path = None
    attachment_name = None
    if answered_record:
        attachment_path, attachment_name = resolve_recording_path(answered_record.get('uniqueid'))
    await delivery.notify_event(
        subject="SipBridgeBot: CDR событие",
        text=msg,
        attachment_path=attachment_path,
        attachment_name=attachment_name,
        parse_mode="Markdown",
    )


async def start_cdr_monitor(delivery: DeliveryHub):
    async def cdr_group_callback(group: list):
        await handle_cdr_group_notification(delivery, group)

    cdr_file = "/var/log/asterisk/cdr-csv/Master.csv"
    monitor = CDRMonitor(cdr_file, cdr_group_callback, check_interval=5.0, group_timeout=30.0)
    asyncio.create_task(monitor.start())


async def handle_sms_notification(delivery: DeliveryHub, sender: str, sim: str, when: str, text: str) -> None:
    event = SMSReceivedEvent(sender=sender, sim=sim, received_at=when, text=text)
    await delivery.notify_event(
        subject=f"SipBridgeBot: SMS от {event.sender}",
        text=format_sms(event),
        parse_mode="Markdown",
    )


async def send_startup_notification(delivery: DeliveryHub, app_version_text: str):
    text = (
        f"✅ Бот запущен ({time.strftime('%Y-%m-%d %H:%M:%S')})\n\n"
        f"Версия (Git):\n```\n{app_version_text}\n```"
    )
    await delivery.notify_event(
        subject="SipBridgeBot: запуск",
        text=text,
        parse_mode="Markdown",
    )
