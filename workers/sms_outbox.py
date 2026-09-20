import asyncio
import logging
from integrations.tg200.client import SmsSendResult

logger = logging.getLogger(__name__)


class SmsOutboxWorker:
    def __init__(self, client, gateway, config, delivery):
        self.client, self.gateway, self.config, self.delivery = client, gateway, config, delivery
        self.pending_result = None

    async def run_forever(self):
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception('SMS outbox temporarily unavailable')
            await asyncio.sleep(2)

    async def run_once(self):
        heartbeat = await self.client.request('heartbeat', ports=self.config.SMS_SIM_PORTS, connected=self.gateway.ready.is_set())
        if self.pending_result:
            await self._complete()
            return
        if not self.gateway.ready.is_set() or heartbeat.get('has_pending') is False:
            return
        claimed = await self.client.request('claim')
        job = claimed.get('job')
        if not job:
            return
        allowed = {p['port'] for p in self.config.SMS_SIM_PORTS}
        if job['port'] not in allowed:
            result = SmsSendResult('failed', 'Выбранная SIM больше не доступна.')
        else:
            try:
                result = await self.gateway.send_sms(job['number'], job['text'], job['port'], span_offset=self.config.SMS_SPAN_OFFSET)
            except Exception:
                logger.exception('SMS send failed; no automatic resend')
                result = SmsSendResult('unknown', 'Результат неизвестен. Проверьте получателя перед повтором.')
        # Retry persistence only, never the physical send. A crash leaves a
        # claimed job as unknown; it is deliberately never claimed again.
        self.pending_result = (job, result)
        await self._complete()

    async def _complete(self):
        job, result = self.pending_result
        await self.client.request('complete', id=job['id'], claim_token=job['claim_token'],
                                  status=result.status, message=result.message, gateway_id=result.gateway_id)
        self.pending_result = None
        logger.info('SMS %s: %s (SIM %s)', job['id'], result.status, job['port'])
        await self.delivery.notify_event(subject='SipBridgeBot: результат отправки СМС',
            text=f"СМС {job['id']}\nОт: {job['sender'] or 'SIM ' + str(job['port'])}\nКому: {job['number']}\n{result.message}", parse_mode=None,
            telegram_enabled=False)
