#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

from integrations.transcription.stereo import StereoCallTranscriber


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Transcribe stereo WAV into JSON conversation. By default prints JSON to stdout.'
    )
    parser.add_argument('input_wav', help='Path to input stereo WAV file.')
    parser.add_argument('-o', '--output', default=None, help='Optional output JSON file. If omitted, JSON is printed to stdout.')
    parser.add_argument('--model', default='small', help='Whisper model size or local CTranslate2 model. Default: small')
    parser.add_argument('--device', default='cpu', help='Inference device. Default: cpu')
    parser.add_argument('--compute-type', default='int8', help='Compute type. Default: int8')
    parser.add_argument('--beam-size', type=int, default=5, help='Beam size. Default: 5')
    parser.add_argument('--merge-gap', type=float, default=0.8, help='Merge adjacent segments gap in seconds. Default: 0.8')
    parser.add_argument('--left-label', default='SPEAKER_1', help='Label for left channel speaker. Default: SPEAKER_1')
    parser.add_argument('--right-label', default='SPEAKER_2', help='Label for right channel speaker. Default: SPEAKER_2')
    parser.add_argument('--vad-filter', action=argparse.BooleanOptionalAction, default=True, help='Enable or disable VAD filter. Default: enabled')
    parser.add_argument('--vad-min-silence-ms', type=int, default=500, help='Minimum silence duration for VAD. Default: 500')
    parser.add_argument('--indent', type=int, default=2, help='JSON indentation. Default: 2')
    return parser.parse_args()


def build_cli_config(args: argparse.Namespace) -> SimpleNamespace:
    return SimpleNamespace(
        CALL_TRANSCRIBE_ENABLED=True,
        CALL_TRANSCRIBE_MODEL=args.model,
        CALL_TRANSCRIBE_DEVICE=args.device,
        CALL_TRANSCRIBE_COMPUTE_TYPE=args.compute_type,
        CALL_TRANSCRIBE_BEAM_SIZE=args.beam_size,
        CALL_TRANSCRIBE_MERGE_GAP=args.merge_gap,
        CALL_TRANSCRIBE_VAD_FILTER=args.vad_filter,
        CALL_TRANSCRIBE_VAD_MIN_SILENCE_MS=args.vad_min_silence_ms,
        CALL_TRANSCRIBE_LEFT_LABEL=args.left_label,
        CALL_TRANSCRIBE_RIGHT_LABEL=args.right_label,
        CALL_TRANSCRIBE_ARTIFACTS_DIR=Path('/tmp/sip-bridge-bot-transcriptions'),
    )


def main() -> int:
    args = parse_args()
    input_wav = Path(args.input_wav).expanduser()
    if not input_wav.exists():
        print(f'Input file not found: {input_wav}', file=sys.stderr)
        return 2

    try:
        transcriber = StereoCallTranscriber(build_cli_config(args))
        payload = transcriber.transcribe_to_json_text(input_wav, indent=args.indent)
        if args.output:
            output_path = Path(args.output).expanduser()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(payload + '\n', encoding='utf-8')
        else:
            sys.stdout.write(payload)
            sys.stdout.write('\n')
            sys.stdout.flush()
        return 0
    except KeyboardInterrupt:
        print('Interrupted', file=sys.stderr)
        return 130
    except Exception as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
