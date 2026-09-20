"""Russian call ASR: detect speech per channel, then decode each interval separately."""
from __future__ import annotations

import math
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import numpy as np
from faster_whisper.audio import decode_audio
from faster_whisper.vad import VadOptions, get_speech_timestamps

from integrations.transcription.stereo import ChannelResult, split_segment_into_phrases

MODEL_NAME = 'gigaam-v3-e2e-rnnt'
SAMPLE_RATE = 16000


def load_model(model_path: str | None, cpu_threads: int):
    import onnx_asr
    import onnxruntime

    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = cpu_threads
    options.inter_op_num_threads = 1
    return onnx_asr.load_model(
        MODEL_NAME, model_path or None, quantization='int8',
        sess_options=options, providers=['CPUExecutionProvider'],
    ).with_timestamps()


def decode_stereo(wav_path: Path):
    # Reject mono input instead of silently assigning the same voice to two speakers.
    with wave.open(str(wav_path)) as wav:
        if wav.getnchannels() != 2:
            raise ValueError('Call transcription requires a stereo PCM WAV recording')
    return decode_audio(str(wav_path), sampling_rate=SAMPLE_RATE, split_stereo=True)


def aligned_segment(result: Any, offset: float, end: float) -> SimpleNamespace:
    """Adapt ONNX token alignment to the existing phrase splitter without losing text.

    Tokens already contain leading spaces. Token timestamps denote emission times,
    not word boundaries; a final 40 ms frame estimates each word's end. Malformed
    alignment falls back to the whole VAD interval, preserving recognized text.
    """
    segment = SimpleNamespace(start=offset, end=end, text=result.text, words=[])
    tokens = getattr(result, 'tokens', None) or []
    times = getattr(result, 'timestamps', None) or []
    if not tokens or len(tokens) != len(times):
        return segment
    previous = 0.0
    for token, timestamp in zip(tokens, times):
        timestamp = float(timestamp)
        if not math.isfinite(timestamp) or timestamp < previous or timestamp < 0:
            segment.words = []
            return segment
        previous = timestamp
        start = min(end, offset + timestamp)
        token = str(token)
        if not token:
            continue
        if not segment.words or token[0].isspace():
            segment.words.append(SimpleNamespace(word=token, start=start, end=min(end, start + .04)))
        else:
            segment.words[-1].word += token
            segment.words[-1].end = min(end, start + .04)
    return segment


def transcribe_channel(
    audio: np.ndarray, get_model: Callable[[], Any], *, speaker: str,
    channel_name: str, vad_min_silence_ms: int, split_gap_seconds: float,
    punctuation_gap_seconds: float, max_phrase_seconds: float,
) -> ChannelResult:
    rows = []
    intervals = []
    # A silent channel must never reach the recognizer, even if VAD misbehaves.
    if audio.size and np.any(audio):
        intervals = get_speech_timestamps(audio, VadOptions(
            threshold=.5, min_speech_duration_ms=0,
            min_silence_duration_ms=vad_min_silence_ms,
            max_speech_duration_s=20, speech_pad_ms=300,
        ))
        for interval in intervals:
            start, end = interval['start'], interval['end']
            result = get_model().recognize(audio[start:end], sample_rate=SAMPLE_RATE)
            rows.extend(split_segment_into_phrases(
                seg=aligned_segment(result, start / SAMPLE_RATE, end / SAMPLE_RATE),
                speaker=speaker, channel_name=channel_name,
                split_gap_seconds=split_gap_seconds,
                punctuation_gap_seconds=punctuation_gap_seconds,
                max_phrase_seconds=max_phrase_seconds,
            ))
    # The language is fixed by the model, not detected with a measured probability.
    return ChannelResult(speaker, channel_name, 'ru', None, rows,
        sum(i['end']-i['start'] for i in intervals)/SAMPLE_RATE)
