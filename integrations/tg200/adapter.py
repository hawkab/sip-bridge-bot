import asyncio

from integrations.event_store.client import EventStoreClient
from services.event_router import handle_sms_notification
from services.delivery_service import DeliveryHub


async def start_reader(ys, delivery: DeliveryHub, event_store: EventStoreClient):
    pending = set()

    async def sms_cb(sender, sim, when, text):
        await handle_sms_notification(delivery, event_store, sender, sim, when, text)

    def dispatch(*args):
        task = asyncio.create_task(sms_cb(*args))
        pending.add(task)
        task.add_done_callback(pending.discard)

    async def run():
        try:
            await ys.connect_forever()
        finally:
            ys.on_sms = None
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    ys.on_sms = dispatch
    return asyncio.create_task(run(), name='gsm-gateway')
