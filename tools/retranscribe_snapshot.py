"""Build a GigaAM replacement plan from an authenticated site snapshot.

No network requests, uploads, notifications or production writes are performed.
Audio files must be named <audio_sha256>.wav. Missing/changed audio or ASR errors
abort preparation; an empty successful result deliberately clears old text.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def speaker_aliases(rows, record):
    direction = str(record.get('type', '')).strip().lower()
    if direction not in {'входящий', 'исходящий'}:
        raise ValueError('Unknown call direction; cannot assign channel speakers')
    inbound = direction == 'входящий'
    number = str(record.get('number', 'Абонент'))
    aliases = {'left': number if inbound else 'Я', 'right': 'Я' if inbound else number}
    return [dict(row, speaker=aliases[row['channel']]) for row in rows]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('audio_dir', type=Path)
    parser.add_argument('output_dir', type=Path)
    parser.add_argument('--gigaam-model-path', default='')
    parser.add_argument('--cpu-threads', type=int, default=2)
    args = parser.parse_args()
    if args.cpu_threads < 1:
        parser.error('cpu-threads must be positive')
    # Plans contain private conversation text, just like the original snapshot.
    os.umask(0o077)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    snapshot = json.loads(args.snapshot.read_text())
    calls = snapshot['calls']
    from integrations.transcription.stereo import StereoCallTranscriber
    cfg = SimpleNamespace(
        CALL_TRANSCRIBE_BACKEND='gigaam', CALL_TRANSCRIBE_GIGAAM_CHANNEL_WORKERS=2,
        CALL_TRANSCRIBE_CHANNEL_WORKERS=1, CALL_TRANSCRIBE_CPU_THREADS=args.cpu_threads,
        CALL_TRANSCRIBE_GIGAAM_MODEL_PATH=args.gigaam_model_path,
        CALL_TRANSCRIBE_ENABLED=True, CALL_TRANSCRIBE_SETTINGS_URL='',
        CALL_TRANSCRIBE_LEFT_LABEL='SPEAKER_1', CALL_TRANSCRIBE_RIGHT_LABEL='SPEAKER_2',
        CALL_TRANSCRIBE_VAD_MIN_SILENCE_MS=500, CALL_TRANSCRIBE_SPLIT_GAP_SECONDS=.8,
        CALL_TRANSCRIBE_PUNCTUATION_GAP_SECONDS=.35, CALL_TRANSCRIBE_MAX_PHRASE_SECONDS=0,
        CALL_TRANSCRIBE_MERGE_GAP=.15,
    )
    transcriber = StereoCallTranscriber(cfg)
    updates, statistics, skipped = [], [], []
    started = time.perf_counter()
    for index, entry in enumerate(calls):
        record = entry['record']
        sha = entry['audio_sha256']
        if sha is None:
            skipped.append(record['id'])
            continue
        if len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
            raise ValueError('Invalid recording hash')
        audio = args.audio_dir / (sha + '.wav')
        if hashlib.sha256(audio.read_bytes()).hexdigest() != sha:
            raise ValueError('Recording hash mismatch')
        start = time.perf_counter()
        result = transcriber._transcribe_blocking(audio)
        rows = speaker_aliases(result['conversation'], record)
        updates.append(dict(id=record['id'], version=entry['version'], audio_sha256=sha, transcription=rows))
        elapsed = time.perf_counter() - start
        statistics.append(dict(id=record['id'], seconds=elapsed, segments=len(rows),
                               old_segments=len(record.get('transcription') or [])))
        (args.output_dir / f'result-{index:04d}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(f'{index + 1}/{len(calls)}: {len(rows)} segments, {elapsed:.2f}s', flush=True)
    summary = dict(backend='gigaam-v3-e2e-rnnt', quantization='int8',
                   prepared=len(updates), skipped_without_audio=skipped,
                   empty_results=sum(not row['transcription'] for row in updates),
                   seconds=time.perf_counter() - started, records=statistics)
    (args.output_dir / 'plan.json').write_text(json.dumps({'updates':updates}, ensure_ascii=False, indent=2))
    (args.output_dir / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f'Prepared {len(updates)} updates; skipped {len(skipped)} calls without audio.', flush=True)


if __name__ == '__main__':
    main()
