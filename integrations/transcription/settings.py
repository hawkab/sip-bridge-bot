"""Read the UI engine selection, retaining the last known value on network errors."""
import logging

import httpx
from integrations.http import HostingHttpClient

logger = logging.getLogger(__name__)


class TranscriptionSettingsClient(HostingHttpClient):
    def __init__(self, config, http_client=None):
        super().__init__(http_client)
        self.url = config.CALL_TRANSCRIBE_SETTINGS_URL
        self.token = config.EVENT_STORE_AUTH_TOKEN
        self.timeout = config.CALL_TRANSCRIBE_SETTINGS_TIMEOUT_SECONDS
        self.backend = config.CALL_TRANSCRIBE_BACKEND

    async def get_backend(self) -> str:
        if not self.url or not self.token:
            return self.backend
        try:
            response = await self.http.get(self.url, headers={
                'Accept': 'application/json', 'Authentication': self.token,
                'Cache-Control': 'no-cache',
            }, timeout=self.timeout, follow_redirects=False)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or data.get('ok') is not True:
                raise ValueError('Unsuccessful settings response')
            settings = data.get('settings')
            backend = settings.get('backend') if isinstance(settings, dict) else None
            if backend not in {'whisper', 'gigaam'}:
                raise ValueError('Unsupported transcription backend in settings')
            self.backend = backend
        except (httpx.HTTPError, ValueError, TypeError):
            # Do not log response bodies or URLs, which may contain authentication data.
            logger.warning('Cannot read transcription settings; retaining backend=%s', self.backend)
        return self.backend
