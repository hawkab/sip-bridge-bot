import asyncio
import unittest
from integrations.tg200.client import YeastarSMSClient

class GatewayTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.commands=[]
        async def handler(reader, writer):
            writer.write(b'Asterisk Call Manager/1.1\r\n')
            try:
                while True:
                    raw=(await reader.readuntil(b'\r\n\r\n')).decode()
                    if 'Action: Login' in raw:
                        writer.write(b'Response: Success\r\nMessage: Authentication accepted\r\n\r\n')
                    else:
                        fields=dict(line.split(': ',1) for line in raw.strip().split('\r\n'))
                        cmd=fields['command'];self.commands.append(cmd)
                        writer.write(f'Response: Follows\r\nActionID: {fields["ActionID"]}\r\nOK\n--END COMMAND--\r\n\r\n'.encode())
                        if cmd.startswith('gsm send sms'):
                            identifier=cmd.rsplit(' ',1)[-1]
                            writer.write(f'Event: UpdateSMSSend\r\nID: {identifier}\r\nStatus: 1\r\n\r\n'.encode())
                    await writer.drain()
            except (asyncio.IncompleteReadError,ConnectionError):pass
            finally:writer.close()
        self.server=await asyncio.start_server(handler,'127.0.0.1',0)
        self.client=YeastarSMSClient('127.0.0.1',self.server.sockets[0].getsockname()[1],'u','p')
        self.task=asyncio.create_task(self.client.connect_forever())
        await asyncio.wait_for(self.client.ready.wait(),2)
    async def asyncTearDown(self):
        self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)
        self.server.close();await self.server.wait_closed()
    async def test_one_unicode_send_with_correlated_status(self):
        result=await self.client.send_sms('+79991234567','Тест + "слово"\nВторая строка',1)
        self.assertEqual(result.status,'sent');self.assertEqual(len(self.commands),1)
        self.assertTrue(self.commands[0].startswith('gsm send sms 2 +79991234567 "%D0'))
        self.assertIn('%0A',self.commands[0]);self.assertIn('%22',self.commands[0]);self.assertIn('%2B',self.commands[0])
    async def test_command_responses_and_incoming_multipart(self):
        replies=await asyncio.gather(self.client.send_command('gsm show spans'), self.client.send_command('gsm show 2'))
        self.assertTrue(all(r['Response']=='Follows' for r in replies))
        received=[];self.client.on_sms=lambda *args:received.append(args)
        for idx,text in [(2,'world'),(1,'hello%20')]:
            self.client._handle_block(f'Event: ReceivedSMS\r\nID: abc\r\nSender: 123\r\nGsmPort: 2\r\nIndex: {idx}\r\nTotal: 2\r\nContent: {text}')
        self.assertEqual(received[0][-1],'hello world')
    async def test_current_firmware_span_and_zero_based_multipart(self):
        received=[];self.client.on_sms=lambda *args:received.append(args)
        for idx,text in [(1,'world'),(0,'hello%20')]:
            self.client._handle_block(f'Event: ReceivedSMS\r\nID: zero\r\nSender: 123\r\nGsmSpan: 3\r\nIndex: {idx}\r\nTotal: 2\r\nContent: {text}')
        self.assertEqual(received[0][1],'2')
        self.assertEqual(received[0][-1],'hello world')

    async def test_missing_status_is_unknown_without_resend(self):
        original=self.client._handle_block
        self.client._handle_block=lambda block: None if 'UpdateSMSSend' in block else original(block)
        result=await self.client.send_sms('+79991234567','test',1,status_timeout=0.01)
        self.assertEqual(result.status,'unknown');self.assertEqual(len(self.commands),1)

    async def test_invalid_input_never_reaches_gateway(self):
        for number,text,port in [('+7\r\nAction: Login','x',1),('+79991234567','',1),('+79991234567','я'*513,1),('+79991234567','text',0)]:
            with self.assertRaises(ValueError):await self.client.send_sms(number,text,port)
        self.assertEqual(self.commands,[])

if __name__=='__main__':unittest.main()
