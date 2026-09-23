import asyncio
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from integrations.transcription.stereo import StereoCallTranscriber
from workers.transcription_test import TranscriptionTestWorker


def config():
    return SimpleNamespace(CALL_TRANSCRIBE_BACKEND='gigaam', CALL_TRANSCRIBE_ENABLED=True,
        CALL_TRANSCRIBE_VAD_MIN_SILENCE_MS=500, CALL_TRANSCRIBE_SPLIT_GAP_SECONDS=.8,
        CALL_TRANSCRIBE_PUNCTUATION_GAP_SECONDS=.35, CALL_TRANSCRIBE_MAX_PHRASE_SECONDS=0,
        CALL_TRANSCRIBE_BEAM_SIZE=5, CALL_TRANSCRIBE_VAD_FILTER=True)


class QualityTests(unittest.IsolatedAsyncioTestCase):
    async def test_selected_backend_is_serialized_and_does_not_change_call_preference(self):
        transcriber = StereoCallTranscriber(config())
        entered, release = threading.Event(), threading.Event()
        def recognize(path):
            self.assertEqual(transcriber._backend, 'whisper')
            entered.set(); release.wait(3)
            return {'text':'Проверка'}
        with patch.object(transcriber, '_transcribe_sample_blocking', side_effect=recognize):
            task = asyncio.create_task(transcriber.transcribe_sample(Path('sample'), 'whisper'))
            await asyncio.to_thread(entered.wait, 3)
            self.assertTrue(transcriber._transcribe_lock.locked())
            task.cancel(); await asyncio.sleep(.01)
            self.assertTrue(transcriber._transcribe_lock.locked())
            release.set()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(transcriber._backend, 'gigaam')
        self.assertFalse(transcriber._transcribe_lock.locked())

    async def test_real_decoder_silence_invalid_file_and_duration_limit(self):
        transcriber = StereoCallTranscriber(config())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'sample.wav'
            for seconds in [1,301]:
                with wave.open(str(path),'wb') as wav:
                    wav.setparams((1,2,16000,0,'NONE','not compressed'))
                    wav.writeframes(b'\0\0'*16000*seconds)
                if seconds == 301:
                    with self.assertRaisesRegex(ValueError,'5 минут'): await transcriber.transcribe_sample(path,'gigaam')
                else:
                    with patch.object(transcriber,'_get_model',side_effect=AssertionError('No model on silence')):
                        result = await transcriber.transcribe_sample(path,'gigaam')
                    self.assertEqual(result, {'text':'','duration':1.0,'speech_detected':False})
            path.write_text('This is not audio')
            with self.assertRaisesRegex(ValueError,'прочитать аудио'): await transcriber.transcribe_sample(path,'gigaam')

    async def test_whisper_uses_same_call_parameters(self):
        transcriber = StereoCallTranscriber(config())
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'sample.wav'
            with wave.open(str(path),'wb') as wav:
                wav.setparams((2,2,8000,0,'NONE','not compressed')); wav.writeframes(b'\0'*32000)
            result = SimpleNamespace(segments=[{'text':'Тест.'}],speech_seconds=.5)
            with patch.object(transcriber,'_get_model',return_value=object()), patch('integrations.transcription.stereo.transcribe_channel',return_value=result) as recognize:
                response=await transcriber.transcribe_sample(path,'whisper')
            self.assertEqual(response['text'],'Тест.')
            self.assertTrue(recognize.call_args.kwargs['vad_filter'])
            self.assertEqual(recognize.call_args.kwargs['language'],'ru')

    async def test_completion_retry_does_not_repeat_recognition_and_removes_local_audio(self):
        client = SimpleNamespace(request=AsyncMock(), download=AsyncMock(return_value=b'audio'))
        transcriber = SimpleNamespace(transcribe_sample=AsyncMock(return_value={'text':'Ответ','duration':1,'speech_detected':True}))
        client.request.side_effect=[{'job':{'id':'a'*32,'backend':'gigaam','claim_token':'token'}},RuntimeError('lost response'),{'ok':True}]
        worker=TranscriptionTestWorker(client,transcriber)
        with self.assertRaises(RuntimeError): await worker.run_once()
        sample = transcriber.transcribe_sample.call_args.args[0]
        self.assertFalse(sample.exists())
        await worker.run_once()
        self.assertIsNone(worker.pending_result)
        transcriber.transcribe_sample.assert_awaited_once()
        self.assertEqual(client.request.call_args.args,('complete',))

    async def test_decode_failure_becomes_a_visible_error(self):
        client=SimpleNamespace(request=AsyncMock(return_value={'job':{'id':'a'*32,'backend':'gigaam','claim_token':'t'}}),download=AsyncMock(return_value=b'bad'))
        worker=TranscriptionTestWorker(client,SimpleNamespace(transcribe_sample=AsyncMock(side_effect=ValueError('Неверное аудио.'))))
        await worker.run_once()
        self.assertEqual(client.request.call_args.kwargs['status'],'failed')
        self.assertEqual(client.request.call_args.kwargs['message'],'Неверное аудио.')
