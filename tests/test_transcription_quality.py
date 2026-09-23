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

    async def test_real_decoder_accepts_long_audio_and_rejects_invalid_file(self):
        transcriber = StereoCallTranscriber(config())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'sample.wav'
            for seconds in [1,601]:
                with wave.open(str(path),'wb') as wav:
                    wav.setparams((1,2,16000,0,'NONE','not compressed'))
                    wav.writeframes(b'\0\0'*16000*seconds)
                with patch.object(transcriber,'_get_model',side_effect=AssertionError('No model on silence')):
                    result = await transcriber.transcribe_sample(path,'gigaam')
                self.assertEqual(result, {'text':'','duration':float(seconds),'speech_detected':False})
            path.write_text('This is not audio')
            with self.assertRaisesRegex(ValueError,'прочитать аудио'): await transcriber.transcribe_sample(path,'gigaam')

    async def test_both_engines_keep_phrase_timestamps_and_call_parameters(self):
        transcriber = StereoCallTranscriber(config())
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'sample.wav'
            with wave.open(str(path),'wb') as wav:
                wav.setparams((2,2,8000,0,'NONE','not compressed')); wav.writeframes(b'\0'*32000)
            result = SimpleNamespace(segments=[
                {'start_hms':'00:00:01.250', 'text':' Первая реплика. '},
                {'start_hms':'00:00:02.000', 'text':'   '},
                {'start_hms':'01:02:03.450', 'text':'Вторая реплика.'},
            ],speech_seconds=.5)
            for backend, module in [('whisper', 'stereo'), ('gigaam', 'gigaam')]:
                with self.subTest(backend=backend), patch.object(transcriber,'_get_model',return_value=object()), patch(f'integrations.transcription.{module}.transcribe_channel',return_value=result) as recognize:
                    response=await transcriber.transcribe_sample(path,backend)
                self.assertEqual(response['text'],'[00:00:01.250] Первая реплика.\n[01:02:03.450] Вторая реплика.')
                self.assertEqual(recognize.call_args.kwargs['split_gap_seconds'], .8)
                if backend == 'whisper':
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
