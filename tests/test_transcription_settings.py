import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from integrations.transcription.settings import TranscriptionSettingsClient
from integrations.transcription.stereo import StereoCallTranscriber


class SettingsTests(unittest.IsolatedAsyncioTestCase):
    def config(self):
        return SimpleNamespace(CALL_TRANSCRIBE_SETTINGS_URL='https://example.test/transcription_settings.php',
            EVENT_STORE_AUTH_TOKEN='test-token', CALL_TRANSCRIBE_SETTINGS_TIMEOUT_SECONDS=3,
            CALL_TRANSCRIBE_BACKEND='gigaam', CALL_TRANSCRIBE_ENABLED=True)

    async def test_selection_and_invalid_response_keep_last_known_value(self):
        settings = TranscriptionSettingsClient(self.config())
        client = AsyncMock()
        request = httpx.Request('GET', settings.url)
        client.get.side_effect = [
            httpx.Response(200, json={'ok': True, 'settings': {'backend': 'whisper'}}, request=request),
            httpx.Response(200, json={'ok': True, 'settings': {'backend': 'invalid'}}, request=request),
            httpx.ReadTimeout('timeout'),
            httpx.Response(302, headers={'Location': 'https://other.test/'}, request=request),
        ]
        with patch('integrations.http.create_http_client', return_value=client) as factory:
            for _ in range(4):
                self.assertEqual(await settings.get_backend(), 'whisper')
            factory.assert_called_once_with()
        self.assertEqual(client.get.call_args.kwargs['timeout'], 3)
        self.assertFalse(client.get.call_args.kwargs['follow_redirects'])
        await settings.aclose()
        client.aclose.assert_awaited_once()
        self.assertEqual(client.get.call_args.kwargs['headers']['Authentication'], 'test-token')

    async def test_disabled_endpoint_uses_environment(self):
        cfg = self.config()
        cfg.CALL_TRANSCRIBE_SETTINGS_URL = ''
        with patch('integrations.http.create_http_client') as client:
            self.assertEqual(await TranscriptionSettingsClient(cfg).get_backend(), 'gigaam')
            client.assert_not_called()

    async def test_queued_calls_switch_model_only_between_recordings(self):
        transcriber = StereoCallTranscriber(self.config())
        transcriber._model = object()
        transcriber._settings_client.get_backend = AsyncMock(side_effect=['whisper', 'gigaam'])
        def recognize(path):
            self.assertIsNone(transcriber._model)
            transcriber._model = object()
            return {'backend': transcriber._backend}
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / 'call.wav'
            wav.touch()
            with patch.object(transcriber, '_transcribe_blocking', side_effect=recognize):
                results = await asyncio.gather(transcriber.transcribe_recording(str(wav)), transcriber.transcribe_recording(str(wav)))
        self.assertEqual(results, [{'backend': 'whisper'}, {'backend': 'gigaam'}])


if __name__ == '__main__':
    unittest.main()
