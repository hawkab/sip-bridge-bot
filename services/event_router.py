import asyncio
import time

from domain.events import CdrGroupEvent, SMSReceivedEvent
from integrations.asterisk.cdr_monitor import CDRMonitor
from integrations.asterisk.recordings import resolve_recording_path
from integrations.event_store.client import CallStoreResult, EventStoreClient
from services.delivery_service import DeliveryHub
from services.formatters.cdr import format_cdr_group
from services.formatters.sms import format_sms


async def handle_cdr_group_notification(delivery: DeliveryHub, event_store: EventStoreClient, rows: list[dict]) -> None:
    event = CdrGroupEvent(rows=rows)
    msg = format_cdr_group(event.rows)
    if not msg:
        return
    answered_record = next((record for record in event.rows if record.get("disposition") == "ANSWERED"), None)
    attachment_path = None
    attachment_name = None
    if answered_record:
        attachment_path, attachment_name = resolve_recording_path(answered_record.get("uniqueid"))

    call_store_result = await _save_call_event(event_store, event.rows, attachment_path, attachment_name)
    email_link_label = "Карточка звонка" if call_store_result.ok else "Карточка ошибки"
    email_text = _append_event_link(msg, call_store_result.view_url, email_link_label)
    if not call_store_result.ok and call_store_result.error_message:
        email_text = f"{email_text}\n\nОшибка сохранения звонка: {call_store_result.error_message}"

    await delivery.notify_event(
        subject="SipBridgeBot: CDR событие",
        text=msg,
        attachment_path=attachment_path,
        attachment_name=attachment_name,
        parse_mode="Markdown",
        email_text=email_text,
        email_attachment_path=None if call_store_result.ok else attachment_path,
        email_attachment_name=None if call_store_result.ok else attachment_name,
    )


async def start_cdr_monitor(delivery: DeliveryHub, event_store: EventStoreClient):
    async def cdr_group_callback(group: list):
        await handle_cdr_group_notification(delivery, event_store, group)

    cdr_file = "/var/log/asterisk/cdr-csv/Master.csv"
    monitor = CDRMonitor(cdr_file, cdr_group_callback, check_interval=5.0, group_timeout=30.0)
    asyncio.create_task(monitor.start())


async def handle_sms_notification(delivery: DeliveryHub, event_store: EventStoreClient, sender: str, sim: str, when: str, text: str) -> None:
    event = SMSReceivedEvent(sender=sender, sim=sim, received_at=when, text=text)
    message_text = format_sms(event)
    view_url = await event_store.save_sms(timestamp=event.received_at, number=event.sender, text=event.text)
    await delivery.notify_event(
        subject=f"SipBridgeBot: SMS от {event.sender}",
        text=message_text,
        parse_mode="Markdown",
        email_text=_append_event_link(message_text, view_url, "Карточка SMS"),
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


async def _save_call_event(
    event_store: EventStoreClient,
    rows: list[dict],
    recording_path: str | None,
    recording_name: str | None,
) -> CallStoreResult:
    payload = _build_call_payload(rows)
    if not payload:
        return CallStoreResult(ok=False, error_message="call payload is incomplete")
    return await event_store.save_call(
        call_type=payload["type"],
        timestamp=payload["timestamp"],
        number=payload["number"],
        duration=payload["duration"],
        recording_path=recording_path,
        recording_name=recording_name,
    )


def _build_call_payload(rows: list[dict]) -> dict | None:
    if not rows:
        return None
    first = rows[0]
    answered_record = next((record for record in rows if record.get("disposition") == "ANSWERED"), None)
    primary = answered_record or first
    context = str(first.get("dcontext") or "")
    is_inbound = "inbound-gsm" in context

    number = str((primary.get("src") if is_inbound else primary.get("dst")) or "").strip()
    timestamp = str(primary.get("start") or first.get("start") or "").strip()
    if not number or not timestamp:
        return None

    duration = _safe_int(primary.get("billsec"))
    if duration <= 0:
        duration = _safe_int(primary.get("duration"))

    return {
        "type": "входящий" if is_inbound else "исходящий",
        "timestamp": timestamp,
        "number": number,
        "duration": duration,
    }


def _append_event_link(text: str, view_url: str | None, label: str) -> str:
    if not view_url:
        return text
    return f"{text}\n\n<a href='{view_url}'>{label}</a>"


def _safe_int(value) -> int:
    try:
        return int(str(value or "0").strip())
    except Exception:
        return 0
