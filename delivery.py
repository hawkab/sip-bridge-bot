import asyncio
import json
import logging
import mimetypes
import os
import re
import smtplib
from dataclasses import asdict, dataclass
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable

from auth import get_admin_chat_id
from telegram.error import NetworkError, RetryAfter, TimedOut

logger = logging.getLogger(__name__)

FAILED_TG_QUEUE = Path("/opt/sms/failed_telegram.queue")
TELEGRAM_RETRY_DELAYS = [0, 1, 2, 5, 10, 20]
LEGACY_QUEUE_BLOCK_RE = re.compile(
    r"(?ms)^--- (?P<created_at>.+?) ---\n"
    r"chat_id=(?P<chat_id>\d+)\n"
    r"(?:last_error=.*\n)?"
    r"(?P<text>.*?)(?=^--- .+? ---\n|\Z)"
)


@dataclass
class ResponseItem:
    kind: str  # text | file
    text: str | None = None
    parse_mode: str | None = None
    attachment_path: str | None = None
    attachment_name: str | None = None
    caption: str | None = None


@dataclass
class CommandResult:
    items: list[ResponseItem]
    post_action: str | None = None


@dataclass
class QueuedTelegramMessage:
    chat_id: int
    item: ResponseItem
    created_at: str
    last_error: str | None = None


async def send_tg_safe(
    app,
    chat_id: int,
    text: str,
    parse_mode: str | None = None,
    reply_markup=None,
) -> bool:
    delivered, _ = await _send_tg_text_direct(
        app=app,
        chat_id=chat_id,
        text=text,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
    )
    return delivered


async def _send_tg_text_direct(
    app,
    chat_id: int,
    text: str,
    parse_mode: str | None = None,
    reply_markup=None,
) -> tuple[bool, str | None]:
    last_exc = None

    for delay in TELEGRAM_RETRY_DELAYS:
        if delay:
            await asyncio.sleep(delay)
        try:
            await app.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
            return True, None
        except RetryAfter as exc:
            last_exc = exc
            retry_after = int(getattr(exc, "retry_after", 5))
            await asyncio.sleep(max(1, retry_after))
        except (TimedOut, NetworkError) as exc:
            last_exc = exc
        except Exception as exc:
            logger.exception("Telegram text send failed permanently for chat_id=%s", chat_id)
            return False, f"{type(exc).__name__}: {exc}"

    error_text = None
    if last_exc is not None:
        error_text = f"{type(last_exc).__name__}: {last_exc}"
    return False, error_text


async def _send_tg_document_direct(app, chat_id: int, item: ResponseItem) -> tuple[bool, str | None]:
    if not item.attachment_path:
        logger.error("Telegram document send skipped: attachment_path is empty")
        return False, "attachment_path is empty"
    if not os.path.exists(item.attachment_path):
        logger.error("Telegram document send skipped: file not found: %s", item.attachment_path)
        return False, f"file not found: {item.attachment_path}"

    last_exc = None
    for delay in TELEGRAM_RETRY_DELAYS:
        if delay:
            await asyncio.sleep(delay)
        try:
            with open(item.attachment_path, "rb") as f:
                await app.bot.send_document(
                    chat_id=chat_id,
                    document=f,
                    filename=item.attachment_name or os.path.basename(item.attachment_path),
                    caption=item.caption,
                    parse_mode=item.parse_mode,
                )
            return True, None
        except RetryAfter as exc:
            last_exc = exc
            retry_after = int(getattr(exc, "retry_after", 5))
            await asyncio.sleep(max(1, retry_after))
        except (TimedOut, NetworkError) as exc:
            last_exc = exc
        except Exception as exc:
            logger.exception("Telegram document send failed permanently for chat_id=%s", chat_id)
            return False, f"{type(exc).__name__}: {exc}"

    error_text = None
    if last_exc is not None:
        error_text = f"{type(last_exc).__name__}: {last_exc}"
    return False, error_text


class DeliveryHub:
    def __init__(self, config):
        self.config = config
        self._telegram_app = None
        self._telegram_send_lock = asyncio.Lock()

    def set_telegram_app(self, app) -> None:
        self._telegram_app = app

    def is_email_enabled(self) -> bool:
        return bool(self.config.EMAIL_ENABLED and self.config.EMAIL_SMTP_HOST and self.config.EMAIL_TO_LIST)

    def is_imap_enabled(self) -> bool:
        return bool(
            self.config.EMAIL_ENABLED
            and self.config.EMAIL_IMAP_HOST
            and self.config.EMAIL_IMAP_USER
            and self.config.EMAIL_ALLOWED_SENDERS_SET
            and self.config.EMAIL_COMMAND_HASH
        )

    async def flush_pending_telegram_messages(self) -> None:
        if not self._telegram_app:
            return
        async with self._telegram_send_lock:
            await self._flush_pending_telegram_messages_locked()

    async def notify_event(
        self,
        subject: str,
        text: str,
        attachment_path: str | None = None,
        attachment_name: str | None = None,
        parse_mode: str | None = None,
    ) -> None:
        await asyncio.gather(
            self._notify_telegram(text, attachment_path, attachment_name, parse_mode=parse_mode),
            self._notify_email(subject, text, attachment_path, attachment_name),
            return_exceptions=True,
        )

    async def reply_telegram(self, chat_id: int, result: CommandResult) -> None:
        for item in result.items:
            await self._deliver_telegram_item(chat_id, item)

    async def reply_email(self, recipient: str, subject: str, result: CommandResult) -> None:
        if not self.is_email_enabled():
            logger.warning("Email is disabled; cannot reply to %s", recipient)
            return
        body_parts = []
        attachments = []
        for item in result.items:
            if item.kind == "text" and item.text:
                body_parts.append(item.text)
            elif item.kind == "file" and item.attachment_path:
                attachments.append((item.attachment_path, item.attachment_name or os.path.basename(item.attachment_path)))
                if item.caption:
                    body_parts.append(item.caption)
        body = "\n\n".join(part for part in body_parts if part).strip() or "Готово."
        await self._send_email([recipient], subject, body, attachments)

    async def _notify_telegram(
        self,
        text: str,
        attachment_path: str | None,
        attachment_name: str | None,
        parse_mode: str | None = None,
    ) -> None:
        chat_id = get_admin_chat_id()
        if not chat_id:
            return

        item = ResponseItem(
            kind="file" if attachment_path else "text",
            text=None if attachment_path else text,
            parse_mode=parse_mode,
            attachment_path=attachment_path,
            attachment_name=attachment_name,
            caption=text if attachment_path else None,
        )
        await self._deliver_telegram_item(chat_id, item)

    async def _deliver_telegram_item(self, chat_id: int, item: ResponseItem) -> None:
        app = self._telegram_app
        if not app:
            self._append_failed_telegram_message(chat_id, item, "telegram transport is unavailable")
            return

        try:
            async with self._telegram_send_lock:
                await self._flush_pending_telegram_messages_locked()
                delivered, last_error = await self._send_telegram_item_direct(app, chat_id, item)
                if delivered:
                    return
                if self._is_retryable_telegram_error(last_error):
                    self._append_failed_telegram_message(chat_id, item, last_error)
                    return
                logger.error("Telegram item is non-retryable for chat_id=%s: %s", chat_id, last_error)
        except Exception:
            logger.exception("Failed to deliver Telegram item to chat_id=%s", chat_id)

    async def _flush_pending_telegram_messages_locked(self) -> None:
        app = self._telegram_app
        if not app:
            return

        queue = self._load_failed_telegram_queue()
        if not queue:
            return

        logger.info("Telegram queue flush started: %s item(s)", len(queue))
        remaining = list(queue)
        while remaining:
            queued = remaining[0]
            delivered, retryable_failure = await self._send_queued_message(app, queued)
            if delivered:
                remaining.pop(0)
                self._store_failed_telegram_queue(remaining)
                continue
            if retryable_failure:
                logger.warning(
                    "Telegram queue flush stopped on retryable failure for chat_id=%s",
                    queued.chat_id,
                )
                break
            logger.error(
                "Telegram queue item dropped as non-retryable for chat_id=%s created_at=%s",
                queued.chat_id,
                queued.created_at,
            )
            remaining.pop(0)
            self._store_failed_telegram_queue(remaining)

    async def _send_queued_message(self, app, queued: QueuedTelegramMessage) -> tuple[bool, bool]:
        delivered, last_error = await self._send_telegram_item_direct(app, queued.chat_id, queued.item)
        if delivered:
            return True, False

        if self._is_retryable_telegram_error(last_error):
            return False, True

        logger.error(
            "Telegram queue item is non-retryable for chat_id=%s: %s",
            queued.chat_id,
            last_error,
        )
        return False, False

    async def _send_telegram_item_direct(self, app, chat_id: int, item: ResponseItem) -> tuple[bool, str | None]:
        if item.kind == "file":
            return await _send_tg_document_direct(app, chat_id, item)
        return await _send_tg_text_direct(
            app=app,
            chat_id=chat_id,
            text=item.text or "",
            parse_mode=item.parse_mode,
        )

    def _append_failed_telegram_message(self, chat_id: int, item: ResponseItem, last_error: str | None) -> None:
        try:
            queue = self._load_failed_telegram_queue()
            queue.append(
                QueuedTelegramMessage(
                    chat_id=chat_id,
                    item=item,
                    created_at=datetime.now().isoformat(timespec="seconds"),
                    last_error=last_error,
                )
            )
            self._store_failed_telegram_queue(queue)
        except Exception:
            logger.exception("Failed to append failed Telegram message to queue")

    def _load_failed_telegram_queue(self) -> list[QueuedTelegramMessage]:
        if not FAILED_TG_QUEUE.exists():
            return []

        raw = FAILED_TG_QUEUE.read_text(encoding="utf-8").strip()
        if not raw:
            return []

        if raw.startswith("{"):
            return self._parse_json_queue(raw)
        return self._parse_legacy_queue(raw)

    def _parse_json_queue(self, raw: str) -> list[QueuedTelegramMessage]:
        queue = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                queue.append(
                    QueuedTelegramMessage(
                        chat_id=int(payload["chat_id"]),
                        item=ResponseItem(**payload["item"]),
                        created_at=payload.get("created_at") or datetime.now().isoformat(timespec="seconds"),
                        last_error=payload.get("last_error"),
                    )
                )
            except Exception:
                logger.exception("Failed to parse Telegram queue line: %s", line)
        return queue

    def _parse_legacy_queue(self, raw: str) -> list[QueuedTelegramMessage]:
        queue = []
        for match in LEGACY_QUEUE_BLOCK_RE.finditer(raw + "\n"):
            text = match.group("text").rstrip("\n")
            if not text:
                continue
            queue.append(
                QueuedTelegramMessage(
                    chat_id=int(match.group("chat_id")),
                    item=ResponseItem(kind="text", text=text),
                    created_at=match.group("created_at"),
                    last_error=None,
                )
            )
        return queue

    def _store_failed_telegram_queue(self, queue: list[QueuedTelegramMessage]) -> None:
        if not queue:
            if FAILED_TG_QUEUE.exists():
                FAILED_TG_QUEUE.unlink()
            return

        FAILED_TG_QUEUE.parent.mkdir(parents=True, exist_ok=True)
        with FAILED_TG_QUEUE.open("w", encoding="utf-8") as f:
            for queued in queue:
                payload = {
                    "chat_id": queued.chat_id,
                    "item": asdict(queued.item),
                    "created_at": queued.created_at,
                    "last_error": queued.last_error,
                }
                f.write(json.dumps(payload, ensure_ascii=False))
                f.write("\n")

    def _is_retryable_telegram_error(self, last_error: str | None) -> bool:
        if not last_error:
            return False
        return last_error.startswith("RetryAfter:") or last_error.startswith("TimedOut:") or last_error.startswith("NetworkError:")

    async def _notify_email(
        self,
        subject: str,
        text: str,
        attachment_path: str | None,
        attachment_name: str | None,
    ) -> None:
        if not self.is_email_enabled():
            return
        attachments = []
        if attachment_path:
            attachments.append((attachment_path, attachment_name or os.path.basename(attachment_path)))
        await self._send_email(self.config.EMAIL_TO_LIST, subject, text, attachments)

    async def _send_email(
        self,
        recipients: Iterable[str],
        subject: str,
        body: str,
        attachments: list[tuple[str, str]],
    ) -> None:
        if not self.is_email_enabled():
            return
        recipient_list = [x for x in recipients if x]
        if not recipient_list:
            return
        await asyncio.to_thread(self._send_email_blocking, recipient_list, subject, body, attachments)

    def _send_email_blocking(
        self,
        recipients: list[str],
        subject: str,
        body: str,
        attachments: list[tuple[str, str]],
    ) -> None:
        msg = EmailMessage()
        msg["From"] = self.config.EMAIL_FROM
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = subject
        msg.set_content(body)

        for attachment_path, attachment_name in attachments:
            try:
                with open(attachment_path, "rb") as f:
                    payload = f.read()
                mime_type, _ = mimetypes.guess_type(attachment_name)
                maintype, subtype = (mime_type.split("/", 1) if mime_type else ("application", "octet-stream"))
                msg.add_attachment(payload, maintype=maintype, subtype=subtype, filename=attachment_name)
            except Exception:
                logger.exception("Failed to attach %s to email", attachment_path)

        if self.config.EMAIL_SMTP_SSL:
            with smtplib.SMTP_SSL(self.config.EMAIL_SMTP_HOST, self.config.EMAIL_SMTP_PORT, timeout=30) as smtp:
                self._smtp_login_if_needed(smtp)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(self.config.EMAIL_SMTP_HOST, self.config.EMAIL_SMTP_PORT, timeout=30) as smtp:
                smtp.ehlo()
                if self.config.EMAIL_SMTP_STARTTLS:
                    smtp.starttls()
                    smtp.ehlo()
                self._smtp_login_if_needed(smtp)
                smtp.send_message(msg)

    def _smtp_login_if_needed(self, smtp) -> None:
        if self.config.EMAIL_SMTP_USER:
            smtp.login(self.config.EMAIL_SMTP_USER, self.config.EMAIL_SMTP_PASS)
