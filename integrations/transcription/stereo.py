from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)

_SENTENCE_END_RE = re.compile(r'[.!?…]+["»”)]*$')


@dataclass
class ChannelResult:
    speaker: str
    channel: str
    detected_language: str | None
    language_probability: float | None
    segments: list[dict[str, Any]]


class StereoCallTranscriber:
    def __init__(self, config):
        self.config = config
        self._model: WhisperModel | None = None
        self._model_lock = Lock()
        self._transcribe_lock = asyncio.Lock()

    def is_enabled(self) -> bool:
        return bool(self.config.CALL_TRANSCRIBE_ENABLED)

    async def transcribe_recording(self, wav_path: str | None) -> dict[str, Any] | None:
        if not self.is_enabled() or not wav_path:
            return None

        path = Path(wav_path)
        if not path.exists() or not path.is_file():
            logger.warning('Transcription skipped: file not found: %s', path)
            return None

        async with self._transcribe_lock:
            try:
                return await asyncio.to_thread(self._transcribe_blocking, path)
            except Exception:
                logger.exception('Failed to transcribe recording: %s', path)
                return None

    def transcribe_to_json_text(self, wav_path: str | Path, indent: int = 2) -> str:
        payload = self._transcribe_blocking(Path(wav_path))
        return json.dumps(payload, ensure_ascii=False, indent=indent)

    def _transcribe_blocking(self, wav_path: Path) -> dict[str, Any]:
        ensure_ffmpeg()

        with tempfile.TemporaryDirectory(prefix='stereo_transcribe_') as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            left_wav = tmp_dir / 'left.wav'
            right_wav = tmp_dir / 'right.wav'

            run_ffmpeg_extract_channel(wav_path, left_wav, 0)
            run_ffmpeg_extract_channel(wav_path, right_wav, 1)

            model = self._get_model()
            left_result = transcribe_channel(
                model=model,
                wav_path=left_wav,
                speaker=self.config.CALL_TRANSCRIBE_LEFT_LABEL,
                channel_name='left',
                language='ru',
                beam_size=self.config.CALL_TRANSCRIBE_BEAM_SIZE,
                vad_filter=self.config.CALL_TRANSCRIBE_VAD_FILTER,
                vad_min_silence_ms=self.config.CALL_TRANSCRIBE_VAD_MIN_SILENCE_MS,
                split_gap_seconds=self.config.CALL_TRANSCRIBE_SPLIT_GAP_SECONDS,
                punctuation_gap_seconds=self.config.CALL_TRANSCRIBE_PUNCTUATION_GAP_SECONDS,
                max_phrase_seconds=self.config.CALL_TRANSCRIBE_MAX_PHRASE_SECONDS,
            )
            right_result = transcribe_channel(
                model=model,
                wav_path=right_wav,
                speaker=self.config.CALL_TRANSCRIBE_RIGHT_LABEL,
                channel_name='right',
                language='ru',
                beam_size=self.config.CALL_TRANSCRIBE_BEAM_SIZE,
                vad_filter=self.config.CALL_TRANSCRIBE_VAD_FILTER,
                vad_min_silence_ms=self.config.CALL_TRANSCRIBE_VAD_MIN_SILENCE_MS,
                split_gap_seconds=self.config.CALL_TRANSCRIBE_SPLIT_GAP_SECONDS,
                punctuation_gap_seconds=self.config.CALL_TRANSCRIBE_PUNCTUATION_GAP_SECONDS,
                max_phrase_seconds=self.config.CALL_TRANSCRIBE_MAX_PHRASE_SECONDS,
            )
            return build_output_json(
                input_wav=wav_path,
                model_name=self.config.CALL_TRANSCRIBE_MODEL,
                device=self.config.CALL_TRANSCRIBE_DEVICE,
                compute_type=self.config.CALL_TRANSCRIBE_COMPUTE_TYPE,
                language='ru',
                vad_filter=self.config.CALL_TRANSCRIBE_VAD_FILTER,
                vad_min_silence_ms=self.config.CALL_TRANSCRIBE_VAD_MIN_SILENCE_MS,
                merge_gap=self.config.CALL_TRANSCRIBE_MERGE_GAP,
                left=left_result,
                right=right_result,
            )

    def _get_model(self) -> WhisperModel:
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is None:
                logger.info(
                    'Loading Whisper model for call transcription: model=%s device=%s compute_type=%s',
                    self.config.CALL_TRANSCRIBE_MODEL,
                    self.config.CALL_TRANSCRIBE_DEVICE,
                    self.config.CALL_TRANSCRIBE_COMPUTE_TYPE,
                )
                self._model = WhisperModel(
                    self.config.CALL_TRANSCRIBE_MODEL,
                    device=self.config.CALL_TRANSCRIBE_DEVICE,
                    compute_type=self.config.CALL_TRANSCRIBE_COMPUTE_TYPE,
                )
        return self._model


def ensure_ffmpeg() -> None:
    if shutil.which('ffmpeg') is None:
        raise RuntimeError('ffmpeg not found in PATH. Install it first, for example: sudo apt install -y ffmpeg')


def run_ffmpeg_extract_channel(input_path: Path, output_path: Path, channel_index: int) -> None:
    cmd = [
        'ffmpeg',
        '-hide_banner',
        '-loglevel',
        'error',
        '-y',
        '-i',
        str(input_path),
        '-map_channel',
        f'0.0.{channel_index}',
        '-ac',
        '1',
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        stderr = result.stderr.strip() or 'unknown ffmpeg error'
        raise RuntimeError(f'ffmpeg failed while extracting channel {channel_index}: {stderr}')


def format_ts(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    ms = total_ms % 1000
    total_seconds = total_ms // 1000
    s = total_seconds % 60
    total_minutes = total_seconds // 60
    m = total_minutes % 60
    h = total_minutes // 60
    return f'{h:02d}:{m:02d}:{s:02d}.{ms:03d}'


def transcribe_channel(
    model: WhisperModel,
    wav_path: Path,
    speaker: str,
    channel_name: str,
    language: str,
    beam_size: int,
    vad_filter: bool,
    vad_min_silence_ms: int,
    split_gap_seconds: float,
    punctuation_gap_seconds: float,
    max_phrase_seconds: float,
) -> ChannelResult:
    kwargs: dict[str, Any] = {
        'beam_size': beam_size,
        'vad_filter': vad_filter,
        'language': language,
        'word_timestamps': True,
    }

    if vad_filter:
        kwargs['vad_parameters'] = {
            'min_silence_duration_ms': vad_min_silence_ms,
        }

    segments_iter, info = model.transcribe(str(wav_path), **kwargs)
    segments = list(segments_iter)

    rows: list[dict[str, Any]] = []
    for seg in segments:
        rows.extend(
            split_segment_into_phrases(
                seg=seg,
                speaker=speaker,
                channel_name=channel_name,
                split_gap_seconds=split_gap_seconds,
                punctuation_gap_seconds=punctuation_gap_seconds,
                max_phrase_seconds=max_phrase_seconds,
            )
        )

    detected_language = getattr(info, 'language', None)
    language_probability = getattr(info, 'language_probability', None)
    if language_probability is not None:
        language_probability = round(float(language_probability), 6)

    return ChannelResult(
        speaker=speaker,
        channel=channel_name,
        detected_language=detected_language,
        language_probability=language_probability,
        segments=rows,
    )


def split_segment_into_phrases(
    *,
    seg: Any,
    speaker: str,
    channel_name: str,
    split_gap_seconds: float,
    punctuation_gap_seconds: float,
    max_phrase_seconds: float,
) -> list[dict[str, Any]]:
    words = []
    for word in getattr(seg, 'words', None) or []:
        start = getattr(word, 'start', None)
        end = getattr(word, 'end', None)
        token = str(getattr(word, 'word', '') or '')
        if start is None or end is None or token == '':
            continue
        words.append(
            {
                'start': float(start),
                'end': float(end),
                'token': token,
            }
        )

    if not words:
        text = normalize_phrase_text(str(getattr(seg, 'text', '') or ''))
        if not text:
            return []
        start = round(float(getattr(seg, 'start', 0.0) or 0.0), 3)
        end = round(float(getattr(seg, 'end', start) or start), 3)
        return [build_conversation_row(speaker, channel_name, start, end, text)]

    rows: list[dict[str, Any]] = []
    bucket: list[dict[str, Any]] = []

    for word in words:
        if not bucket:
            bucket.append(word)
            continue

        previous_word = bucket[-1]
        gap = float(word['start']) - float(previous_word['end'])
        duration = float(previous_word['end']) - float(bucket[0]['start'])
        previous_token = str(previous_word['token'])

        should_split = False
        if gap >= split_gap_seconds:
            should_split = True
        elif gap >= punctuation_gap_seconds and _looks_like_sentence_end(previous_token):
            should_split = True
        elif duration >= max_phrase_seconds and gap >= min(0.25, punctuation_gap_seconds):
            should_split = True

        if should_split:
            row = build_row_from_words(speaker, channel_name, bucket)
            if row is not None:
                rows.append(row)
            bucket = [word]
        else:
            bucket.append(word)

    if bucket:
        row = build_row_from_words(speaker, channel_name, bucket)
        if row is not None:
            rows.append(row)

    return rows


def build_row_from_words(speaker: str, channel_name: str, words: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not words:
        return None

    text = normalize_phrase_text(''.join(str(word['token']) for word in words))
    if not text:
        return None

    start = round(float(words[0]['start']), 3)
    end = round(float(words[-1]['end']), 3)
    return build_conversation_row(speaker, channel_name, start, end, text)


def build_conversation_row(speaker: str, channel_name: str, start: float, end: float, text: str) -> dict[str, Any]:
    return {
        'speaker': speaker,
        'channel': channel_name,
        'start': round(float(start), 3),
        'end': round(float(end), 3),
        'start_hms': format_ts(float(start)),
        'end_hms': format_ts(float(end)),
        'text': text,
    }


def normalize_phrase_text(text: str) -> str:
    value = str(text or '').replace('\n', ' ')
    value = re.sub(r'\s+', ' ', value)
    value = re.sub(r'\s+([,.;:!?…])', r'\1', value)
    return value.strip()


def _looks_like_sentence_end(token: str) -> bool:
    return bool(_SENTENCE_END_RE.search(token.strip()))


def merge_adjacent_segments(conversation: list[dict[str, Any]], max_gap: float) -> list[dict[str, Any]]:
    if not conversation:
        return []

    merged: list[dict[str, Any]] = [dict(conversation[0])]
    effective_gap = min(float(max_gap), 0.2)

    for row in conversation[1:]:
        prev = merged[-1]

        same_speaker = prev['speaker'] == row['speaker']
        same_channel = prev['channel'] == row['channel']
        gap = float(row['start']) - float(prev['end'])

        if (
            same_speaker
            and same_channel
            and gap <= effective_gap
            and not _looks_like_sentence_end(str(prev.get('text') or ''))
        ):
            prev['end'] = row['end']
            prev['end_hms'] = row['end_hms']
            prev['text'] = normalize_phrase_text(f"{prev['text']} {row['text']}")
        else:
            merged.append(dict(row))

    return merged


def build_output_json(
    *,
    input_wav: Path,
    model_name: str,
    device: str,
    compute_type: str,
    language: str,
    vad_filter: bool,
    vad_min_silence_ms: int,
    merge_gap: float,
    left: ChannelResult,
    right: ChannelResult,
) -> dict[str, Any]:
    conversation = left.segments + right.segments
    conversation.sort(key=lambda x: (x['start'], x['end'], x['speaker'], x['channel']))
    conversation = merge_adjacent_segments(conversation, merge_gap)

    from datetime import datetime, timezone

    return {
        'input_file': str(input_wav),
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'model': model_name,
        'device': device,
        'compute_type': compute_type,
        'language_mode': language,
        'vad_filter': vad_filter,
        'vad_min_silence_ms': vad_min_silence_ms,
        'channels': {
            'left': {
                'speaker': left.speaker,
                'detected_language': left.detected_language,
                'language_probability': left.language_probability,
                'segments_count': len(left.segments),
            },
            'right': {
                'speaker': right.speaker,
                'detected_language': right.detected_language,
                'language_probability': right.language_probability,
                'segments_count': len(right.segments),
            },
        },
        'conversation': conversation,
    }
