import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from domain.models import ResponseItem

logger = logging.getLogger(__name__)

FAILED_TG_QUEUE = Path("/opt/sms/failed_telegram.queue")
LEGACY_QUEUE_BLOCK_RE = re.compile(
    r"(?ms)^--- (?P<created_at>.+?) ---\n"
    r"chat_id=(?P<chat_id>\d+)\n"
    r"(?:last_error=.*\n)?"
    r"(?P<text>.*?)(?=^--- .+? ---\n|\Z)"
)


@dataclass
class QueuedTelegramMessage:
    chat_id: int
    item: ResponseItem
    created_at: str
    last_error: str | None = None


def append_failed_message(chat_id: int, item: ResponseItem, last_error: str | None) -> None:
    queue = load_failed_queue()
    queue.append(
        QueuedTelegramMessage(
            chat_id=chat_id,
            item=item,
            created_at=datetime.now().isoformat(timespec="seconds"),
            last_error=last_error,
        )
    )
    store_failed_queue(queue)


def load_failed_queue() -> list[QueuedTelegramMessage]:
    if not FAILED_TG_QUEUE.exists():
        return []
    raw = FAILED_TG_QUEUE.read_text(encoding="utf-8").strip()
    if not raw:
        return []
    if raw.startswith("{"):
        return _parse_json_queue(raw)
    return _parse_legacy_queue(raw)


def store_failed_queue(queue: list[QueuedTelegramMessage]) -> None:
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


def _parse_json_queue(raw: str) -> list[QueuedTelegramMessage]:
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


def _parse_legacy_queue(raw: str) -> list[QueuedTelegramMessage]:
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
