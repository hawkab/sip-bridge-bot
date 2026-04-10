import asyncio
import logging
import mimetypes
import os
import smtplib
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable

from auth import get_admin_chat_id
from telegram.error import NetworkError, RetryAfter, TimedOut

logger = logging.getLogger(__name__)

FAILED_TG_QUEUE = Path("/opt/sms/failed_telegram.queue")


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


async def send_tg_safe(
    app,
    chat_id: int,
    text: str,
    parse_mode: str | None = None,
    reply_markup=None,
) -> bool:
    delays = [0, 1, 2, 5, 10, 20]
    last_exc = None

    for d in delays:
        if d:
            await asyncio.sleep(d)
        try:
            await app.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
            return True
        except RetryAfter as e:
            last_exc = e
            retry = int(getattr(e, "retry_after", 5))
            await asyncio.sleep(max(1, retry))
        except (TimedOut, NetworkError) as e:
            last_exc = e

    try:
        FAILED_TG_QUEUE.parent.mkdir(parents=True, exist_ok=True)
        with FAILED_TG_QUEUE.open("a", encoding="utf-8") as f:
            f.write(f"\n--- {datetime.now().isoformat(timespec='seconds')} ---\n")
            f.write(f"chat_id={chat_id}\n")
            if last_exc:
                f.write(f"last_error={type(last_exc).__name__}: {last_exc}\n")
            f.write(text)
            f.write("\n")
    except Exception:
        logger.exception("Failed to append failed Telegram message to queue")
    return False


class DeliveryHub:
    def __init__(self, config):
        self.config = config
        self._telegram_app = None

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
        if not self._telegram_app:
            logger.warning("Telegram app is unavailable; cannot reply to chat_id=%s", chat_id)
            return
        for item in result.items:
            if item.kind == "text" and item.text is not None:
                await send_tg_safe(self._telegram_app, chat_id, item.text, parse_mode=item.parse_mode)
            elif item.kind == "file" and item.attachment_path:
                await self._send_tg_document(chat_id, item)

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
        app = self._telegram_app
        chat_id = get_admin_chat_id()
        if not app or not chat_id:
            return
        try:
            if attachment_path:
                item = ResponseItem(
                    kind="file",
                    attachment_path=attachment_path,
                    attachment_name=attachment_name,
                    caption=text,
                    parse_mode=parse_mode,
                )
                await self._send_tg_document(chat_id, item)
            else:
                await send_tg_safe(app, chat_id, text, parse_mode=parse_mode)
        except Exception:
            logger.exception("Failed to deliver event to Telegram")

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

    async def _send_tg_document(self, chat_id: int, item: ResponseItem) -> None:
        if not self._telegram_app or not item.attachment_path:
            return
        with open(item.attachment_path, "rb") as f:
            await self._telegram_app.bot.send_document(
                chat_id=chat_id,
                document=f,
                filename=item.attachment_name or os.path.basename(item.attachment_path),
                caption=item.caption,
                parse_mode=item.parse_mode,
            )

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
