import json
import tempfile
import unittest
import wave
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from integrations.transcription import gigaam
from integrations.transcription.stereo import StereoCallTranscriber, split_segment_into_phrases
from integrations.transcription import stereo
from services.formatters.transcription import format_transcription


def config():
    return SimpleNamespace(
        CALL_TRANSCRIBE_ENABLED=True, CALL_TRANSCRIBE_BACKEND='gigaam',
        CALL_TRANSCRIBE_GIGAAM_MODEL_PATH='', CALL_TRANSCRIBE_CPU_THREADS=2,
        CALL_TRANSCRIBE_LEFT_LABEL='SPEAKER_1', CALL_TRANSCRIBE_RIGHT_LABEL='SPEAKER_2',
        CALL_TRANSCRIBE_VAD_MIN_SILENCE_MS=500, CALL_TRANSCRIBE_SPLIT_GAP_SECONDS=.8,
        CALL_TRANSCRIBE_PUNCTUATION_GAP_SECONDS=.35, CALL_TRANSCRIBE_MAX_PHRASE_SECONDS=0,
        CALL_TRANSCRIBE_MERGE_GAP=.15,
    )


class TranscriptionTests(unittest.TestCase):
    def test_silent_stereo_never_loads_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'silence.wav'
            with wave.open(str(path), 'wb') as wav:
                wav.setparams((2, 2, 8000, 0, 'NONE', 'not compressed'))
                wav.writeframes(np.zeros((8000, 2), dtype='<i2').tobytes())
            transcriber = StereoCallTranscriber(config())
            with patch.object(transcriber, '_get_model', side_effect=AssertionError('ASR on silence')):
                payload = json.loads(transcriber.transcribe_to_json_text(path))
            self.assertEqual(payload['conversation'], [])
            self.assertEqual(payload['channels']['left']['segments_count'], 0)
            self.assertEqual(payload['channels']['left']['status'], 'no_speech')
            self.assertTrue(payload['vad_filter'])
            self.assertEqual(payload['backend'], 'gigaam')

    def test_vad_negative_noise_never_calls_recognizer(self):
        get_model = Mock(side_effect=AssertionError('ASR without speech'))
        with patch.object(gigaam, 'get_speech_timestamps', return_value=[]):
            result = gigaam.transcribe_channel(
                np.full(16000, .001, dtype=np.float32), get_model,
                speaker='SPEAKER_1', channel_name='left', vad_min_silence_ms=500,
                split_gap_seconds=.8, punctuation_gap_seconds=.35, max_phrase_seconds=0,
            )
        self.assertEqual(result.segments, [])
        get_model.assert_not_called()

    def test_empty_decoder_result_is_distinguished_from_silence(self):
        model = Mock()
        model.recognize.return_value = SimpleNamespace(text='', tokens=[], timestamps=[])
        with patch.object(gigaam, 'get_speech_timestamps', return_value=[{'start':0,'end':16000}]):
            result = gigaam.transcribe_channel(np.ones(16000, dtype=np.float32)*.01, lambda:model,
                speaker='S', channel_name='left', vad_min_silence_ms=500,
                split_gap_seconds=.8, punctuation_gap_seconds=.35, max_phrase_seconds=0)
        payload = stereo.build_output_json(input_wav=Path('fixture.wav'), model_name='test',device='cpu',compute_type='int8',
            language='ru',vad_filter=True,vad_min_silence_ms=500,merge_gap=.15,left=result,right=result)
        self.assertEqual(payload['conversation'], [])
        self.assertEqual(payload['channels']['left']['status'], 'unrecognized')
        self.assertEqual(payload['channels']['left']['speech_seconds'], 1)

    def test_repeated_prompts_keep_original_time_and_channel(self):
        audio = np.ones(16000 * 12, dtype=np.float32) * .01
        intervals = [{'start': 16000, 'end': 32000}, {'start': 160000, 'end': 176000}]
        model = Mock()
        model.recognize.return_value = SimpleNamespace(text='Наберите номер.', tokens=[' Наберите', ' номер', '.'], timestamps=[.1, .4, .6])
        with patch.object(gigaam, 'get_speech_timestamps', return_value=intervals):
            result = gigaam.transcribe_channel(
                audio, lambda: model, speaker='SPEAKER_2', channel_name='right',
                vad_min_silence_ms=500, split_gap_seconds=.8,
                punctuation_gap_seconds=.35, max_phrase_seconds=0,
            )
        self.assertEqual(model.recognize.call_count, 2)
        self.assertEqual([s['text'] for s in result.segments], ['Наберите номер.'] * 2)
        self.assertEqual([s['start'] for s in result.segments], [1.1, 10.1])
        self.assertTrue(all(s['channel'] == 'right' for s in result.segments))
        self.assertIn('00:00:10.100', format_transcription(result.segments))

    def test_wordpieces_and_punctuation_preserve_text(self):
        result = SimpleNamespace(text='Добро пожаловать. Ещё раз.',
            tokens=[' До', 'б', 'ро', ' по', 'жа', 'ловать', '.', ' Ещё', ' раз', '.'],
            timestamps=[.1, .2, .3, .5, .6, .7, .8, 2., 2.3, 2.4])
        segment = gigaam.aligned_segment(result, 10., 14.)
        rows = split_segment_into_phrases(seg=segment, speaker='S', channel_name='left',
            split_gap_seconds=.8, punctuation_gap_seconds=.35, max_phrase_seconds=0)
        self.assertEqual([r['text'] for r in rows], ['Добро пожаловать.', 'Ещё раз.'])
        self.assertEqual(rows[1]['start'], 12.)

    def test_invalid_alignment_preserves_whole_text(self):
        for times in [[.2], [.2, float('nan')], [.2, .1]]:
            result = SimpleNamespace(text='Добрый день.', tokens=[' Добрый', ' день.'], timestamps=times)
            segment = gigaam.aligned_segment(result, 5., 8.)
            rows = split_segment_into_phrases(seg=segment, speaker='S', channel_name='left',
                split_gap_seconds=.8, punctuation_gap_seconds=.35, max_phrase_seconds=0)
            self.assertEqual(rows[0]['text'], result.text)
            self.assertEqual((rows[0]['start'], rows[0]['end']), (5., 8.))

    def test_mono_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'mono.wav'
            with wave.open(str(path), 'wb') as wav:
                wav.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
                wav.writeframes(b'\0\0' * 8000)
            with self.assertRaisesRegex(ValueError, 'stereo'):
                gigaam.decode_stereo(path)

    def test_model_is_reused(self):
        transcriber = StereoCallTranscriber(config())
        with patch.object(gigaam, 'load_model', return_value=object()) as load:
            first = transcriber._get_model()
            self.assertIs(first, transcriber._get_model())
            load.assert_called_once_with('', 2)

    def test_whisper_vad_intervals_keep_global_timestamps(self):
        @dataclass
        class Word:
            word: str = ' Алло.'
            start: float = .1
            end: float = .5
        @dataclass
        class Segment:
            text: str
            start: float
            end: float
            words: list
        model = Mock()
        model.transcribe.side_effect = lambda *a, **k: (iter([Segment('Алло.', .1, .5, [Word()])]), SimpleNamespace(language='ru', language_probability=1))
        with patch('faster_whisper.audio.decode_audio', return_value=np.ones(16000 * 12, dtype=np.float32)), patch('faster_whisper.vad.get_speech_timestamps', return_value=[{'start': 16000, 'end': 32000}, {'start': 160000, 'end': 176000}]):
            result = stereo.transcribe_channel(model, Path('unused.wav'), 'S', 'left', 'ru', 5, True, 500, .8, .35, 0)
        self.assertEqual([r['start'] for r in result.segments], [1.1, 10.1])
        self.assertEqual([r['text'] for r in result.segments], ['Алло.', 'Алло.'])
        self.assertFalse(model.transcribe.call_args.kwargs['vad_filter'])


if __name__ == '__main__':
    unittest.main()
