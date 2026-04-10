import asyncio
import logging
import os

from domain.models import ResponseItem
from services.retry_policy import TELEGRAM_RETRY_DELAYS
from telegram.error import NetworkError, RetryAfter, TimedOut

logger = logging.getLogger(__name__)


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


async def send_tg_document_direct(app, chat_id: int, item: ResponseItem) -> tuple[bool, str | None]:
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
