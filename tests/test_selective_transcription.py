import math
import unittest
from copy import deepcopy
from dataclasses import replace

from integrations.transcription.selective import Policy, select_windows, propose_edits, apply_edits, confidence_words, correct


def interval(start=0,end=10,text='Это сложный термин здесь',channel='left',prob=.3):
    tokens=[' '+t for t in text.split()]
    return dict(start=int(start*16000),end=int(end*16000),text=text,channel=channel,
                tokens=tokens,logprobs=[math.log(prob)]*len(tokens))


def fixture(before='Мы подключили вайфай сегодня дома.',after='Мы подключили Wi-Fi сегодня дома.'):
    rows=[dict(channel='left',speaker='Я',text=before,start=1.,end=8.,start_hms='00:00:01.000',end_hms='00:00:08.000')]
    terms=after.split()
    donor=dict(words=[dict(text=t,t0_ms=(i+1)*1000,t1_ms=(i+1)*1000+400,first_token=i,n_tokens=1)for i,t in enumerate(terms)],tokens=[dict(p=.98)for t in terms])
    window=dict(channel='left',start=0,end=160000,seconds=10,flags=[dict(weak_words=[dict(text='вайфай')],term_matches=[])])
    return rows,window,donor


class SelectiveTests(unittest.TestCase):
    def test_silence_never_selects(self):
        windows,audit=select_windows([])
        self.assertEqual(windows,[]);self.assertEqual(audit['selected_seconds'],0)

    def test_budget_and_channels(self):
        source=[interval(i*25,i*25+10,channel=side,text='Мы подключили вайфай сегодня дома')for side in ['left','right']for i in range(30)]
        windows,audit=select_windows(source)
        self.assertLessEqual(audit['selected_seconds'],60)
        self.assertLessEqual(len(windows),8)
        self.assertTrue(all(w['end']-w['start']<=20*16000 for w in windows))
        self.assertEqual(select_windows(list(reversed(source))), (windows,audit))

    def test_short_context_and_high_confidence_skipped(self):
        self.assertFalse(select_windows([interval(end=3)])[0])
        self.assertFalse(select_windows([interval(prob=.99)])[0])

    def test_term_selects_even_when_confident(self):
        self.assertTrue(select_windows([interval(text='Мы подключили вайфай сегодня дома',prob=.99)])[0])

    def test_uncertain_ordinary_call_does_not_load_second_model(self):
        self.assertFalse(select_windows([interval(prob=.2)])[0])

    def test_invalid_confidence_ignored(self):
        row=interval();row['logprobs'][0]=float('nan')
        self.assertEqual(confidence_words(row),[])
        row['logprobs']=[]
        self.assertEqual(confidence_words(row),[])

    def test_punctuation_confidence_does_not_trigger(self):
        row=interval(text='Понятный нормальный текст',prob=.999)
        row['tokens'].append('.');row['logprobs'].append(-50)
        self.assertTrue(all(w['probability']>.99 for w in confidence_words(row)))

    def test_valid_term_changes_only_text_with_audit(self):
        rows,w,d=fixture();payload=dict(conversation=rows,channels={'left':dict(status='recognized')})
        original=deepcopy(payload);proposals=propose_edits(rows,w,d,[])
        self.assertTrue(proposals[0]['accepted'],proposals)
        result,edits=apply_edits(payload,proposals)
        self.assertEqual(payload,original)
        self.assertEqual(result['conversation'][0]['text'],'Мы подключили Wi-Fi сегодня дома.')
        self.assertEqual(result['conversation'][0]['start'],1.)
        self.assertEqual(result['conversation'][0]['end'],8.)
        self.assertEqual(len(edits),1)

    def test_numbers_protected(self):
        rows,w,d=fixture(before='Мы подключили вайфай 5 сегодня дома.')
        self.assertFalse(any(p['accepted'] for p in propose_edits(rows,w,d,[])))

    def test_foreign_hallucination_not_allowed(self):
        rows,w,d=fixture(after='We connected Wi-Fi today at home.')
        self.assertFalse(any(p['accepted'] for p in propose_edits(rows,w,d,[])))

    def test_splice_and_uncertain_donor_rejected(self):
        rows,w,d=fixture()
        self.assertFalse(any(p['accepted'] for p in propose_edits(rows,w,d,[3.1])))
        d['tokens'][2]['p']=.3
        self.assertFalse(any(p['accepted'] for p in propose_edits(rows,w,d,[])))

    def test_no_cross_channel_or_cross_row_replacement(self):
        rows,w,d=fixture();w['channel']='right'
        self.assertFalse(any(p['accepted'] for p in propose_edits(rows,w,d,[])))
        rows,w,d=fixture(before='Мы подключили вай фай сегодня дома.')
        rows=[dict(rows[0],text='Мы подключили вай',end=3),dict(rows[0],text='фай сегодня дома.',start=3.1)]
        self.assertFalse(any(p['accepted'] for p in propose_edits(rows,w,d,[])))

    def test_overlapping_edits_not_reapplied(self):
        rows,w,d=fixture();proposals=propose_edits(rows,w,d,[])
        result,edits=apply_edits({'conversation':rows},proposals+proposals)
        self.assertEqual(len(edits),1)
        self.assertEqual(result['conversation'][0]['text'].count('Wi-Fi'),1)

    def test_donor_failure_preserves_complete_base(self):
        rows,w,_=fixture();payload={'conversation':rows}
        def fail(window): raise RuntimeError('model unavailable')
        result,audit=correct(payload,[w],fail)
        self.assertEqual(result,payload)
        self.assertEqual(audit['accepted'],[])
        self.assertIn('model unavailable',audit['attempts'][0]['error'])

class PipelineTests(unittest.TestCase):
    def test_no_selection_never_starts_native_worker(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from integrations.transcription.parakeet import correct_selected
        payload = {'conversation': []}
        with patch('integrations.transcription.parakeet.recognize_batch', side_effect=AssertionError('unneeded worker')):
            result = correct_selected(payload, [], {}, SimpleNamespace())
        self.assertEqual(result['conversation'], [])
        self.assertEqual(result['correction']['status'], 'skipped')
        self.assertNotIn('correction', payload)

    def test_native_crash_and_timeout_keep_base(self):
        import subprocess
        from types import SimpleNamespace
        from unittest.mock import patch
        import numpy as np
        from integrations.transcription.parakeet import correct_selected
        rows, _, _ = fixture()
        payload = {'conversation': rows}
        for error in [subprocess.TimeoutExpired('worker', 180), subprocess.CalledProcessError(-11, 'worker')]:
            with self.subTest(error=type(error).__name__), patch('integrations.transcription.parakeet.recognize_batch', side_effect=error):
                result = correct_selected(payload, [interval(text='Мы подключили вайфай сегодня дома.')],
                    {'left': np.ones(160000, dtype=np.float32)}, SimpleNamespace())
            self.assertEqual(result['conversation'], payload['conversation'])
            self.assertEqual(result['correction']['status'], 'fallback')
            self.assertEqual(result['correction']['accepted'], [])

    def test_real_pipeline_applies_donor_and_preserves_timestamps(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        import numpy as np
        from integrations.transcription.parakeet import correct_selected
        rows, _, donor = fixture()
        payload = {'conversation': rows}
        with patch('integrations.transcription.parakeet.recognize_batch', return_value=[donor]) as recognize:
            result = correct_selected(payload, [interval(text='Мы подключили вайфай сегодня дома.')],
                {'left': np.ones(160000, dtype=np.float32)}, SimpleNamespace())
        self.assertEqual(len(recognize.call_args.args[0]), 1)
        self.assertEqual(result['conversation'][0]['text'], 'Мы подключили Wi-Fi сегодня дома.')
        self.assertEqual(result['conversation'][0]['start_hms'], rows[0]['start_hms'])
        self.assertEqual(result['correction']['status'], 'complete')
        self.assertEqual(result['correction']['accepted'][0]['base_text'], rows[0]['text'])


class CaptureTests(unittest.TestCase):
    def test_invalid_optional_confidence_keeps_first_pass(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        import numpy as np
        from integrations.transcription import gigaam
        model = Mock()
        model.recognize.return_value = SimpleNamespace(text='Мы подключили вайфай сегодня дома.',
            tokens=[' Мы', ' подключили', ' вайфай', ' сегодня', ' дома.'],
            timestamps=[.1, .5, 1., 2., 3.], logprobs=[None])
        options = dict(speaker='S', channel_name='left', vad_min_silence_ms=500,
            split_gap_seconds=.8, punctuation_gap_seconds=.35, max_phrase_seconds=0)
        capture = []
        with patch.object(gigaam, 'get_speech_timestamps', return_value=[{'start':0,'end':160000}]):
            audio = np.ones(160000, dtype=np.float32) * .01
            base = gigaam.transcribe_channel(audio, lambda:model, **options)
            result = gigaam.transcribe_channel(audio, lambda:model, capture=capture, **options)
        self.assertEqual(base, result)
        self.assertEqual(capture[0]['logprobs'], [])
        self.assertEqual(capture[0]['text'], model.recognize.return_value.text)


if __name__ == '__main__':
    unittest.main()
