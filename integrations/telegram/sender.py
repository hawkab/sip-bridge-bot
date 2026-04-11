import asyncio
import logging
import os

from domain.models import ResponseItem
from services.retry_policy import TELEGRAM_RETRY_DELAYS
from telegram.error import NetworkError, RetryAfter, TimedOut

logger = logging.getLogger(__name__)

_TELEGRAM_TEXT_LIMIT = 4000
_TELEGRAM_CAPTION_LIMIT = 1000


async def send_tg_safe(app, chat_id: int, text: str, parse_mode: str | None = None, reply_markup=None) -> bool:
    delivered, _ = await send_tg_text_direct(
        app=app,
        chat_id=chat_id,
        text=text,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
    )
    return delivered


async def send_tg_item_direct(app, chat_id: int, item: ResponseItem) -> tuple[bool, str | None]:
    if item.kind == "file":
        return await send_tg_document_direct(app, chat_id, item)
    return await send_tg_text_direct(
        app=app,
        chat_id=chat_id,
        text=item.text or "",
        parse_mode=item.parse_mode,
    )


async def send_tg_text_direct(app, chat_id: int, text: str, parse_mode: str | None = None, reply_markup=None) -> tuple[bool, str | None]:
    chunks = split_telegram_text(text or "", _TELEGRAM_TEXT_LIMIT)
    if not chunks:
        chunks = [""]

    for index, chunk in enumerate(chunks):
        chunk_reply_markup = reply_markup if index == len(chunks) - 1 else None
        delivered, error_text = await _send_single_text_message(
            app=app,
            chat_id=chat_id,
            text=chunk,
            parse_mode=parse_mode,
            reply_markup=chunk_reply_markup,
        )
        if not delivered:
            return False, error_text
    return True, None


async def send_tg_document_direct(app, chat_id: int, item: ResponseItem) -> tuple[bool, str | None]:
    if not item.attachment_path:
        logger.error("Telegram document send skipped: attachment_path is empty")
        return False, "attachment_path is empty"
    if not os.path.exists(item.attachment_path):
        logger.error("Telegram document send skipped: file not found: %s", item.attachment_path)
        return False, f"file not found: {item.attachment_path}"

    caption = item.caption or ""
    extra_text = None
    if len(caption) > _TELEGRAM_CAPTION_LIMIT:
        extra_text = caption
        caption = "WAV запись звонка"

    delivered, error_text = await _send_single_document(
        app=app,
        chat_id=chat_id,
        attachment_path=item.attachment_path,
        attachment_name=item.attachment_name,
        caption=caption or None,
        parse_mode=item.parse_mode if not extra_text else None,
    )
    if not delivered:
        return False, error_text

    if extra_text:
        return await send_tg_text_direct(app, chat_id, extra_text, parse_mode=item.parse_mode)
    return True, None


async def _send_single_text_message(app, chat_id: int, text: str, parse_mode: str | None = None, reply_markup=None) -> tuple[bool, str | None]:
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


async def _send_single_document(app, chat_id: int, attachment_path: str, attachment_name: str | None, caption: str | None, parse_mode: str | None) -> tuple[bool, str | None]:
    last_exc = None
    for delay in TELEGRAM_RETRY_DELAYS:
        if delay:
            await asyncio.sleep(delay)
        try:
            with open(attachment_path, "rb") as f:
                await app.bot.send_document(
                    chat_id=chat_id,
                    document=f,
                    filename=attachment_name or os.path.basename(attachment_path),
                    caption=caption,
                    parse_mode=parse_mode,
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


def split_telegram_text(text: str, limit: int) -> list[str]:
    value = (text or "").strip()
    if value == "":
        return []

    chunks: list[str] = []
    remaining = value
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at <= 0:
            split_at = remaining.rfind(" ", 0, limit + 1)
        if split_at <= 0:
            split_at = limit
        chunk = remaining[:split_at].strip()
        if not chunk:
            chunk = remaining[:limit].strip()
            split_at = len(chunk)
        chunks.append(chunk)
        remaining = remaining[split_at:].lstrip()

    if remaining:
        chunks.append(remaining)

    return chunks
