"""Quality checks share the call recognizer and never create call events or notifications."""
import asyncio
import logging
import tempfile
import time
from pathlib import Path
import httpx

logger = logging.getLogger(__name__)


class TranscriptionTestWorker:
    def __init__(self, client, transcriber):
        self.client = client
        self.transcriber = transcriber
        self.pending_result = None

    async def run_forever(self):
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception('Transcription quality queue temporarily unavailable')
            await asyncio.sleep(3)

    async def run_once(self):
        if self.pending_result is None:
            job = (await self.client.request('claim')).get('job')
            if not job:
                return
            result = {'id': job['id'], 'claim_token': job['claim_token']}
            try:
                audio = await self.client.download(job['id'])
                with tempfile.TemporaryDirectory(prefix='asr_quality_') as directory:
                    path = Path(directory) / 'input.audio'
                    path.write_bytes(audio)
                    start = time.monotonic()
                    output = await self.transcriber.transcribe_sample(path, job['backend'])
                    result.update(status='completed', **output, elapsed=round(time.monotonic()-start, 2))
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.exception('Transcription quality check failed')
                result.update(status='failed', message=str(error) if isinstance(error, ValueError)
                    else 'Не удалось распознать запись. Повторите проверку.')
            self.pending_result = result
        # Retain results across a lost completion response without running ASR again.
        try:
            await self.client.request('complete', **self.pending_result)
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 400:
                raise
            # The server may have expired the job during a long outage.
            logger.warning('Transcription quality result expired or rejected')
        self.pending_result = None
