import asyncio
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import wave

for key in ('BOT_TOKEN','ADMIN_LOGIN','TG_HOST','TG_USER','TG_PASS'):
    os.environ.setdefault(key,'test')
from integrations.asterisk.voice_calls import AsteriskVoiceCalls, convert_audio
from integrations.telegram.voice_calls import parse_destination, voicecall, receive_voice, confirm_voice
from workers.voice_outbox import VoiceOutboxWorker


class VoiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_audio_conversion_rejects_silence_and_long_recording(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            def pcm(seconds, silent=False):
                import math, struct
                out=io.BytesIO()
                with wave.open(out,'wb') as w:
                    w.setparams((1,2,8000,0,'NONE','not compressed'))
                    w.writeframes(b''.join(struct.pack('<h',0 if silent else int(5000*math.sin(i*0.2))) for i in range(int(seconds*8000))))
                return out.getvalue()
            self.assertAlmostEqual(await convert_audio(pcm(1),root/'message.wav'),1)
            with self.assertRaisesRegex(ValueError,'тишина'): await convert_audio(pcm(1,True),root/'message.wav')
            with self.assertRaisesRegex(ValueError,'120'): await convert_audio(pcm(120.2),root/'message.wav')
            with self.assertRaises(ValueError): await convert_audio(b'x'*100,root/'message.wav')

    async def test_callfile_published_once_and_journal_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'outgoing').mkdir()
            config=SimpleNamespace(VOICE_CALLS_DIR=str(root/'jobs'),VOICE_CALLS_SPOOL=str(root/'outgoing'),VOICE_CALLS_ENDPOINT='gsm-port1',ASTERISK_CLI='asterisk')
            calls=AsteriskVoiceCalls(config)
            job={'id':'a'*32,'number':'+79991111111','claim_token':'secret'}
            with patch('integrations.asterisk.voice_calls.convert_audio',AsyncMock(return_value=2)):
                await calls.prepare_and_originate(job,b'audio')
                with self.assertRaises(RuntimeError): await calls.prepare_and_originate(job,b'audio')
            self.assertEqual(len(list((root/'outgoing').glob('*.call'))),1)
            text=next((root/'outgoing').glob('*')).read_text()
            self.assertIn('MaxRetries: 0',text); self.assertIn('Channel: PJSIP/+79991111111@gsm-port1',text)
            restarted=AsteriskVoiceCalls(config)
            saved=restarted.journals()[0]
            self.assertIsNone(restarted.result(saved))
            (root/'jobs'/job['id']/'result.json').write_text(json.dumps({'status':'completed','message':'OK'}))
            api=SimpleNamespace(request=AsyncMock(return_value={}))
            worker=VoiceOutboxWorker(api,restarted,SimpleNamespace(notify_event=AsyncMock()))
            with patch.object(restarted,'ready',AsyncMock(return_value=False)):
                await worker.run_once(); await worker.run_once()
            self.assertEqual(sum(call.args==('complete',) for call in api.request.call_args_list),1)
            self.assertTrue(restarted.journals()[0]['reported'])

    def test_moscow_time_and_validation(self):
        with patch('time.time',return_value=1790000000):
            number, when=parse_destination(['+79991111111','2026-09-25','12:30'])
            self.assertEqual(when,'2026-09-25T09:30:00+00:00')
        for args in [[],['+7999;System'],['+79991111111','2020-01-01','12:30'],['+79991111111','2026-99-99','12:30']]:
            with self.assertRaises(ValueError): parse_destination(args)

    async def test_telegram_voice_requires_admin_and_confirmation(self):
        from bootstrap.config import CONFIG
        api=SimpleNamespace(enabled=True,request=AsyncMock(return_value={'job':{'id':'b'*32,'number':'+79991111111','scheduled_at':'2026-09-20T10:00:00Z','message':'Queued'}}))
        message=SimpleNamespace(reply_text=AsyncMock(),voice=SimpleNamespace(file_id='file',file_size=100,duration=2),audio=None)
        update=SimpleNamespace(effective_user=SimpleNamespace(username='stranger'),effective_chat=SimpleNamespace(id=1),effective_message=message)
        context=SimpleNamespace(args=['+79991111111'],user_data={},bot_data={'command_service':SimpleNamespace(voice_outbox=api)},bot=SimpleNamespace(get_file=AsyncMock(return_value=SimpleNamespace(file_size=100,download_as_bytearray=AsyncMock(return_value=b'x'*100)))))
        with patch.object(CONFIG,'ADMIN_LOGIN','admin'),patch('integrations.telegram.auth.set_admin_chat_id'):
            await voicecall(update,context); self.assertEqual(context.user_data,{})
            update.effective_user.username='admin'
            await voicecall(update,context); await receive_voice(update,context)
            api.request.assert_not_awaited()
            nonce=context.user_data['voice_call']['nonce']
            query=SimpleNamespace(answer=AsyncMock(),edit_message_text=AsyncMock(),data='voice:send:'+nonce)
            update.callback_query=query
            await confirm_voice(update,context)
            await confirm_voice(update,context)
            self.assertEqual(api.request.await_count,1)
            self.assertEqual(api.request.call_args.kwargs['audio'],b'x'*100)

if __name__=='__main__': unittest.main()
