import asyncio
import ctypes as c
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import numpy as np
from integrations.transcription.nemotron import NemotronModel, _Options
from integrations.transcription.stereo import StereoCallTranscriber
from test_gigaam_transcription import config


class NemotronTests(unittest.TestCase):
    def test_silent_call_does_not_load_native_model(self):
        cfg=config();cfg.CALL_TRANSCRIBE_BACKEND='nemotron'
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'silent.wav'
            with wave.open(str(path),'wb') as wav:
                wav.setparams((2,2,8000,0,'NONE','not compressed'));wav.writeframes(b'\0'*32000)
            t=StereoCallTranscriber(cfg)
            with patch.object(t,'_get_model',side_effect=AssertionError('Model used on silence')):
                result=t._transcribe_blocking(path)
            self.assertEqual(result['backend'],'nemotron')
            self.assertEqual(result['compute_type'],'q8_0')
            self.assertEqual(result['conversation'],[])
            self.assertEqual(result['channels']['left']['status'],'no_speech')

    def test_missing_model_fails_without_network(self):
        with self.assertRaisesRegex(RuntimeError,'не установлен'):
            NemotronModel('/missing/model.gguf','/missing/runtime.so')

    def test_native_words_use_seconds_and_result_is_always_freed(self):
        model=NemotronModel.__new__(NemotronModel);model.handle=c.c_void_p(1)
        model._recognition_options_default=lambda:_Options()
        model._destroy=Mock();model._result_destroy=Mock()
        def recognize(handle,options,samples,count,rate,result):
            self.assertEqual(options._obj.language_code,b'ru-RU')
            result._obj.value=2
            return 0
        model._recognize_f32=recognize
        model._result_transcript=lambda *_:'Привет, мир.'.encode()
        model._result_word_count=lambda *_:2
        model._result_word_text=lambda _,alt,i:['Привет,','мир.'][i].encode()
        model._result_word_start_time=lambda _,alt,i:[1250,2500][i]
        result=model.recognize(np.ones(16000,dtype=np.float32))
        self.assertEqual(result.timestamps,[1.25,2.5])
        self.assertEqual(result.tokens,[' Привет,',' мир.'])
        model._result_destroy.assert_called_once()
        model._result_transcript=lambda *_:'Привет, мир. <ru-RU>.'.encode()
        model._result_word_count=lambda *_:4
        model._result_word_text=lambda _,alt,i:['Привет,','мир','.', '<ru-RU>.'][i].encode()
        model._result_word_start_time=lambda _,alt,i:[1250,2500,2600,2700][i]
        result=model.recognize(np.ones(16000))
        self.assertEqual(result.tokens,[' Привет,',' мир.'])
        self.assertEqual(result.timestamps,[1.25,2.5])

        model._result_transcript=lambda *_:b'\xff'
        with self.assertRaises(UnicodeDecodeError): model.recognize(np.ones(16000))
        self.assertEqual(model._result_destroy.call_count,3)
        model.close();model.close();model._destroy.assert_called_once()

    def test_native_channels_remain_serial(self):
        cfg=config();cfg.CALL_TRANSCRIBE_BACKEND='nemotron';cfg.CALL_TRANSCRIBE_CHANNEL_WORKERS=2
        with patch('integrations.transcription.stereo.ThreadPoolExecutor',side_effect=AssertionError('Native oversubscription')):
            self.assertEqual(StereoCallTranscriber(cfg)._run_channels(str,['left','right']),['left','right'])


class LifetimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_call_keeps_native_handle_until_inference_finishes(self):
        cfg=config();cfg.CALL_TRANSCRIBE_BACKEND='nemotron'
        t=StereoCallTranscriber(cfg);model=Mock();t._model=model
        entered,release=threading.Event(),threading.Event()
        def recognize(path):
            entered.set();release.wait(5);return {'conversation':[]}
        with tempfile.NamedTemporaryFile() as file, patch.object(t,'_transcribe_blocking',side_effect=recognize):
            task=asyncio.create_task(t.transcribe_recording(file.name))
            await asyncio.to_thread(entered.wait,3);task.cancel()
            closing=asyncio.create_task(t.aclose());await asyncio.sleep(.02)
            model.close.assert_not_called();self.assertTrue(t._transcribe_lock.locked())
            release.set()
            with self.assertRaises(asyncio.CancelledError):await task
            await closing
        model.close.assert_called_once()
        self.assertIsNone(t._model)
