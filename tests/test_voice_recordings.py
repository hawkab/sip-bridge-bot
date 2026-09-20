import array
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
import wave

for key in ('BOT_TOKEN', 'ADMIN_LOGIN', 'TG_HOST', 'TG_USER', 'TG_PASS'):
    os.environ.setdefault(key, 'test')
from integrations.asterisk.voice_recording import finalize_recording
from integrations.event_store.client import CallStoreResult
from workers.voice_recordings import VoiceRecordingWorker


class RecordingTests(unittest.TestCase):
    def test_closed_raw_becomes_stereo_with_us_left_and_remote_right(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = array.array('h', [100, -200, 300, -400])*20000
            (root/'conversation.raw').write_bytes(original.tobytes())
            result = finalize_recording(root)
            with wave.open(str(result), 'rb') as wav:
                self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()), (2, 2, 8000))
                samples = array.array('h', wav.readframes(wav.getnframes()))
            self.assertEqual(samples[::2], original[1::2])
            self.assertEqual(samples[1::2], original[::2])
            self.assertFalse((root/'conversation.raw').exists())
            self.assertEqual(finalize_recording(root), result)

    def test_incomplete_raw_is_preserved_for_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'conversation.raw').write_bytes(b'abc')
            with self.assertRaises(ValueError):
                finalize_recording(root)
            self.assertFalse((root/'conversation.wav').exists())
            self.assertEqual((root/'conversation.raw').read_bytes(), b'abc')


class ArchiveTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.job = {'id': 'a'*32, 'started_at': 1789910400, 'number': '+79990000002',
                    'sender': '+79990000001', 'port': 1}
        self.directory = self.root/self.job['id']; self.directory.mkdir()
        (self.directory/'result.json').write_text('{"status":"completed"}')
        with wave.open(str(self.directory/'conversation.wav'), 'wb') as wav:
            wav.setparams((2, 2, 8000, 0, 'NONE', 'not compressed'))
            wav.writeframes(b'\x01\x00\x02\x00'*16000)
        self.payload = {'channels': {'left': {'speaker': 'SPEAKER_1'}, 'right': {'speaker': 'SPEAKER_2'}},
                        'conversation': [{'channel': 'left', 'start_hms': '00:00:00', 'end_hms': '00:00:01', 'text': 'Здравствуйте.'},
                                         {'channel': 'right', 'start_hms': '00:00:01', 'end_hms': '00:00:02', 'text': 'Сообщение получил.'}]}
        self.transcriber = SimpleNamespace(is_enabled=lambda: True,
            transcribe_recording=AsyncMock(return_value=self.payload))
        self.store = SimpleNamespace(save_call=AsyncMock(return_value=CallStoreResult(True, 'https://example.test/call')))
        self.pdf = SimpleNamespace(render_for_recording=Mock(return_value=('call.pdf', 'call.pdf')))
        self.delivery = SimpleNamespace(notify_event=AsyncMock())
        self.worker = VoiceRecordingWorker(SimpleNamespace(root=self.root, journals=lambda: [self.job]),
            self.store, self.transcriber, self.pdf, self.delivery)

    def tearDown(self):
        self.temp.cleanup()

    def state(self):
        return json.loads((self.directory/'archive.json').read_text())

    async def retry(self):
        with patch('workers.voice_recordings.time.time', return_value=self.state()['next_attempt_at']+1):
            await self.worker.run_once()

    async def test_save_labels_and_notify_once_across_restart(self):
        await self.worker.run_once(); await self.worker.run_once()
        self.assertTrue(self.state()['done'])
        self.transcriber.transcribe_recording.assert_awaited_once()
        self.delivery.notify_event.assert_awaited_once()
        args = self.store.save_call.call_args.kwargs
        self.assertEqual(args['source_id'], 'voice:'+'a'*32)
        self.assertEqual(args['number'], self.job['number'])
        self.assertEqual(args['local_number'], self.job['sender'])
        self.assertEqual([x['speaker'] for x in args['transcription']], ['Я', self.job['number']])
        notification = self.delivery.notify_event.call_args.kwargs
        self.assertIn('Сообщение получил.', notification['email_text'])
        self.assertTrue(notification['attachment_path'].endswith('/conversation.wav'))
        self.assertEqual(notification['telegram_bundle_attachment_path'], 'call.pdf')
        # Recreating the worker uses the durable receipt, not in-memory dedup.
        await VoiceRecordingWorker(self.worker.calls, self.store, self.transcriber, self.pdf, self.delivery).run_once()
        self.store.save_call.assert_awaited_once()

    async def test_lost_hosting_reply_retries_same_id_without_retranscription_or_notifications(self):
        self.store.save_call.side_effect = [CallStoreResult(False, error_message='lost response'), CallStoreResult(True, 'url')]
        await self.worker.run_once()
        self.assertFalse(self.state()['done'])
        await self.retry()
        self.assertTrue(self.state()['done'])
        self.assertEqual(self.store.save_call.await_count, 2)
        self.transcriber.transcribe_recording.assert_awaited_once()
        self.delivery.notify_event.assert_awaited_once()
        self.assertEqual(self.store.save_call.call_args_list[0].kwargs['source_id'], self.store.save_call.call_args_list[1].kwargs['source_id'])

    async def test_asr_failure_still_saves_audio_then_updates_same_card(self):
        self.transcriber.transcribe_recording.side_effect = [None, self.payload]
        await self.worker.run_once()
        self.assertTrue(self.state()['saved']); self.assertFalse(self.state()['done'])
        self.assertIsNone(self.store.save_call.call_args.kwargs['transcription'])
        self.delivery.notify_event.assert_not_awaited()
        await self.retry()
        self.assertTrue(self.state()['done'])
        self.delivery.notify_event.assert_awaited_once()

    async def test_raw_and_unfinished_calls_are_not_uploaded(self):
        (self.directory/'result.json').unlink()
        await self.worker.run_once()
        self.store.save_call.assert_not_awaited()
        (self.directory/'result.json').write_text('{}')
        (self.directory/'conversation.wav').rename(self.directory/'conversation.raw')
        await self.worker.run_once()
        self.store.save_call.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
