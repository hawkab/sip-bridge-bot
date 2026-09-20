from integrations.http import HostingHttpClient


class SmsOutboxClient(HostingHttpClient):
    def __init__(self, config, http_client=None):
        super().__init__(http_client)
        self.url = config.SMS_OUTBOX_URL
        self.token = config.EVENT_STORE_AUTH_TOKEN

    @property
    def enabled(self):
        return bool(self.url and self.token)

    async def request(self, action=None, **payload):
        if not self.enabled:
            raise RuntimeError('Отправка СМС не настроена.')
        headers = {'Authentication': self.token, 'Accept': 'application/json'}
        if action:
            response = await self.http.post(self.url, headers=headers, json={'action': action, **payload}, timeout=15)
        else:
            response = await self.http.get(self.url, headers=headers, timeout=15)
        try:
            body = response.json()
        except ValueError:
            raise RuntimeError('Сервис отправки СМС недоступен.') from None
        if not response.is_success or not body.get('ok'):
            raise RuntimeError(body.get('message') or 'Не удалось обработать СМС.')
        return body
