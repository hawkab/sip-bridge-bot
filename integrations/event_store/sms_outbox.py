import httpx


class SmsOutboxClient:
    def __init__(self, config):
        self.url = config.SMS_OUTBOX_URL
        self.token = config.EVENT_STORE_AUTH_TOKEN

    @property
    def enabled(self):
        return bool(self.url and self.token)

    async def request(self, action=None, **payload):
        if not self.enabled:
            raise RuntimeError('Отправка СМС не настроена.')
        async with httpx.AsyncClient(timeout=15) as client:
            headers = {'Authentication': self.token, 'Accept': 'application/json'}
            if action:
                response = await client.post(self.url, headers=headers, json={'action': action, **payload})
            else:
                response = await client.get(self.url, headers=headers)
        try:
            body = response.json()
        except ValueError:
            raise RuntimeError('Сервис отправки СМС недоступен.') from None
        if not response.is_success or not body.get('ok'):
            raise RuntimeError(body.get('message') or 'Не удалось обработать СМС.')
        return body
