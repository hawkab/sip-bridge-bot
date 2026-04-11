import asyncio
import time

from domain.events import CdrGroupEvent, SMSReceivedEvent
from integrations.asterisk.cdr_monitor import CDRMonitor
from integrations.asterisk.recordings import resolve_recording_path
from integrations.event_store.client import CallStoreResult, EventStoreClient
from services.delivery_service import DeliveryHub
from services.formatters.cdr import format_cdr_group
from services.formatters.email_html import render_email_html
from services.formatters.sms import format_sms
from services.formatters.transcription import format_transcription


async def handle_cdr_group_notification(delivery: DeliveryHub, event_store: EventStoreClient, transcriber, transcription_pdf_renderer, rows: list[dict]) -> None:
    event = CdrGroupEvent(rows=rows)
    msg = format_cdr_group(event.rows)
    if not msg:
        return

    answered_record = next((record for record in event.rows if record.get("disposition") == "ANSWERED"), None)
    attachment_path = None
    attachment_name = None
    if answered_record:
        attachment_path, attachment_name = resolve_recording_path(answered_record.get("uniqueid"))

    transcription_text = await _transcribe_call_recording(transcriber, attachment_path)
    transcription_pdf_path, transcription_pdf_name = _build_transcription_pdf(
        transcription_pdf_renderer,
        attachment_path,
        transcription_text,
    )
    call_store_result = await _save_call_event(
        event_store,
        event.rows,
        attachment_path,
        attachment_name,
        transcription_text,
    )

    email_link_label = "Карточка звонка" if call_store_result.ok else "Карточка ошибки"
    email_text = _append_transcription(msg, transcription_text)
    email_text = _append_event_link(email_text, call_store_result.view_url, email_link_label)
    if not call_store_result.ok and call_store_result.error_message:
        email_text = f"{email_text}\n\nОшибка сохранения звонка: {call_store_result.error_message}"
    email_html = render_email_html(email_text)

    await delivery.notify_event(
        subject="SipBridgeBot: CDR событие",
        text=msg,
        attachment_path=attachment_path,
        attachment_name=attachment_name,
        parse_mode=None,
        email_text=email_text,
        email_html=email_html,
        email_attachment_path=None if call_store_result.ok else attachment_path,
        email_attachment_name=None if call_store_result.ok else attachment_name,
        telegram_followup_attachment_path=transcription_pdf_path,
        telegram_followup_attachment_name=transcription_pdf_name,
        telegram_followup_attachment_caption="Транскрибация звонка",
        telegram_followup_attachment_parse_mode=None,
    )


async def start_cdr_monitor(delivery: DeliveryHub, event_store: EventStoreClient, transcriber, transcription_pdf_renderer):
    async def cdr_group_callback(group: list):
        await handle_cdr_group_notification(delivery, event_store, transcriber, transcription_pdf_renderer, group)

    cdr_file = "/var/log/asterisk/cdr-csv/Master.csv"
    monitor = CDRMonitor(cdr_file, cdr_group_callback, check_interval=5.0, group_timeout=30.0)
    asyncio.create_task(monitor.start())


async def handle_sms_notification(delivery: DeliveryHub, event_store: EventStoreClient, sender: str, sim: str, when: str, text: str) -> None:
    event = SMSReceivedEvent(sender=sender, sim=sim, received_at=when, text=text)
    message_text = format_sms(event)
    view_url = await event_store.save_sms(timestamp=event.received_at, number=event.sender, text=event.text)
    email_text = _append_event_link(message_text, view_url, "Карточка SMS")
    await delivery.notify_event(
        subject=f"SipBridgeBot: SMS от {event.sender}",
        text=message_text,
        parse_mode="Markdown",
        email_text=email_text,
        email_html=render_email_html(email_text),
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
    transcription_text: str | None,
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
        transcription=transcription_text,
    )


async def _transcribe_call_recording(transcriber, attachment_path: str | None) -> str | None:
    if transcriber is None or not attachment_path:
        return None

    payload = await transcriber.transcribe_recording(attachment_path)
    if not payload:
        return None

    transcription_text = format_transcription(payload.get("conversation"))
    return transcription_text or None


def _build_transcription_pdf(transcription_pdf_renderer, attachment_path: str | None, transcription_text: str | None) -> tuple[str | None, str | None]:
    if transcription_pdf_renderer is None or not attachment_path or not transcription_text:
        return None, None
    return transcription_pdf_renderer.render_for_recording(attachment_path, transcription_text)


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


def _append_transcription(text: str, transcription_text: str | None) -> str:
    if not transcription_text:
        return text
    return f"{text}\n\nТранскрибация:\n{transcription_text}"


def _append_event_link(text: str, view_url: str | None, label: str) -> str:
    if not view_url:
        return text
    return f"{text}\n\n<a href='{view_url}'>{label}</a>"


def _safe_int(value) -> int:
    try:
        return int(str(value or "0").strip())
    except Exception:
        return 0
