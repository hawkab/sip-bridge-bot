import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

from integrations.asterisk.voice_playback import AGI, await_audio, play

MODULE = 'integrations.asterisk.voice_playback'


class AudioChannel:
    """Model elapsed time, received frames and tone hits from Asterisk's DSP."""
    def __init__(self, waits):
        self.metadata = {'agi_channel': 'PJSIP/test'}
        self.waits = iter(waits)
        self.now = 0
        self.hits = 0
        self.received = 0
        self.commands = []

    def command(self, text):
        self.commands.append(text)
        return 0, None, None

    def variable(self, name):
        return str(self.hits if name == 'TONE_DETECT(rx)' else self.received)

    def wait(self, app, milliseconds, seconds):
        expected, status, elapsed, hits, packets = next(self.waits)
        assert app == expected
        assert 0 < seconds <= 6
        self.now += elapsed
        self.hits += hits
        self.received += packets
        return status


class PlaybackTests(unittest.TestCase):
    def readiness(self, channel):
        with patch(MODULE+'.time.monotonic', side_effect=lambda: channel.now):
            return await_audio(channel)

    def test_two_ringback_bursts_then_greeting(self):
        channel = AudioChannel([
            ('WaitForNoise', 'NOISE', .2, 0, 10),
            ('WaitForSilence', 'SILENCE', 1.5, 1, 75),
            ('WaitForNoise', 'NOISE', 3.5, 0, 175),
            ('WaitForSilence', 'SILENCE', 1.5, 1, 75),
            ('WaitForNoise', 'NOISE', 3.5, 0, 175),
            ('WaitForSilence', 'SILENCE', 1.5, 0, 75),
        ])
        self.assertEqual(self.readiness(channel), 'greeting_finished')
        self.assertGreater(channel.now, 11)
        self.assertEqual(channel.hits, 2)

    def test_silent_answer_requires_inbound_rtp(self):
        channel = AudioChannel([
            ('WaitForNoise', 'TIMEOUT', 6, 0, 0),
            ('WaitForNoise', 'TIMEOUT', 6, 0, 300),
        ])
        self.assertEqual(self.readiness(channel), 'quiet_audio')
        self.assertEqual(channel.now, 12)

    def test_missing_rtp_stops_after_deadline(self):
        channel = AudioChannel([('WaitForNoise', 'TIMEOUT', 6, 0, 0)]*8)
        self.assertIsNone(self.readiness(channel))
        self.assertLessEqual(channel.now, 48)

    def test_ringback_never_turns_into_success_on_timeout(self):
        channel = AudioChannel([
            ('WaitForNoise', 'NOISE', .2, 0, 10),
            ('WaitForSilence', 'SILENCE', 4.8, 1, 240),
        ]*9)
        self.assertIsNone(self.readiness(channel))
        self.assertEqual(channel.now, 45)

    def test_unfinished_greeting_waits_for_silence(self):
        channel = AudioChannel([
            ('WaitForNoise', 'NOISE', .2, 0, 10),
            ('WaitForSilence', 'TIMEOUT', 5, 0, 250),
            ('WaitForNoise', 'NOISE', .2, 0, 10),
            ('WaitForSilence', 'SILENCE', 1, 0, 50),
        ])
        self.assertEqual(self.readiness(channel), 'greeting_finished')
        self.assertGreater(channel.now, 6)

    def playback(self, responses, readiness='greeting_finished'):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with wave.open(str(directory/'message.wav'), 'wb') as wav:
                wav.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
                wav.writeframes(b'\x01\x00'*8000)
            output = io.StringIO()
            with patch('sys.stdin', io.StringIO('agi_channel: test\nagi_uniqueid: test-id\n\n'+responses)), \
                    patch('sys.stdout', output), patch(MODULE+'.await_audio', return_value=readiness):
                play(directory)
            self.assertFalse((directory/'result.tmp').exists())
            return json.loads((directory/'result.json').read_text()), output.getvalue()

    def test_complete_requires_entire_file_and_playback_success(self):
        for response, status in [
            ('200 result=0 endpos=8000\n200 result=1 (SUCCESS)\n200 result=0\n', 'completed'),
            ('200 result=0 endpos=4000\n200 result=1 (SUCCESS)\n', 'interrupted'),
            ('200 result=0 endpos=8000\n200 result=1 (FAILED)\n', 'interrupted'),
            ('200 result=0\n200 result=1 (SUCCESS)\n', 'interrupted'),
            ('200 result=-1\n', 'interrupted'),
            ('', 'interrupted'),
        ]:
            with self.subTest(response=response):
                result, _ = self.playback('200 result=6\n'+response)
                self.assertEqual(result['status'], status)
                self.assertEqual(result['diagnostics']['uniqueid'], 'test-id')

    def test_no_ready_audio_means_no_stream(self):
        result, commands = self.playback('200 result=6\n', readiness=None)
        self.assertEqual(result['status'], 'unknown')
        self.assertNotIn('STREAM FILE', commands)

    def test_unanswered_channel_never_plays(self):
        result, commands = self.playback('200 result=5\n')
        self.assertEqual(result['status'], 'interrupted')
        self.assertNotIn('STREAM FILE', commands)

    def test_hangup_after_complete_does_not_erase_delivery(self):
        result, _ = self.playback('200 result=6\n200 result=0 endpos=8000\n200 result=1 (SUCCESS)\n200 result=-1\n')
        self.assertEqual(result['status'], 'completed')

    def test_missing_dsp_fails_instead_of_playing_blindly(self):
        with patch('sys.stdin', io.StringIO('\n200 result=1 ()\n')), patch('sys.stdout', io.StringIO()):
            with self.assertRaises(ValueError):
                int(AGI().variable('TONE_DETECT(rx)'))


if __name__ == '__main__':
    unittest.main()
