from integrations.http import HostingHttpClient


class VoiceOutboxClient(HostingHttpClient):
    def __init__(self, config, http_client=None):
        super().__init__(http_client)
        self.url = config.VOICE_OUTBOX_URL
        self.token = config.EVENT_STORE_AUTH_TOKEN

    @property
    def enabled(self):
        return bool(self.url and self.token)

    async def request(self, action=None, *, audio=None, **payload):
        if not self.enabled:
            raise RuntimeError('Голосовые вызовы не настроены.')
        headers = {'Authentication': self.token, 'Accept': 'application/json'}
        if audio is not None:
            response = await self.http.post(self.url, headers=headers, data={'action': action, **payload},
                files={'audio': ('voice.audio', audio, 'application/octet-stream')}, timeout=30)
        elif action:
            response = await self.http.post(self.url, headers=headers, json={'action': action, **payload}, timeout=30)
        else:
            response = await self.http.get(self.url, headers=headers, timeout=30)
        try:
            body = response.json()
        except ValueError:
            raise RuntimeError('Сервис голосовых вызовов недоступен.') from None
        if not response.is_success or not body.get('ok'):
            raise RuntimeError(body.get('message') or 'Не удалось обработать вызов.')
        return body

    async def download(self, job_id):
        async with self.http.stream('GET', self.url, params={'audio': job_id},
                headers={'Authentication': self.token}, timeout=30) as response:
            response.raise_for_status()
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > 2097152:
                    raise ValueError('Запись превышает 2 МБ.')
            return bytes(data)
