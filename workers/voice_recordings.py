"""Archive voice-call replies independently of the outgoing-call scheduler."""
import asyncio
from datetime import datetime, timezone
import json
import logging
import re
import time
import wave

from integrations.asterisk.voice_calls import atomic_json
from services.event_router import _apply_call_speaker_aliases, _append_event_link
from services.formatters.email_html import render_email_html
from services.formatters.transcription import format_transcription

logger = logging.getLogger(__name__)


class VoiceRecordingWorker:
    def __init__(self, calls, store, transcriber, pdf_renderer, delivery):
        self.calls, self.store, self.transcriber = calls, store, transcriber
        self.pdf_renderer, self.delivery = pdf_renderer, delivery

    async def run_forever(self):
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception('Voice recording archive temporarily unavailable')
            await asyncio.sleep(5)

    async def run_once(self):
        for job in self.calls.journals():
            if not re.fullmatch(r'[a-f0-9]{32}', job['id']):
                continue
            directory = self.calls.root/job['id']
            recording = directory/'conversation.wav'
            # The converter publishes WAV atomically after MixMonitor closes.
            if not recording.is_file() or not (directory/'result.json').is_file():
                continue
            journal = directory/'archive.json'
            state = json.loads(journal.read_text()) if journal.exists() else {}
            if state.get('done') or state.get('next_attempt_at', 0) > time.time():
                continue
            state['next_attempt_at'] = time.time()+60
            atomic_json(journal, state)
            try:
                state.pop('error', None)
                await self.archive(job, recording, state)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                state['error'] = str(error)
                logger.exception('Could not archive voice call %s', job['id'])
            finally:
                atomic_json(journal, state)

    async def archive(self, job, recording, state):
        with wave.open(str(recording), 'rb') as wav:
            duration = round(wav.getnframes()/wav.getframerate())
        timestamp = datetime.fromtimestamp(job['started_at'], timezone.utc).isoformat()
        rows = [{'dcontext': 'outbound-voice', 'dst': job['number'], 'start': timestamp,
                 'disposition': 'ANSWERED', 'billsec': duration}]
        cache = recording.with_suffix('.transcription.json')
        payload = json.loads(cache.read_text()) if cache.exists() else None
        transcribe_enabled = self.transcriber is not None and self.transcriber.is_enabled()
        if payload is None and transcribe_enabled:
            payload = await self.transcriber.transcribe_recording(str(recording))
            if payload is not None:
                atomic_json(cache, payload)
        payload = _apply_call_speaker_aliases(rows, payload)
        transcription_ready = not transcribe_enabled or payload is not None
        saved = await self.store.save_call(
            call_type='исходящий', timestamp=timestamp, number=job['number'], duration=duration,
            local_number=job.get('sender', ''), sim_port=job.get('port'),
            recording_path=str(recording), recording_name=f"voice-{job['id']}.wav",
            transcription=(payload or {}).get('conversation'),
            transcription_channels=(payload or {}).get('channels'), source_id='voice:'+job['id'],
        )
        state['saved'] = saved.ok
        if saved.ok and saved.view_url:
            state['view_url'] = saved.view_url
        if not saved.ok:
            state['error'] = saved.error_message or 'Call storage failed'
        # Save audio even if ASR fails; retry recognition and update the same card.
        # Successful recognition is cached, so a hosting retry won't run ASR again.
        if transcription_ready and not state.get('notified'):
            text = (f"📞 Голосовой вызов\nОт: {job.get('sender') or 'номер не определён'}\n"
                    f"Кому: {job['number']}\nЗапись разговора: {duration} сек.")
            transcript = format_transcription((payload or {}).get('conversation'))
            email = text+'\n\nТранскрибация:\n'+(transcript or 'Речь не распознана.')
            email = _append_event_link(email, state.get('view_url'), 'Карточка звонка')
            pdf, pdf_name = (None, None)
            if self.pdf_renderer is not None and payload:
                pdf, pdf_name = self.pdf_renderer.render_for_recording(str(recording), payload.get('conversation'))
            await self.delivery.notify_event(
                subject=f"SipBridgeBot: запись голосового вызова {job['number']}",
                text=text, attachment_path=str(recording), attachment_name=f"voice-{job['id']}.wav",
                email_text=email, email_html=render_email_html(email),
                telegram_bundle_attachment_path=pdf, telegram_bundle_attachment_name=pdf_name,
                telegram_followup_text=None if pdf else (transcript or 'Речь не распознана.'),
            )
            state['notified'] = True
        state['done'] = bool(saved.ok and transcription_ready and state.get('notified'))
