import asyncio
import logging
import os
from collections.abc import Iterable

from domain.models import CommandResult, ResponseItem
from integrations.email.smtp_sender import EmailSender
from integrations.telegram.auth import get_admin_chat_id
from integrations.telegram.queue_store import append_failed_message, load_failed_queue, store_failed_queue
from integrations.telegram.sender import send_tg_item_direct
from services.retry_policy import is_retryable_telegram_error

logger = logging.getLogger(__name__)


class DeliveryHub:
    def __init__(self, config):
        self.config = config
        self._telegram_app = None
        self._telegram_send_lock = asyncio.Lock()
        self._email_sender = EmailSender(config)

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

    async def notify_event(self, subject: str, text: str, attachment_path: str | None = None, attachment_name: str | None = None, parse_mode: str | None = None) -> None:
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

    async def _notify_telegram(self, text: str, attachment_path: str | None, attachment_name: str | None, parse_mode: str | None = None) -> None:
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
            append_failed_message(chat_id, item, "telegram transport is unavailable")
            return
        try:
            async with self._telegram_send_lock:
                await self._flush_pending_telegram_messages_locked()
                delivered, last_error = await send_tg_item_direct(app, chat_id, item)
                if delivered:
                    return
                if self._is_retryable_telegram_error(last_error):
                    append_failed_message(chat_id, item, last_error)
                    return
                logger.error("Telegram item is non-retryable for chat_id=%s: %s", chat_id, last_error)
        except Exception:
            logger.exception("Failed to deliver Telegram item to chat_id=%s", chat_id)

    async def _flush_pending_telegram_messages_locked(self) -> None:
        app = self._telegram_app
        if not app:
            return
        remaining = list(load_failed_queue())
        if not remaining:
            return
        logger.info("Telegram queue flush started: %s item(s)", len(remaining))
        while remaining:
            queued = remaining[0]
            delivered, retryable_failure = await self._send_queued_message(app, queued)
            if delivered:
                remaining.pop(0)
                store_failed_queue(remaining)
                continue
            if retryable_failure:
                logger.warning("Telegram queue flush stopped on retryable failure for chat_id=%s", queued.chat_id)
                break
            logger.error(
                "Telegram queue item dropped as non-retryable for chat_id=%s created_at=%s",
                queued.chat_id,
                queued.created_at,
            )
            remaining.pop(0)
            store_failed_queue(remaining)

    async def _send_queued_message(self, app, queued) -> tuple[bool, bool]:
        delivered, last_error = await send_tg_item_direct(app, queued.chat_id, queued.item)
        if delivered:
            return True, False
        if self._is_retryable_telegram_error(last_error):
            return False, True
        logger.error("Telegram queue item is non-retryable for chat_id=%s: %s", queued.chat_id, last_error)
        return False, False

    def _is_retryable_telegram_error(self, last_error: str | None) -> bool:
        return bool(last_error) and is_retryable_telegram_error(last_error)

    async def _notify_email(self, subject: str, text: str, attachment_path: str | None, attachment_name: str | None) -> None:
        if not self.is_email_enabled():
            return
        attachments = []
        if attachment_path:
            attachments.append((attachment_path, attachment_name or os.path.basename(attachment_path)))
        await self._send_email(self.config.EMAIL_TO_LIST, subject, text, attachments)

    async def _send_email(self, recipients: Iterable[str], subject: str, body: str, attachments: list[tuple[str, str]]) -> None:
        if not self.is_email_enabled():
            return
        recipient_list = [x for x in recipients if x]
        if not recipient_list:
            return
        await self._email_sender.send(recipient_list, subject, body, attachments)
