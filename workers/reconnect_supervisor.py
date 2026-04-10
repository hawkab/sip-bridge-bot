import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


async def run_named_worker(name: str, factory: Callable[[], Awaitable[None]], delay: float = 30.0) -> None:
    while True:
        try:
            await factory()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Worker %s failed", name)
        logger.warning("Worker %s will retry in %s seconds", name, delay)
        await asyncio.sleep(delay)
