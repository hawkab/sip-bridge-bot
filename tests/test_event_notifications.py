import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

for key in ('BOT_TOKEN', 'ADMIN_LOGIN', 'TG_HOST', 'TG_USER', 'TG_PASS'):
    os.environ.setdefault(key, 'test')

from integrations.tg200.client import SmsSendResult
from integrations.event_store.client import CallStoreResult
from services.delivery_service import DeliveryHub
from services.event_router import handle_cdr_group_notification, handle_sms_notification
from workers.sms_outbox import SmsOutboxWorker
from workers.voice_outbox import VoiceOutboxWorker


class EventNotificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = SimpleNamespace(SMS_SIM_PORTS=[
            {'port': 1, 'number': '+79990000001'},
            {'port': 2, 'number': '+79990000002'},
        ], CALL_GSM_INBOUND_DIDS={'1001': 1, '1002': 2})
        self.delivery = DeliveryHub(self.config)
        self.delivery._notify_telegram = AsyncMock()
        self.delivery._notify_email = AsyncMock()
        self.store = SimpleNamespace(config=self.config, save_sms=AsyncMock(return_value='https://example.test/sms'))
        self.store.save_call = AsyncMock(return_value=CallStoreResult(ok=True, view_url='https://example.test/call'))

    async def test_call_notifications_use_same_sim_as_storage_and_readable_dates_in_both_channels(self):
        row = {'src': '+79991111111', 'dcontext': 'inbound-gsm',
               'start': '2026-09-29 08:19:12', 'answer': '2026-09-29 08:19:13',
               'end': '2026-09-29 08:19:26', 'duration': '14', 'billsec': '13', 'disposition': 'ANSWERED'}
        for port in (1, 2):
            with self.subTest(port=port):
                recipient = self.config.SMS_SIM_PORTS[port - 1]['number']
                await handle_cdr_group_notification(self.delivery, self.store, None, None, [{**row, 'dst': f'100{port}'}])
                for text in (self.delivery._notify_telegram.call_args.args[0], self.delivery._notify_email.call_args.args[1]):
                    self.assertIn('От: `+79991111111`\n', text)
                    self.assertIn(f'Кому: `{recipient}` (`100{port}`)', text)
                    self.assertIn('Начало: `08:19:12 29.09.2026`', text)
                    self.assertIn('Ответ: `08:19:13 29.09.2026`', text)
                    self.assertIn('Конец: `08:19:26 29.09.2026`', text)
                self.assertEqual(self.store.save_call.call_args.kwargs['local_number'], recipient)
                self.assertEqual(self.store.save_call.call_args.kwargs['timestamp'], row['start'])

    async def test_call_group_keeps_dates_across_midnight(self):
        row = {'src': '+79991111111', 'dst': '1002', 'dcontext': 'inbound-gsm',
               'start': '2026-09-28 23:59:58', 'end': '2026-09-29 00:00:12',
               'duration': '14', 'billsec': '0', 'disposition': 'NO ANSWER'}
        await handle_cdr_group_notification(self.delivery, self.store, None, None,
            [row, {**row, 'start': '2026-09-29 00:00:15', 'end': '2026-09-29 00:00:29'}])
        for text in (self.delivery._notify_telegram.call_args.args[0], self.delivery._notify_email.call_args.args[1]):
            self.assertIn('Кому: `+79990000002` (`1002`)', text)
            self.assertIn('23:59:58 28.09.2026 - 00:00:12 29.09.2026', text)
            self.assertIn('00:00:15 29.09.2026 - 00:00:29 29.09.2026', text)

    async def test_outgoing_calls_show_actual_sim_at_sender_and_keep_remote_recipient(self):
        base = {'src': '1001', 'start': '2026-09-29 08:19:12', 'duration': '14', 'disposition': 'NO ANSWER'}
        cases = [({'dcontext': 'inbound-gsm', 'dst': 's'}, '`1001`'),
                 ({'dcontext': 'internal-calls', 'dst': '+79991111111'}, '`1001`')]
        for port in (1, 2):
            cases.append(({'dcontext': 'internal-calls', 'dst': '+79991111111', 'userfield': f'sim_port={port}'},
                          f"`{self.config.SMS_SIM_PORTS[port - 1]['number']}` (`1001`)"))
        for fields, sender in cases:
            for attempts in (1, 2):
                with self.subTest(fields=fields, attempts=attempts):
                    await handle_cdr_group_notification(self.delivery, self.store, None, None, [{**base, **fields}] * attempts)
                    for text in (self.delivery._notify_telegram.call_args.args[0], self.delivery._notify_email.call_args.args[1]):
                        self.assertIn(f'От: {sender}\n', text)
                        self.assertIn(f"Кому: `{fields['dst']}`\n", text)

    async def test_incoming_sms_uses_receiving_number_in_notification_and_storage(self):
        for port in (1, 2):
            with self.subTest(port=port):
                self.delivery._notify_telegram.reset_mock()
                recipient = self.config.SMS_SIM_PORTS[port - 1]['number']
                await handle_sms_notification(self.delivery, self.store, '+79991111111', str(port), '2026-09-20 10:00:00', 'Привет!')
                self.delivery._notify_telegram.assert_awaited_once()
                text = self.delivery._notify_telegram.call_args.args[0]
                self.assertIn('От: `+79991111111`', text)
                self.assertIn(f'Кому: `{recipient}`', text)
                self.assertNotIn('\nSIM:', text)
                self.assertTrue(text.endswith('\n\nПривет!'))
                self.assertEqual(self.store.save_sms.call_args.kwargs['local_number'], recipient)
                self.assertEqual(self.store.save_sms.call_args.kwargs['sim_port'], port)
                self.assertIn(f'Кому: `{recipient}`', self.delivery._notify_email.call_args.args[1])

    async def test_unknown_sim_does_not_guess_recipient(self):
        await handle_sms_notification(self.delivery, self.store, '+79991111111', '3', '2026-09-20', 'Сообщение')
        text = self.delivery._notify_telegram.call_args.args[0]
        self.assertIn('Кому: `номер не определён (SIM 3)`', text)
        self.assertEqual(self.store.save_sms.call_args.kwargs['local_number'], '')

    async def test_outgoing_results_are_saved_without_telegram_or_email_notifications(self):
        api = SimpleNamespace(request=AsyncMock(return_value={'has_due': False}))
        sms = SmsOutboxWorker(api, None, self.config, self.delivery)
        sms.pending_result = ({'id': 'sms', 'claim_token': 'token', 'sender': '+79990000001',
                               'number': '+79991111111', 'port': 1}, SmsSendResult('sent', 'Отправлено'))
        await sms._complete()
        self.assertIsNone(sms.pending_result)
        self.assertEqual(api.request.call_args.kwargs['status'], 'sent')
        for status in ('completed', 'failed'):
            result = {'status': status, 'message': 'Результат вызова'}
            job = {'id': status, 'claim_token': 'token', 'number': '+79991111111', 'result': result}
            calls = SimpleNamespace(journals=lambda: [job], save=Mock())
            await VoiceOutboxWorker(api, calls, self.delivery).run_once()
            self.assertEqual(api.request.call_args.kwargs['status'], status)
            self.assertTrue(calls.save.call_args.args[0]['reported'])
        self.delivery._notify_telegram.assert_not_awaited()
        self.delivery._notify_email.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
