import asyncio
import logging
import mimetypes
import os
import smtplib
from email.message import EmailMessage
from typing import Iterable

logger = logging.getLogger(__name__)


class EmailSender:
    def __init__(self, config):
        self.config = config

    async def send(self, recipients: Iterable[str], subject: str, body: str, attachments: list[tuple[str, str]]) -> None:
        recipient_list = [x for x in recipients if x]
        if not recipient_list:
            return
        await asyncio.to_thread(self._send_blocking, recipient_list, subject, body, attachments)

    def _send_blocking(self, recipients: list[str], subject: str, body: str, attachments: list[tuple[str, str]]) -> None:
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
                self._login_if_needed(smtp)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(self.config.EMAIL_SMTP_HOST, self.config.EMAIL_SMTP_PORT, timeout=30) as smtp:
                smtp.ehlo()
                if self.config.EMAIL_SMTP_STARTTLS:
                    smtp.starttls()
                    smtp.ehlo()
                self._login_if_needed(smtp)
                smtp.send_message(msg)

    def _login_if_needed(self, smtp) -> None:
        if self.config.EMAIL_SMTP_USER:
            smtp.login(self.config.EMAIL_SMTP_USER, self.config.EMAIL_SMTP_PASS)
