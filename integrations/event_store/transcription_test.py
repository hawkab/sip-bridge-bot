from urllib.parse import urljoin

from integrations.http import HostingHttpClient


class TranscriptionTestClient(HostingHttpClient):
    def __init__(self, config, http_client=None):
        super().__init__(http_client)
        self.url = urljoin(config.CALL_TRANSCRIBE_SETTINGS_URL, 'transcription_test_worker.php') if config.CALL_TRANSCRIBE_SETTINGS_URL else ''
        self.token = config.EVENT_STORE_AUTH_TOKEN

    @property
    def enabled(self):
        return bool(self.url and self.token)

    async def request(self, action, **payload):
        response = await self.http.post(self.url, headers={'Authentication':self.token, 'Accept':'application/json'},
            json={'action':action, **payload}, timeout=30)
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or body.get('ok') is not True:
            raise RuntimeError('Сервис проверки транскрибации недоступен.')
        return body

    async def download(self, job_id):
        async with self.http.stream('GET', self.url, params={'audio': job_id},
                headers={'Authentication': self.token}, timeout=60) as response:
            response.raise_for_status()
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > 20 * 1024 * 1024:
                    raise ValueError('Запись превышает 20 МБ.')
            return bytes(data)
