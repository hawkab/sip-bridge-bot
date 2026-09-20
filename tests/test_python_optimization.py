import asyncio
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
import wave

import httpx
from integrations.http import create_http_client
from integrations.event_store.client import EventStoreClient
from integrations.event_store.sms_outbox import SmsOutboxClient
from integrations.event_store.voice_outbox import VoiceOutboxClient
from integrations.asterisk.voice_calls import pcm_has_signal
from integrations.tg200.client import SmsSendResult
from workers.sms_outbox import SmsOutboxWorker
from workers.voice_outbox import VoiceOutboxWorker


class HttpPoolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.connections = 0
        self.requests = []
        self.writers = set()
        self.close_next = False
        self.server = await asyncio.start_server(self.handle, '127.0.0.1', 0)
        port = self.server.sockets[0].getsockname()[1]
        self.url = f'http://127.0.0.1:{port}/api'
        self.config = SimpleNamespace(SMS_OUTBOX_URL=self.url, VOICE_OUTBOX_URL=self.url,
            EVENT_STORE_AUTH_TOKEN='test-token', EVENT_STORE_CALL_URL=self.url, EVENT_STORE_TIMEOUT_SECONDS=5)

    async def handle(self, reader, writer):
        self.connections += 1
        self.writers.add(writer)
        try:
            while True:
                raw = await reader.readuntil(b'\r\n\r\n')
                lines = raw.decode().split('\r\n')
                headers = dict(line.lower().split(': ', 1) for line in lines[1:] if ': ' in line)
                body = await reader.readexactly(int(headers.get('content-length', 0)))
                self.requests.append((lines[0], headers, body))
                if '/lost ' in lines[0]:
                    return  # Ambiguous POST: no response must not cause a retry.
                payload = json.dumps({'ok':True,'saved':True,'view_url':'https://example.test/call'}).encode()
                connection = b'Connection: close\r\n' if self.close_next else b''
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: '
                    + str(len(payload)).encode() + b'\r\n' + connection + b'\r\n' + payload)
                await writer.drain()
                if self.close_next:
                    self.close_next = False
                    return
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            self.writers.discard(writer)

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()
        for writer in list(self.writers):
            writer.close()
            await writer.wait_closed()

    async def test_pool_reuse_shared_ownership_and_server_reconnect(self):
        async with create_http_client() as http:
            sms = SmsOutboxClient(self.config, http)
            voice = VoiceOutboxClient(self.config, http)
            await sms.request()
            await voice.request('heartbeat')
            await sms.aclose()
            self.assertFalse(http.is_closed)
            await voice.request()
            self.assertEqual(self.connections, 1)
            self.close_next = True
            await voice.request()
            await voice.request()
            self.assertEqual(self.connections, 2)
            self.assertTrue(all(headers['authentication'] == 'test-token' for _, headers, _ in self.requests))

    async def test_owned_client_close_and_no_ambiguous_post_retry(self):
        sms = SmsOutboxClient(self.config)
        sms.url = self.url.replace('/api', '/lost')
        with self.assertRaises(httpx.RemoteProtocolError):
            await sms.request('claim')
        self.assertEqual(len(self.requests), 1)
        sms.url = self.url
        await sms.request()
        http = sms.http
        await sms.aclose()
        self.assertTrue(http.is_closed)
        with self.assertRaisesRegex(RuntimeError, 'closed'):
            await sms.request()

    async def test_call_multipart_stream_keeps_audio_and_transcript(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'call.wav'
            audio = b'test-audio-' * 50000
            path.write_bytes(audio)
            store = EventStoreClient(self.config)
            try:
                result = await store.save_call(call_type='входящий', timestamp='2026-09-20', number='123',
                    duration=10, recording_path=str(path), transcription=[{'text':'Проверка'}])
                self.assertTrue(result.ok)
                body = self.requests[0][2]
                self.assertIn(audio, body)
                self.assertIn('Проверка'.encode(), body)
            finally:
                await store.aclose()


class IdleQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_queues_skip_claim_and_asterisk_but_finish_pending_result(self):
        api = SimpleNamespace(request=AsyncMock(return_value={'has_pending':False,'has_due':False}))
        ready = asyncio.Event(); ready.set()
        gateway = SimpleNamespace(ready=ready, send_sms=AsyncMock())
        delivery = SimpleNamespace(notify_event=AsyncMock())
        cfg = SimpleNamespace(SMS_SIM_PORTS=[], SMS_SPAN_OFFSET=1)
        sms = SmsOutboxWorker(api, gateway, cfg, delivery)
        await sms.run_once()
        self.assertEqual([c.args[0] for c in api.request.call_args_list], ['heartbeat'])
        api.request.reset_mock()
        sms.pending_result = ({'id':'x','claim_token':'token','sender':'1','number':'2','port':1}, SmsSendResult('sent','OK'))
        await sms.run_once()
        self.assertEqual([c.args[0] for c in api.request.call_args_list], ['heartbeat','complete'])
        gateway.send_sms.assert_not_awaited()
        api.request.reset_mock()
        calls = SimpleNamespace(journals=lambda:[], ready=AsyncMock())
        await VoiceOutboxWorker(api, calls, delivery).run_once()
        self.assertEqual([c.args[0] for c in api.request.call_args_list], ['heartbeat'])
        calls.ready.assert_not_awaited()

    async def test_due_hint_still_checks_asterisk_before_claiming(self):
        api = SimpleNamespace(request=AsyncMock(return_value={'has_due':True}))
        calls = SimpleNamespace(journals=lambda:[], ready=AsyncMock(return_value=False))
        await VoiceOutboxWorker(api,calls,None).run_once()
        calls.ready.assert_awaited_once()
        self.assertEqual([c.args[0] for c in api.request.call_args_list], ['heartbeat'])


class MemoryTests(unittest.TestCase):
    def test_bootstrap_does_not_import_asr_or_pdf_until_needed(self):
        code = ('import bootstrap.main, sys; '
                'assert not any(k in sys.modules for k in ("numpy", "faster_whisper", "onnxruntime", "reportlab"))')
        env = dict(os.environ)
        for key in ('BOT_TOKEN','ADMIN_LOGIN','TG_HOST','TG_USER','TG_PASS'):
            env[key] = 'test'
        subprocess.run([sys.executable, '-c', code], env=env, check=True, capture_output=True)

    def test_signal_threshold_including_late_and_negative_samples(self):
        for raw, expected in [(b'\0\0'*9000,False), (b'\x0f\x00'*9000,False),
                              (b'\0\0'*9000+b'\x10\x00',True), (b'\xf0\xff',True), (b'\x00\x80',True)]:
            data = io.BytesIO()
            with wave.open(data,'wb') as wav:
                wav.setparams((1,2,8000,0,'NONE','not compressed')); wav.writeframes(raw)
            with wave.open(io.BytesIO(data.getvalue()),'rb') as wav:
                self.assertEqual(pcm_has_signal(wav), expected)
