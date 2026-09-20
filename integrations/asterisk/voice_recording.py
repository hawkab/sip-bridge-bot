#!/usr/bin/env python3
"""Finalize MixMonitor's closed stereo RAW file after a voice call ends."""
import array
import os
from pathlib import Path
import re
import sys
import wave


def finalize_recording(directory):
    raw = directory/'conversation.raw'
    destination = directory/'conversation.wav'
    if not raw.exists() and destination.exists():
        return destination
    if raw.stat().st_size == 0 or raw.stat().st_size % 4:
        raise ValueError('Incomplete stereo PCM recording')
    temporary = directory/'conversation.partial.wav'
    with raw.open('rb') as source, temporary.open('wb') as target:
        with wave.open(target, 'wb') as wav:
            wav.setparams((2, 2, 8000, 0, 'NONE', 'not compressed'))
            while chunk := source.read(65536):
                samples = array.array('h', chunk)
                # On the outbound PJSIP channel MixMonitor D writes RX,TX.
                # Existing outgoing-call labels require left=us, right=remote.
                samples[::2], samples[1::2] = samples[1::2], samples[::2]
                wav.writeframesraw(samples.tobytes())
        target.flush()
        os.fsync(target.fileno())
    os.replace(temporary, destination)
    raw.unlink()
    return destination


if __name__ == '__main__':
    path = Path(sys.argv[1]).resolve()
    if not re.fullmatch(r'[a-f0-9]{32}', path.name) or not (path/'job.json').is_file():
        sys.exit(1)
    finalize_recording(path)
