import asyncio
import logging

logger = logging.getLogger(__name__)


class VoiceOutboxWorker:
    def __init__(self, client, calls, delivery):
        self.client, self.calls, self.delivery = client, calls, delivery

    async def run_forever(self):
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception('Voice call queue temporarily unavailable')
            await asyncio.sleep(2)

    async def run_once(self):
        ports = getattr(self.calls, 'sender_ports', None)
        heartbeat = await self.client.request('heartbeat', **({'ports': ports} if ports is not None else {}))
        active = False
        for job in self.calls.journals():
            if job.get('reported'):
                continue
            result = job.get('result') or self.calls.result(job)
            if not result:
                active = True
                continue
            await self.client.request('complete', id=job['id'], claim_token=job['claim_token'], **result)
            self.calls.save({**job, 'reported': True, 'result': result})
        if active or heartbeat.get('has_due') is False or not await self.calls.ready():
            return
        job = (await self.client.request('claim')).get('job')
        if not job:
            return
        try:
            audio = await self.client.download(job['id'])
            await self.calls.prepare_and_originate(job, audio)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.exception('Voice call preparation failed')
            existing = next((j for j in self.calls.journals() if j['id'] == job['id']), None)
            if existing:
                # Publishing may have succeeded. Reconcile the receipt instead of redialing.
                return
            message = str(error) if isinstance(error, ValueError) else 'Не удалось подготовить запись для вызова.'
            self.calls.save({**job, 'reported': False, 'result': {'status': 'failed', 'message': message}})
