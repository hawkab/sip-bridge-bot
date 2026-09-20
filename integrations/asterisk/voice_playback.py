#!/usr/bin/env python3
"""Wait for GSM audio readiness before playing an answered call's message."""
import json
import math
import os
from pathlib import Path
import re
import signal
import sys
import time
import wave

READY_TIMEOUT = 45
# Longer than the silent gap between Russian GSM ringback bursts.
QUIET_TIMEOUT = 6
RESPONSE_SECONDS = 15


class AGI:
    def __init__(self):
        self.started = time.monotonic()
        self.metadata = {}
        self.trace = []
        for line in sys.stdin:
            if line in ('\n', '\r\n'):
                break
            key, _, value = line.rstrip().partition(': ')
            self.metadata[key] = value

    def command(self, text):
        print(text, flush=True)
        response = sys.stdin.readline().rstrip()
        self.trace.append({'seconds': round(time.monotonic()-self.started, 3),
                           'command': text, 'response': response})
        match = re.fullmatch(r'200 result=(-?\d+)(?: \((.*)\))?(?: endpos=(\d+))?', response)
        if not match:
            raise EOFError('Asterisk did not return a valid AGI response')
        code = int(match[1])
        if code < 0:
            raise EOFError('Asterisk channel closed or application failed')
        return code, match[2], int(match[3]) if match[3] else None

    def variable(self, name):
        code, value, _ = self.command(f'GET FULL VARIABLE "${{{name}}}"')
        if code != 1 or value is None:
            raise ValueError(f'Asterisk variable unavailable: {name}')
        return value

    def wait(self, app, milliseconds, seconds):
        self.command('SET VARIABLE WAITSTATUS ""')
        self.command(f'EXEC {app} "{milliseconds},1,{seconds}"')
        status = self.variable('WAITSTATUS')
        if status not in ('NOISE', 'SILENCE', 'TIMEOUT'):
            raise EOFError(f'{app}: {status}')
        return status


def await_audio(agi):
    """Don't mistake post-answer ringback, or its silent gaps, for readiness."""
    deadline = time.monotonic()+READY_TIMEOUT
    # ToneScan has no Russian progress zone. Use the actual 450 Hz GSM tone,
    # receive direction only, without suppressing the audio or changing routes.
    agi.command('SET VARIABLE TONE_DETECT(450,350,r) ""')
    hits = int(agi.variable('TONE_DETECT(rx)'))
    pjsip = agi.metadata.get('agi_channel', '').startswith('PJSIP/')
    # A normally answered call needs only one second of quiet. Keep the longer
    # cadence guard only if the gateway actually sends post-answer ringing.
    rx_before = int(agi.variable('CHANNEL(rtcp,rxcount)')) if pjsip else None
    silence = agi.wait('WaitForSilence', 1000, 5)
    current = int(agi.variable('TONE_DETECT(rx)'))
    has_audio = not pjsip or int(agi.variable('CHANNEL(rtcp,rxcount)')) > rx_before
    if silence == 'SILENCE' and current == hits and has_audio:
        agi.command('SET VARIABLE TONE_DETECT(0,,x) ""')
        return 'answered_audio'
    hits = current
    while time.monotonic() < deadline:
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            break
        window = min(QUIET_TIMEOUT, math.ceil(remaining))
        rx_before = int(agi.variable('CHANNEL(rtcp,rxcount)')) if pjsip else None
        noise = agi.wait('WaitForNoise', 200, window)
        if noise == 'NOISE':
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                break
            silence = agi.wait('WaitForSilence', 1000, min(5, math.ceil(remaining)))
        else:
            silence = 'SILENCE'
        current = int(agi.variable('TONE_DETECT(rx)'))
        if current > hits or silence != 'SILENCE':
            hits = current
            continue
        # A short final timeout, or absent inbound RTP, cannot establish quiet.
        # Noise requires actual audio frames; digital silence alone does not.
        if noise == 'TIMEOUT':
            if window < QUIET_TIMEOUT:
                break
            if pjsip and int(agi.variable('CHANNEL(rtcp,rxcount)')) <= rx_before:
                continue
        agi.command('SET VARIABLE TONE_DETECT(0,,x) ""')
        return 'greeting_finished' if noise == 'NOISE' else 'quiet_audio'
    return None


def play(directory):
    agi = AGI()
    result = {'status': 'interrupted', 'message': 'Соединение или воспроизведение прервано.'}
    details = {'channel': agi.metadata.get('agi_channel'),
               'uniqueid': agi.metadata.get('agi_uniqueid'), 'trace': agi.trace}
    try:
        with wave.open(str(directory/'message.wav'), 'rb') as wav:
            frames = wav.getnframes()
        state, _, _ = agi.command('CHANNEL STATUS')
        if state != 6:
            raise EOFError('Channel is not answered')
        if agi.metadata.get('agi_channel', '').startswith('PJSIP/'):
            details['sip_call_id'] = agi.variable('CHANNEL(pjsip,call-id)')
        converter = Path(__file__).with_name('voice_recording.py').resolve()
        if any(not re.fullmatch(r'/[A-Za-z0-9_/.-]+', str(p)) for p in (directory, converter)):
            raise ValueError('Unsafe recording path')
        # MixMonitor's post-process command runs after its files are closed,
        # including remote hangups when AGI can no longer run StopMixMonitor.
        agi.command(f'EXEC MixMonitor "{directory}/conversation.raw,D,{converter} {directory}"')
        details['recording'] = 'conversation.wav'
        readiness = await_audio(agi)
        details['readiness'] = readiness
        if readiness is None:
            result = {'status': 'unknown', 'message':
                      'Шлюз сообщил об ответе, но готовность звукового канала не подтверждена. Запись не воспроизводилась.'}
        else:
            code, _, endpos = agi.command(f'STREAM FILE "{directory / "message"}" ""')
            playback = agi.variable('PLAYBACKSTATUS')
            details.update(expected_samples=frames, endpos=endpos, playback_status=playback)
            if code == 0 and playback == 'SUCCESS' and endpos is not None and endpos >= frames:
                result = {'status': 'completed', 'message':
                          'Запись полностью передана в отвеченный канал. Прослушивание получателем не подтверждается.'}
                # Record the recipient's reply for exactly this window, unless
                # they hang up first. A hangup here does not undo full playback.
                details['response_seconds'] = RESPONSE_SECONDS
                agi.command(f'EXEC Wait "{RESPONSE_SECONDS}"')
    except (EOFError, BrokenPipeError, OSError, ValueError, wave.Error) as exc:
        details['error'] = str(exc)
    finally:
        details['elapsed_seconds'] = round(time.monotonic()-agi.started, 3)
        result['diagnostics'] = details
        temporary = directory/'result.tmp'
        with temporary.open('w') as stream:
            json.dump(result, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory/'result.json')


if __name__ == '__main__':
    # Persist a result even when Asterisk sends SIGHUP on hangup.
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    path = Path(sys.argv[1]).resolve()
    if not re.fullmatch(r'[a-f0-9]{32}', path.name) or not (path/'job.json').is_file():
        sys.exit(1)
    play(path)
