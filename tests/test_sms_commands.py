import asyncio
import os
import unittest
from email.message import EmailMessage
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

for key in ('BOT_TOKEN','ADMIN_LOGIN','TG_HOST','TG_USER','TG_PASS'):
    os.environ.setdefault(key,'test')
from services.command_service import CommandService
from integrations.email.imap_reader import MailGateway
from integrations.telegram.handlers import cmd_sms, register_handlers
from workers.sms_outbox import SmsOutboxWorker
from integrations.tg200.client import SmsSendResult

class SmsCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_is_preserved_and_sender_can_be_number(self):
        outbox=SimpleNamespace(request=AsyncMock(return_value={'job':{'id':'1','sender':'+79991111111','number':'+79992222222','message':'queued'}}))
        service=CommandService(None,outbox)
        text='Он сказал: "Привет" + it\'s ok\nВторая строка'
        await service.execute('/sms@mybot +79991111111 +79992222222 '+text,source='telegram',request_key='telegram:12345:42')
        args=outbox.request.call_args
        self.assertEqual(args.kwargs['text'],text)
        self.assertEqual(args.kwargs['sender'],'+79991111111')
        self.assertEqual(args.kwargs['request_key'],'telegram:12345:42')
    async def test_telegram_checks_admin_and_passes_message_identity(self):
        service=SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(items=[],post_action=None)))
        delivery=SimpleNamespace(reply_telegram=AsyncMock())
        context=SimpleNamespace(bot_data={'command_service':service,'delivery':delivery})
        update=SimpleNamespace(effective_user=SimpleNamespace(username='someone_else'),effective_chat=SimpleNamespace(id=12345),effective_message=SimpleNamespace(message_id=42,text='/sms 1 +79992222222 test'))
        with patch('integrations.telegram.auth.CONFIG.ADMIN_LOGIN','admin'),patch('integrations.telegram.auth.set_admin_chat_id'):
            await cmd_sms(update,context);service.execute.assert_not_awaited()
            update.effective_user.username='admin';await cmd_sms(update,context)
        self.assertEqual(service.execute.call_args.kwargs['request_key'],'telegram:12345:42')
        self.assertEqual(service.execute.call_args.kwargs['source'],'telegram')
        app=SimpleNamespace(add_handler=lambda handler:handlers.append(handler));handlers=[]
        register_handlers(app)
        self.assertTrue(any('sms' in getattr(handler,'commands',[]) for handler in handlers))
    async def test_email_sender_hash_parser_and_dedup_key(self):
        config=SimpleNamespace(EMAIL_ALLOWED_SENDERS_SET={'admin@example.test'},EMAIL_COMMAND_HASH='secret')
        gateway=MailGateway(config,SimpleNamespace(reply_email=AsyncMock()),SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(post_action=None))))
        def fetch(sender='admin@example.test',body='secret\n/sms 2 +79991111111 Привет!'):
            mail=EmailMessage();mail['From']=sender;mail['Subject']='SMS test';mail.set_content(body)
            client=SimpleNamespace(uid=lambda *args:('OK',[(b'1',mail.as_bytes())]))
            return gateway._fetch_command(client,b'77')
        self.assertIsNone(fetch(sender='stranger@example.test'))
        self.assertIsNone(fetch(body='/sms 2 +79991111111 no secret here'.replace('secret','code')))
        parsed=fetch();self.assertEqual(parsed.command,'/sms 2 +79991111111 Привет!')
        self.assertEqual(parsed.request_key,fetch().request_key)
        await gateway._handle_command(parsed)
        self.assertEqual(gateway.command_service.execute.call_args.kwargs['source'],'email')
    async def test_persistence_failure_does_not_resend_sms(self):
        job={'id':'job','claim_token':'token','port':1,'sender':'+79991111111','number':'+79992222222','text':'test'}
        complete_attempts=0
        async def request(action,**payload):
            nonlocal complete_attempts
            if action=='claim':return {'job':job}
            if action=='complete':
                complete_attempts+=1
                if complete_attempts==1:raise RuntimeError('lost acknowledgment')
            return {'ok':True}
        client=SimpleNamespace(request=AsyncMock(side_effect=request))
        ready=asyncio.Event();ready.set()
        gateway=SimpleNamespace(ready=ready,send_sms=AsyncMock(return_value=SmsSendResult('sent','OK','123')))
        worker=SmsOutboxWorker(client,gateway,SimpleNamespace(SMS_SIM_PORTS=[{'port':1,'number':''}],SMS_SPAN_OFFSET=1),SimpleNamespace(notify_event=AsyncMock()))
        with self.assertRaises(RuntimeError):await worker.run_once()
        await worker.run_once()
        self.assertEqual(gateway.send_sms.await_count,1)
        self.assertIsNone(worker.pending_result)

if __name__=='__main__':unittest.main()
