import asyncio
import email
import imaplib
import logging
import re
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr

from services.command_service import execute_post_action

logger = logging.getLogger(__name__)


@dataclass
class InboundMailCommand:
    sender: str
    subject: str
    command: str
    uid: bytes


class MailGateway:
    def __init__(self, config, delivery, command_service):
        self.config = config
        self.delivery = delivery
        self.command_service = command_service

    async def run_forever(self) -> None:
        if not self.delivery.is_imap_enabled():
            logger.info("Email IMAP gateway is disabled")
            return
        logger.info("Email IMAP gateway started for mailbox %s", self.config.EMAIL_IMAP_MAILBOX)
        while True:
            try:
                commands = await asyncio.to_thread(self._poll_once)
                for item in commands:
                    await self._handle_command(item)
            except Exception:
                logger.exception("Email gateway polling failed")
            await asyncio.sleep(self.config.EMAIL_POLL_INTERVAL)

    async def _handle_command(self, item: InboundMailCommand) -> None:
        logger.info("Executing email command from %s: %s", item.sender, item.command)
        result = await self.command_service.execute(item.command)
        subject = f"SipBridgeBot: {item.command}"
        await self.delivery.reply_email(item.sender, subject, result)
        execute_post_action(result.post_action)

    def _poll_once(self) -> list[InboundMailCommand]:
        commands: list[InboundMailCommand] = []
        client = imaplib.IMAP4_SSL(self.config.EMAIL_IMAP_HOST, self.config.EMAIL_IMAP_PORT)
        try:
            client.login(self.config.EMAIL_IMAP_USER, self.config.EMAIL_IMAP_PASS)
            client.select(self.config.EMAIL_IMAP_MAILBOX)
            typ, data = client.uid("search", None, "UNSEEN")
            if typ != "OK":
                return []
            for uid in data[0].split():
                parsed = self._fetch_command(client, uid)
                if parsed:
                    commands.append(parsed)
        finally:
            try:
                client.logout()
            except Exception:
                pass
        return commands

    def _fetch_command(self, client, uid: bytes) -> InboundMailCommand | None:
        typ, data = client.uid("fetch", uid, "(RFC822)")
        if typ != "OK" or not data:
            return None
        raw_msg = None
        for part in data:
            if isinstance(part, tuple):
                raw_msg = part[1]
                break
        if not raw_msg:
            return None
        message = BytesParser(policy=policy.default).parsebytes(raw_msg)
        sender = parseaddr(message.get("From", ""))[1].lower().strip()
        if not sender or sender not in self.config.EMAIL_ALLOWED_SENDERS_SET:
            return None

        subject = message.get("Subject", "") or ""
        body = self._extract_body_text(message)
        haystack = f"{subject}\n{body}"
        command_hash = self.config.EMAIL_COMMAND_HASH.strip()
        if command_hash and command_hash not in haystack:
            return None

        command = self._extract_command(subject, body)
        if not command:
            return None
        return InboundMailCommand(sender=sender, subject=subject, command=command, uid=uid)

    def _extract_body_text(self, message: email.message.EmailMessage) -> str:
        if message.is_multipart():
            parts = []
            for part in message.walk():
                if part.get_content_maintype() == "multipart":
                    continue
                if part.get_content_disposition() == "attachment":
                    continue
                if part.get_content_type() == "text/plain":
                    try:
                        parts.append(part.get_content())
                    except Exception:
                        continue
            return "\n".join(parts)
        try:
            return message.get_content()
        except Exception:
            return ""

    def _extract_command(self, subject: str, body: str) -> str | None:
        patterns = [subject or "", body or ""]
        for text in patterns:
            for line in text.splitlines():
                m = re.search(r"(/\w+(?:\s+[^\r\n]+)?)", line.strip())
                if m:
                    return m.group(1).strip()
        return None
