"""Benchmark real stereo WAVs without notifications, uploads or settings requests.

Run each workers/threads combination in a fresh process so peak RSS is comparable.
JSONL contains result hashes, not recognized text. Compare hashes only within the
same engine/model. Input filenames may still contain personal information.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import resource
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from integrations.transcription.stereo import StereoCallTranscriber


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('recordings', type=Path, help='Directory of stereo WAV files')
    parser.add_argument('--backend', choices=['gigaam', 'whisper'], default='gigaam')
    parser.add_argument('--channel-workers', choices=[1, 2], type=int, default=1)
    parser.add_argument('--cpu-threads', type=int, default=2)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--gigaam-model-path', default='')
    parser.add_argument('--model', default='small', help='Whisper model or local path')
    args = parser.parse_args()
    if args.cpu_threads < 1 or args.repeats < 1:
        parser.error('cpu-threads and repeats must be positive')
    paths = sorted(args.recordings.glob('*.wav'))
    if not paths:
        parser.error('No WAV files found')
    cfg = SimpleNamespace(
        CALL_TRANSCRIBE_BACKEND=args.backend, CALL_TRANSCRIBE_CHANNEL_WORKERS=args.channel_workers,
        CALL_TRANSCRIBE_CPU_THREADS=args.cpu_threads, CALL_TRANSCRIBE_ENABLED=True,
        CALL_TRANSCRIBE_GIGAAM_MODEL_PATH=args.gigaam_model_path, CALL_TRANSCRIBE_MODEL=args.model,
        CALL_TRANSCRIBE_DEVICE='cpu', CALL_TRANSCRIBE_COMPUTE_TYPE='int8', CALL_TRANSCRIBE_BEAM_SIZE=5,
        CALL_TRANSCRIBE_VAD_FILTER=True, CALL_TRANSCRIBE_VAD_MIN_SILENCE_MS=500,
        CALL_TRANSCRIBE_LEFT_LABEL='SPEAKER_1', CALL_TRANSCRIBE_RIGHT_LABEL='SPEAKER_2',
        CALL_TRANSCRIBE_SPLIT_GAP_SECONDS=.8, CALL_TRANSCRIBE_PUNCTUATION_GAP_SECONDS=.35,
        CALL_TRANSCRIBE_MAX_PHRASE_SECONDS=0, CALL_TRANSCRIBE_MERGE_GAP=.15,
    )
    transcriber = StereoCallTranscriber(cfg)
    start = time.perf_counter()
    transcriber._get_model()
    load_seconds = time.perf_counter() - start
    transcriber._transcribe_blocking(paths[0])  # Untimed warmup, including VAD.
    for trial in range(args.repeats):
        for path in paths:
            start, cpu_start = time.perf_counter(), time.process_time()
            result = transcriber._transcribe_blocking(path)
            elapsed, cpu = time.perf_counter() - start, time.process_time() - cpu_start
            encoded = json.dumps(result['conversation'], ensure_ascii=False, sort_keys=True).encode()
            print(json.dumps(dict(
                backend=args.backend, workers=args.channel_workers, threads=args.cpu_threads,
                trial=trial, case=path.name, seconds=elapsed, cpu_seconds=cpu,
                load_seconds=load_seconds, peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                result_sha256=hashlib.sha256(encoded).hexdigest(),
            )), flush=True)


if __name__ == '__main__':
    main()
