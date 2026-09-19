"""Asterisk call files, with a durable local journal and no automatic redial."""
import array
import asyncio
import json
import os
from pathlib import Path
import re
import time
import wave

MAX_SECONDS = 120


def atomic_json(path, data):
    temp = path.with_suffix('.tmp')
    with temp.open('w') as stream:
        json.dump(data, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


async def convert_audio(audio, destination):
    if not 32 <= len(audio) <= 2097152:
        raise ValueError('Запись должна быть до 2 МБ.')
    source = destination.with_suffix('.source')
    source.write_bytes(audio)
    try:
        process = await asyncio.create_subprocess_exec('ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
            '-protocol_whitelist', 'file,pipe', '-format_whitelist', 'ogg,matroska,webm,mov,mp3,wav,flac,aac',
            '-i', str(source), '-map', '0:a:0', '-vn', '-t', str(MAX_SECONDS+1), '-ac', '1', '-ar', '8000',
            '-af', 'highpass=f=80,lowpass=f=3400', '-c:a', 'pcm_s16le', str(destination),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), 45)
        except BaseException:
            process.kill()
            await process.wait()
            raise
        if process.returncode:
            raise ValueError('Не удалось прочитать аудио. Поддерживаются OGG, WebM, MP3, M4A и WAV.')
        with wave.open(str(destination), 'rb') as wav:
            seconds = wav.getnframes()/wav.getframerate()
            samples = array.array('h', wav.readframes(wav.getnframes()))
        if not 0.3 <= seconds <= MAX_SECONDS:
            raise ValueError('Длительность записи должна быть от 0,3 до 120 секунд.')
        if not samples or max(abs(x) for x in samples) < 16:
            raise ValueError('В записи тишина. Запишите голос ещё раз.')
        return seconds
    finally:
        source.unlink(missing_ok=True)


class AsteriskVoiceCalls:
    def __init__(self, config):
        self.root = Path(config.VOICE_CALLS_DIR)
        self.outgoing = Path(config.VOICE_CALLS_SPOOL)
        self.endpoint = config.VOICE_CALLS_ENDPOINT
        self.agi = Path(__file__).with_name('voice_playback.py').resolve()
        self.cli = config.ASTERISK_CLI
        if not re.fullmatch(r'[A-Za-z0-9_-]+', self.endpoint):
            raise ValueError('Invalid VOICE_CALLS_ENDPOINT')
        for path in (self.root, self.agi):
            if not re.fullmatch(r'/[A-Za-z0-9_/.-]+', str(path)):
                raise ValueError('Voice call paths must not contain spaces or dialplan special characters')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    async def ready(self):
        if not self.outgoing.is_dir() or not os.access(self.outgoing, os.W_OK):
            return False
        process = await asyncio.create_subprocess_exec(self.cli, '-rx', 'core show channels count',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            output, _ = await asyncio.wait_for(process.communicate(), 5)
        except asyncio.TimeoutError:
            process.kill(); await process.wait(); return False
        # Leave normal phone calls priority; don't occupy the gateway while it is in use.
        return process.returncode == 0 and b'0 active channels' in output

    def journals(self):
        return [json.loads(p.read_text()) for p in sorted(self.root.glob('*/job.json'))]

    def save(self, job):
        directory = self.root/job['id']
        directory.mkdir(mode=0o700, exist_ok=True)
        atomic_json(directory/'job.json', job)

    async def prepare_and_originate(self, job, audio):
        if not re.fullmatch(r'[a-f0-9]{32}', job['id']) or not re.fullmatch(r'\+[1-9][0-9]{6,14}', job['number']):
            raise ValueError('Некорректный номер или идентификатор вызова.')
        directory = self.root/job['id']
        if (directory/'job.json').exists():
            raise RuntimeError('Call already recorded in the local journal')
        directory.mkdir(mode=0o700, exist_ok=True)
        duration = await convert_audio(audio, directory/'message.wav')
        # Persist the intent BEFORE publishing the call. A crash in this small gap
        # may miss a call, but can never cause an unsolicited second call.
        job = {**job, 'started_at': time.time(), 'duration': duration, 'reported': False}
        self.save(job)
        filename = f"sip-voice-{job['id']}.call"
        staging = self.outgoing.parent/'sip-voice-staging'
        staging.mkdir(mode=0o700, exist_ok=True)
        pending = staging/filename
        content = (f"Channel: PJSIP/{job['number']}@{self.endpoint}\nMaxRetries: 0\nWaitTime: 45\n"
            f"Application: AGI\nData: {self.agi},{directory}\nArchive: yes\n"
            f"Account: voice-{job['id']}\n")
        with pending.open('x') as stream:
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        os.link(pending, self.outgoing/filename)
        pending.unlink()
        return job

    def result(self, job):
        result = self.root/job['id']/'result.json'
        if result.exists():
            return json.loads(result.read_text())
        archive = self.outgoing.parent/'outgoing_done'/f"sip-voice-{job['id']}.call"
        if archive.exists():
            status = re.search(r'^Status: (\w+)', archive.read_text(), re.MULTILINE)
            if status and status[1] != 'Completed':
                return {'status': 'failed', 'message': 'Абонент не ответил, занят или GSM-сеть отклонила вызов.'}
        if time.time()-job['started_at'] > 240:
            return {'status': 'unknown', 'message': 'Нет подтверждения воспроизведения. Автоповтора не будет.'}
        return None
