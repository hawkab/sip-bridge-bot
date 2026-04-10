import base64
import logging
import mimetypes
import os

import httpx

logger = logging.getLogger(__name__)


class EventStoreClient:
    def __init__(self, config):
        self.config = config

    def is_sms_enabled(self) -> bool:
        return bool(self.config.EVENT_STORE_SMS_URL and self.config.EVENT_STORE_AUTH_TOKEN)

    def is_call_enabled(self) -> bool:
        return bool(self.config.EVENT_STORE_CALL_URL and self.config.EVENT_STORE_AUTH_TOKEN)

    async def save_sms(self, *, timestamp: str, number: str, text: str) -> str | None:
        if not self.is_sms_enabled():
            return None
        payload = {
            "timestamp": timestamp,
            "number": number,
            "text": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        }
        return await self._post_json(self.config.EVENT_STORE_SMS_URL, payload, "sms")

    async def save_call(
        self,
        *,
        call_type: str,
        timestamp: str,
        number: str,
        duration: int,
        recording_path: str | None = None,
        recording_name: str | None = None,
    ) -> str | None:
        if not self.is_call_enabled():
            return None

        if recording_path and os.path.exists(recording_path):
            data = {
                "type": call_type,
                "timestamp": timestamp,
                "number": number,
                "duration": str(max(0, duration)),
            }
            mime_type, _ = mimetypes.guess_type(recording_name or recording_path)
            content_type = mime_type or "application/octet-stream"
            with open(recording_path, "rb") as source:
                files = {
                    "recording": (
                        recording_name or os.path.basename(recording_path),
                        source.read(),
                        content_type,
                    )
                }
            return await self._post_form(self.config.EVENT_STORE_CALL_URL, data, files, "call")

        payload = {
            "type": call_type,
            "timestamp": timestamp,
            "number": number,
            "duration": max(0, duration),
        }
        return await self._post_json(self.config.EVENT_STORE_CALL_URL, payload, "call")

    async def _post_json(self, url: str, payload: dict, event_kind: str) -> str | None:
        try:
            async with httpx.AsyncClient(timeout=self.config.EVENT_STORE_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers=self._build_headers(json_request=True),
                    follow_redirects=True,
                )
                response.raise_for_status()
            return self._parse_view_url(response, event_kind)
        except Exception:
            logger.exception("Failed to save %s event via JSON endpoint %s", event_kind, url)
            return None

    async def _post_form(self, url: str, data: dict, files: dict, event_kind: str) -> str | None:
        try:
            async with httpx.AsyncClient(timeout=self.config.EVENT_STORE_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    url,
                    data=data,
                    files=files,
                    headers=self._build_headers(json_request=False),
                    follow_redirects=True,
                )
                response.raise_for_status()
            return self._parse_view_url(response, event_kind)
        except Exception:
            logger.exception("Failed to save %s event via multipart endpoint %s", event_kind, url)
            return None

    def _parse_view_url(self, response: httpx.Response, event_kind: str) -> str | None:
        try:
            payload = response.json()
        except Exception:
            logger.exception("Event store returned non-JSON response for %s: %s", event_kind, response.text)
            return None

        view_url = str(payload.get("view_url") or "").strip()
        if not view_url:
            logger.error("Event store response for %s does not contain view_url: %s", event_kind, payload)
            return None
        return view_url

    def _build_headers(self, *, json_request: bool) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Authentication": self.config.EVENT_STORE_AUTH_TOKEN,
        }
        if json_request:
            headers["Content-Type"] = "application/json"
        return headers
