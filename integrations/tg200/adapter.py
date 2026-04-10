import asyncio

from services.event_router import handle_sms_notification
from services.delivery_service import DeliveryHub


async def start_reader(ys, delivery: DeliveryHub):
    async def sms_cb(sender, sim, when, text):
        await handle_sms_notification(delivery, sender, sim, when, text)

    ys.on_sms = lambda s, p, w, t: asyncio.create_task(sms_cb(s, p, w, t))
    asyncio.create_task(ys.connect_forever())
