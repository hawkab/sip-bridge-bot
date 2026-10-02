"""CPU Nemotron 3.5 ASR using NVIDIA NeMo-Speech.cpp's stable C ABI v1.

The runtime/model are installed explicitly; recognition never downloads files.
Only one recognizer is resident, and the existing VAD feeds bounded utterances.
ABI definitions follow NVIDIA's installed include/nemo_speech/asr.h (0.1.0).
"""
from __future__ import annotations

import ctypes as c
import re
from pathlib import Path
from types import SimpleNamespace

import numpy as np

MODEL_NAME = 'nvidia/nemotron-3.5-asr-streaming-0.6b'
DEFAULT_ROOT = '/opt/sms/var/models/nemotron'
DEFAULT_LIBRARY = DEFAULT_ROOT + '/runtime/lib/libnemo_speech_asr_c.so.1'
DEFAULT_MODEL = DEFAULT_ROOT + '/nemotron-3.5-asr-streaming-0.6b.q8_0.gguf'
_LANGUAGE_TAG = re.compile(r'<[a-z]{2,3}(?:-[A-Za-z]{2,4})?>')


class _Backend(c.Structure):
    _fields_ = [('size', c.c_size_t), ('gpu', c.c_int32)]


class _Model(c.Structure):
    _fields_ = [('size', c.c_size_t), ('path', c.c_char_p), ('name', c.c_char_p)]


class _Streaming(c.Structure):
    _fields_ = [('size', c.c_size_t), ('chunk_size', c.c_float),
                ('left_padding', c.c_float), ('right_padding', c.c_float),
                ('rnnt_right_context', c.c_int32)]


class _Config(c.Structure):
    _fields_ = [('size', c.c_size_t)] + [(name, c.c_void_p) for name in (
        'backend', 'model', 'streaming', 'decoder', 'vad', 'endpointing',
        'postproc', 'diar', 'batching')]


class _Options(c.Structure):
    _fields_ = [
        ('size', c.c_size_t), ('request_id', c.c_char_p), ('language_code', c.c_char_p),
        ('interim_results', c.c_bool), ('enable_word_time_offsets', c.c_bool),
        ('enable_automatic_punctuation', c.c_bool), ('verbatim_transcripts', c.c_bool),
        ('profanity_filter', c.c_bool), ('stop_history_eou_ms', c.c_int32),
        ('speech_contexts', c.c_void_p), ('speech_context_count', c.c_size_t),
        ('max_alternatives', c.c_int32), ('enable_speaker_diarization', c.c_bool),
        ('max_speaker_count', c.c_int32),
    ]


class NemotronModel:
    def __init__(self, model_path: str, library_path: str):
        self.handle = c.c_void_p()
        if not Path(model_path).is_file() or not Path(library_path).is_file():
            raise RuntimeError('Nemotron ASR не установлен. Запустите tools/install_nemotron.py на Raspberry.')
        self.lib = c.CDLL(str(Path(library_path).resolve()))
        pointer, size, integer = c.c_void_p, c.c_size_t, c.c_int32
        self._bind('version', [], c.c_char_p)
        if self._version().decode() != 'nemo-speech-asr 0.1.0':
            raise RuntimeError('Требуется NeMo-Speech.cpp 0.1.0; проверьте установку Nemotron.')
        self._bind('create', [c.POINTER(_Config), c.POINTER(pointer)], integer)
        self._bind('destroy', [pointer], None)
        self._bind('recognition_options_default', [], _Options)
        self._bind('recognize_f32', [pointer, c.POINTER(_Options), c.POINTER(c.c_float),
                                   size, integer, c.POINTER(pointer)], integer)
        self._bind('last_error', [], c.c_char_p)
        self._bind('result_destroy', [pointer], None)
        self._bind('result_transcript', [pointer, size], c.c_char_p)
        self._bind('result_word_count', [pointer, size], size)
        self._bind('result_word_text', [pointer, size, size], c.c_char_p)
        self._bind('result_word_start_time', [pointer, size, size], integer)
        backend = _Backend(c.sizeof(_Backend), -1)
        model = _Model(c.sizeof(_Model), str(model_path).encode(), None)
        streaming = _Streaming(c.sizeof(_Streaming), .16, 1.92, 1.92, -1)
        config = _Config()
        config.size = c.sizeof(config)
        config.backend, config.model, config.streaming = map(c.addressof, (backend, model, streaming))
        self._check(self._create(c.byref(config), c.byref(self.handle)))

    def _bind(self, name, args, result):
        function = getattr(self.lib, 'nemo_speech_asr_' + name)
        function.argtypes, function.restype = args, result
        setattr(self, '_' + name, function)

    def _check(self, status):
        if status:
            raise RuntimeError((self._last_error() or b'Nemotron ASR failed').decode(errors='replace'))

    def recognize(self, audio: np.ndarray, sample_rate: int = 16000):
        if not self.handle:
            raise RuntimeError('Nemotron recognizer is closed')
        samples = np.ascontiguousarray(audio, dtype=np.float32)
        if samples.ndim != 1 or sample_rate != 16000 or not np.isfinite(samples).all():
            raise ValueError('Nemotron requires finite mono 16 kHz audio')
        options = self._recognition_options_default()
        options.language_code = b'ru-RU'
        options.enable_word_time_offsets = True
        options.enable_automatic_punctuation = True
        result = c.c_void_p()
        try:
            self._check(self._recognize_f32(self.handle, c.byref(options),
                samples.ctypes.data_as(c.POINTER(c.c_float)), len(samples), sample_rate, c.byref(result)))
            text = _LANGUAGE_TAG.sub('', (self._result_transcript(result, 0) or b'').decode()).strip()
            count = self._result_word_count(result, 0)
            tokens, timestamps = [], []
            for i in range(count):
                raw = (self._result_word_text(result, 0, i) or b'').decode()
                word = _LANGUAGE_TAG.sub('', raw).strip()
                # 0.1.0 may retain language tags in word alignment after removing
                # them from the transcript. Never reconstruct those as speech.
                if not word or (_LANGUAGE_TAG.search(raw) and not any(character.isalnum() for character in word)):
                    continue
                if tokens and not any(character.isalnum() for character in word):
                    tokens[-1] += word
                else:
                    tokens.append(' ' + word)
                    timestamps.append(self._result_word_start_time(result, 0, i) / 1000)
            if not any(character.isalnum() for character in text):
                text = ''
            return SimpleNamespace(text=text, tokens=tokens, timestamps=timestamps)
        finally:
            if result:
                self._result_destroy(result)

    def close(self):
        if self.handle:
            self._destroy(self.handle)
            self.handle = c.c_void_p()

    def __del__(self):
        self.close()
