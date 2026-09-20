import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from integrations.asterisk.sim_identity import sms_identity, call_identity
from integrations.asterisk.voice_calls import AsteriskVoiceCalls


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.config = SimpleNamespace(
            SMS_SIM_PORTS=[{'port':1, 'number':'+79990000001'}, {'port':2, 'number':'+79990000002'}],
            CALL_GSM_INBOUND_DIDS={'1001':1, '1002':2},
            VOICE_CALLS_ROUTES=[{'port':1,'endpoint':'gateway','prefix':'991','number':'+79990000001'},
                               {'port':2,'endpoint':'gateway','prefix':'992','number':'+79990000002'}])

    def test_received_sms_and_inbound_did(self):
        self.assertEqual(sms_identity(self.config, '2')['local_number'], '+79990000002')
        self.assertIsNone(sms_identity(self.config, '?')['sim_port'])
        self.assertEqual(call_identity(self.config, [{'dcontext':'inbound-gsm','dst':'1002'}])['sim_port'], 2)

    def test_ambiguous_endpoint_never_guesses_sim(self):
        row = {'dcontext':'inbound-gsm','dst':'s','channel':'PJSIP/gateway-abcd'}
        self.assertIsNone(call_identity(self.config, [row])['sim_port'])
        row = {'dcontext':'internal-calls','dstchannel':'PJSIP/gateway-abcd'}
        self.assertIsNone(call_identity(self.config, [row])['sim_port'])
        row['userfield'] = 'sim_port=2;other=value'
        self.assertEqual(call_identity(self.config, [row])['local_number'], '+79990000002')


class VoiceRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_selected_sim_routes_call_and_rejects_stale_identity(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root); (root/'outgoing').mkdir()
            config = SimpleNamespace(VOICE_CALLS_DIR=str(root/'jobs'),VOICE_CALLS_SPOOL=str(root/'outgoing'),
                VOICE_CALLS_ENDPOINT='gateway',ASTERISK_CLI='asterisk',TG_DEFAULT_SIM=1,
                VOICE_CALLS_ROUTES=[{'port':1,'endpoint':'gateway','prefix':'991','number':'+79990000001'},
                                   {'port':2,'endpoint':'gateway','prefix':'992','number':'+79990000002'}])
            calls = AsteriskVoiceCalls(config)
            job = {'id':'a'*32,'number':'+79991111111','port':2,'sender':'+79990000002'}
            with patch('integrations.asterisk.voice_calls.convert_audio', AsyncMock(return_value=2)):
                await calls.prepare_and_originate(job, b'fixture')
                text = next((root/'outgoing').glob('*.call')).read_text()
                self.assertIn('Channel: PJSIP/992+79991111111@gateway', text)
                self.assertIn('MaxRetries: 0', text)
                for changes in ({'port':3}, {'sender':'+79990000001'}):
                    with self.assertRaises(ValueError):
                        await calls.prepare_and_originate({**job, 'id':'b'*32, **changes}, b'fixture')
            self.assertEqual(len(list((root/'outgoing').glob('*.call'))), 1)
