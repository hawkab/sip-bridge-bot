"""Private subprocess entry point for bounded Parakeet inference (no bot imports)."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path

import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--inputs', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--threads', type=int, default=4)
    args = parser.parse_args()
    if args.threads < 1:
        parser.error('threads must be positive')
    with np.load(args.inputs, allow_pickle=False) as archive:
        if not 1 <= len(archive.files) <= 8:
            raise ValueError('Invalid window count')
        audio = [np.ascontiguousarray(archive[str(i)], dtype=np.float32) for i in range(len(archive.files))]
    if any(a.ndim != 1 or not 0 < len(a) <= 21 * 16000 or not np.isfinite(a).all() for a in audio):
        raise ValueError('Invalid audio window')
    if sum(map(len, audio)) > 60 * 16000 + 1:
        raise ValueError('Second-pass budget exceeded')
    from transcribe_cpp import Model
    with Model(args.model, backend='cpu') as model:
        with model.session(n_threads=args.threads) as session:
            results = [asdict(session.run(a, timestamps='token')) for a in audio]
    Path(args.output).write_text(json.dumps(results, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
