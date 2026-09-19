import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from integrations.transcription import gigaam
from integrations.transcription.stereo import StereoCallTranscriber


def transcriber(workers=2):
    return StereoCallTranscriber(SimpleNamespace(
        CALL_TRANSCRIBE_BACKEND='gigaam', CALL_TRANSCRIBE_CHANNEL_WORKERS=workers,
        CALL_TRANSCRIBE_GIGAAM_MODEL_PATH='', CALL_TRANSCRIBE_CPU_THREADS=1,
    ))


class ChannelParallelismTests(unittest.TestCase):
    def test_parallel_channels_overlap_and_keep_input_order(self):
        barrier = threading.Barrier(2, timeout=5)
        right_finished = threading.Event()

        def recognize(channel):
            barrier.wait()
            if channel == 'left':
                self.assertTrue(right_finished.wait(5))
            else:
                right_finished.set()
            return channel

        self.assertEqual(transcriber()._run_channels(recognize, ['left', 'right']), ['left', 'right'])

    def test_error_waits_for_other_channel_before_releasing_recording(self):
        barrier = threading.Barrier(2, timeout=5)
        release_right = threading.Event()
        right_finished = threading.Event()
        left_failed = threading.Event()

        def recognize(channel):
            barrier.wait()
            if channel == 'left':
                left_failed.set()
                raise ValueError('decoder failure')
            release_right.wait(5)
            right_finished.set()

        # Model switching/tempfile cleanup must not race a surviving channel.
        with ThreadPoolExecutor(max_workers=1) as outer:
            job = outer.submit(transcriber()._run_channels, recognize, ['left', 'right'])
            try:
                self.assertTrue(left_failed.wait(5))
                with self.assertRaises(TimeoutError):
                    job.result(timeout=.05)
            finally:
                release_right.set()
            with self.assertRaisesRegex(ValueError, 'decoder failure'):
                job.result(timeout=5)
        self.assertTrue(right_finished.is_set())

    def test_concurrent_model_load_is_single_and_shared(self):
        t = transcriber()
        barrier = threading.Barrier(2, timeout=5)

        def load_for_channel(_):
            barrier.wait()
            return t._get_model()

        with patch.object(gigaam, 'load_model', return_value=object()) as load:
            left, right = t._run_channels(load_for_channel, ['left', 'right'])
        self.assertIs(left, right)
        load.assert_called_once_with('', 1)

    def test_sequential_mode_and_invalid_worker_count(self):
        seen = []
        self.assertEqual(transcriber(1)._run_channels(lambda x: seen.append(x) or x, ['left', 'right']), seen)
        with self.assertRaisesRegex(ValueError, '1 or 2'):
            transcriber(3)

    def test_engine_specific_workers_follow_backend_selection(self):
        cfg = SimpleNamespace(CALL_TRANSCRIBE_BACKEND='gigaam',
            CALL_TRANSCRIBE_CHANNEL_WORKERS=1, CALL_TRANSCRIBE_GIGAAM_CHANNEL_WORKERS=2)
        t = StereoCallTranscriber(cfg)
        barrier = threading.Barrier(2, timeout=5)

        def recognize(channel):
            barrier.wait()
            return channel

        self.assertEqual(t._run_channels(recognize, ['left', 'right']), ['left', 'right'])
        t._backend = 'whisper'
        with patch('integrations.transcription.stereo.ThreadPoolExecutor', side_effect=AssertionError('Unexpected parallel Whisper')):
            self.assertEqual(t._run_channels(str, ['left', 'right']), ['left', 'right'])


if __name__ == '__main__':
    unittest.main()
