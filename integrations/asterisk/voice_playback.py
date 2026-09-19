#!/usr/bin/env python3
"""Run by Asterisk AGI after the recipient answers; no bot configuration needed."""
import json
import os
from pathlib import Path
import re
import signal
import sys
import time


def command(text):
    print(text, flush=True)
    response = sys.stdin.readline()
    match = re.search(r'200 result=(-?\d+)', response)
    if not match:
        raise EOFError('Asterisk channel closed')
    return int(match[1])


def play(directory):
    # AGI stdin starts with channel metadata terminated by an empty line.
    while True:
        line = sys.stdin.readline()
        if not line or line in ('\n', '\r\n'):
            break
    result = {'status': 'interrupted', 'message': 'Абонент ответил, но воспроизведение прервано.'}
    try:
        time.sleep(1)
        code = command(f'STREAM FILE "{directory / "message"}" ""')
        if code == 0:
            result = {'status': 'completed', 'message': 'Абонент ответил; запись воспроизведена полностью.'}
    except (EOFError, BrokenPipeError, OSError):
        pass
    finally:
        temporary = directory/'result.tmp'
        with temporary.open('w') as stream:
            json.dump(result, stream, ensure_ascii=False)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, directory/'result.json')


if __name__ == '__main__':
    # Asterisk sends SIGHUP on hangup. Keep the process alive long enough to
    # persist the interrupted result; STREAM FILE itself returns -1 / EOF.
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    path = Path(sys.argv[1]).resolve()
    if not re.fullmatch(r'[a-f0-9]{32}', path.name) or not (path/'job.json').is_file():
        sys.exit(1)
    play(path)
