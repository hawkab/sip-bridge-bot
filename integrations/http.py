"""Bounded connection pooling for the hosting API, independent of Telegram proxies."""
import httpx


def create_http_client():
    # No automatic retries: replaying an ambiguous claim/enqueue POST is unsafe.
    return httpx.AsyncClient(
        timeout=30, follow_redirects=False, trust_env=False,
        limits=httpx.Limits(max_connections=8, max_keepalive_connections=4, keepalive_expiry=30),
    )


class HostingHttpClient:
    def __init__(self, http_client=None):
        self._http_client = http_client
        self._owns_http_client = http_client is None
        self._closed = False

    @property
    def http(self):
        if self._closed:
            raise RuntimeError('HTTP client is closed')
        if self._http_client is None:
            self._http_client = create_http_client()
        return self._http_client

    async def aclose(self):
        self._closed = True
        if self._owns_http_client and self._http_client is not None:
            await self._http_client.aclose()
