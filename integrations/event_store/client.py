import base64
import logging
import mimetypes
import os
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


@dataclass
class CallStoreResult:
    ok: bool
    view_url: str | None = None
    error_message: str | None = None


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
        result = await self._post_json(self.config.EVENT_STORE_SMS_URL, payload, "sms")
        return result.view_url

    async def save_call(
        self,
        *,
        call_type: str,
        timestamp: str,
        number: str,
        duration: int,
        recording_path: str | None = None,
        recording_name: str | None = None,
    ) -> CallStoreResult:
        if not self.is_call_enabled():
            return CallStoreResult(ok=False, error_message="call event store is disabled")

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

    async def _post_json(self, url: str, payload: dict, event_kind: str) -> CallStoreResult:
        try:
            async with httpx.AsyncClient(timeout=self.config.EVENT_STORE_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers=self._build_headers(json_request=True),
                    follow_redirects=True,
                )
            return self._parse_response(response, event_kind)
        except Exception as exc:
            logger.exception("Failed to save %s event via JSON endpoint %s", event_kind, url)
            return CallStoreResult(ok=False, error_message=str(exc) or exc.__class__.__name__)

    async def _post_form(self, url: str, data: dict, files: dict, event_kind: str) -> CallStoreResult:
        try:
            async with httpx.AsyncClient(timeout=self.config.EVENT_STORE_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    url,
                    data=data,
                    files=files,
                    headers=self._build_headers(json_request=False),
                    follow_redirects=True,
                )
            return self._parse_response(response, event_kind)
        except Exception as exc:
            logger.exception("Failed to save %s event via multipart endpoint %s", event_kind, url)
            return CallStoreResult(ok=False, error_message=str(exc) or exc.__class__.__name__)

    def _parse_response(self, response: httpx.Response, event_kind: str) -> CallStoreResult:
        payload = self._try_parse_json(response, event_kind)
        if payload is None:
            return CallStoreResult(
                ok=False,
                error_message=f"{response.status_code} {response.reason_phrase}".strip(),
            )

        view_url = str(payload.get("view_url") or "").strip() or None
        ok_flag = bool(payload.get("ok"))
        if response.is_success and ok_flag and view_url:
            return CallStoreResult(ok=True, view_url=view_url)

        error_message = str(payload.get("error") or payload.get("message") or "").strip()
        if not error_message:
            error_message = f"{response.status_code} {response.reason_phrase}".strip()

        if response.is_success and ok_flag and not view_url:
            logger.error("Event store response for %s does not contain view_url: %s", event_kind, payload)
            return CallStoreResult(ok=False, error_message="view_url is missing in event store response")

        logger.error(
            "Event store returned an error for %s: status=%s payload=%s",
            event_kind,
            response.status_code,
            payload,
        )
        return CallStoreResult(ok=False, view_url=view_url, error_message=error_message)

    def _try_parse_json(self, response: httpx.Response, event_kind: str) -> dict | None:
        try:
            payload = response.json()
        except Exception:
            logger.exception("Event store returned non-JSON response for %s: %s", event_kind, response.text)
            return None
        return payload if isinstance(payload, dict) else None

    def _build_headers(self, *, json_request: bool) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Authentication": self.config.EVENT_STORE_AUTH_TOKEN,
        }
        if json_request:
            headers["Content-Type"] = "application/json"
        return headers
