"""Bounded, optional second pass; native inference runs outside the bot process."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from integrations.transcription.selective import correct, pack_window, select_windows

DEFAULT_MODEL = '/opt/sms/var/models/parakeet/parakeet-tdt-0.6b-v3-Q8_0.gguf'
DEFAULT_LIBRARY = '/opt/sms/var/models/parakeet/runtime/libtranscribe.so'
logger = logging.getLogger(__name__)


def recognize_batch(packed, config):
    import numpy as np

    model = getattr(config, 'CALL_TRANSCRIBE_PARAKEET_MODEL_PATH', DEFAULT_MODEL)
    library = getattr(config, 'CALL_TRANSCRIBE_PARAKEET_LIBRARY_PATH', DEFAULT_LIBRARY)
    if not Path(model).is_file() or not Path(library).is_file():
        raise FileNotFoundError('Parakeet model or native runtime is not installed')
    with tempfile.TemporaryDirectory(prefix='asr_terms_') as directory:
        inputs, output = Path(directory) / 'input.npz', Path(directory) / 'output.json'
        np.savez(inputs, **{str(i): item for i, item in enumerate(packed)})
        env = dict(os.environ, TRANSCRIBE_LIBRARY=library)
        # One short-lived model per selected call; RAM is returned on success,
        # timeout or native crash. The worker does not import bot configuration.
        # Stdout is discarded (native model diagnostics).
        subprocess.run([sys.executable, '-m', 'integrations.transcription.parakeet_worker',
            '--model', model, '--inputs', str(inputs), '--output', str(output),
            '--threads', str(getattr(config, 'CALL_TRANSCRIBE_PARAKEET_THREADS', 4))],
            cwd=Path(__file__).resolve().parents[2], env=env, check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=180)
        results = json.loads(output.read_text())
        if not isinstance(results, list) or len(results) != len(packed):
            raise ValueError('Incomplete Parakeet response')
        return results


def correct_selected(payload, intervals, audio_channels, config):
    start = time.perf_counter()
    audit = dict(provider='parakeet-tdt-0.6b-v3-Q8_0', policy_version=3,
                 status='skipped', selected_windows=0, selected_seconds=0., accepted=[])
    result = payload
    try:
        windows, selection = select_windows(intervals)
        audit.update(selected_windows=len(windows), selected_seconds=selection['selected_seconds'])
        if windows:
            packed = [pack_window(audio_channels[w['channel']], w) for w in windows]
            donors = recognize_batch([p[0] for p in packed], config)
            by_window = {id(w): (d, p[1]) for w, d, p in zip(windows, donors, packed)}
            result, details = correct(payload, windows, lambda w: by_window[id(w)])
            errors = sum(attempt['error'] is not None for attempt in details['attempts'])
            audit.update(status='partial' if errors else 'complete', accepted=details['accepted'], errors=errors)
            if errors:
                logger.warning('Parakeet skipped %d invalid donor windows; kept GigaAM text', errors)
    except Exception as exc:
        # A missing optional model, timeout or native failure must not lose a call.
        logger.warning('Parakeet unavailable (%s); kept GigaAM transcript', type(exc).__name__)
        audit.update(status='fallback', error=type(exc).__name__)
        result = payload
    audit['elapsed_seconds'] = round(time.perf_counter() - start, 4)
    return dict(result, correction=audit)
