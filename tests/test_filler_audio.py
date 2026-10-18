import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent_video import filler_audio as audio
from agent_video import filler_rules
from agent_video.pipeline.run import _restore_s4_cleaning


class FillerAudioTest(unittest.TestCase):
    def setUp(self):
        self.source = {'id': 1, 'start': 0., 'end': 3., 'text': '所以我个人很爱这件的',
                       'members': [1]}
        self.words = [{'s': 0., 'e': .5, 'w': '所以'}, {'s': .5, 'e': .8, 'w': '我'},
                      {'s': .8, 'e': 3., 'w': '个人很爱这件的'}]
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        rules = filler_rules.save_rule(Path(temporary.name) / 'rules.json', {'pinyin': 'suo yi'})
        self.report = filler_rules.clean_selection([self.source], self.words, rules)
        self.extract = patch.object(audio, 'extract_audio')
        self.extract.start(); self.addCleanup(self.extract.stop)

    def refine(self, responses):
        recognize = Mock(side_effect=responses)
        result = audio.refine_cleanup(self.report, self.words, 'source.mp4', recognize=recognize)
        return result, recognize

    def test_clean_audio_keeps_existing_cut_and_snapshot(self):
        original = copy.deepcopy(self.report)
        report, recognize = self.refine(['我个人很爱这件的'])
        self.assertEqual(report['cleaned_candidates'][0]['start'], .5)
        self.assertEqual(report['rows'][0]['audio_verification']['status'], 'verified')
        self.assertEqual(_restore_s4_cleaning({'filler_cleanup': report}, [self.source]),
                         report['cleaned_candidates'])
        self.assertEqual(self.report, original)
        self.assertEqual(recognize.call_count, 1)

    def test_residual_syllable_uses_smallest_passing_adjustment(self):
        report, recognize = self.refine(['以我个人很爱这件的', '以我个人很爱这件的', '我个人很爱这件的'])
        row = report['rows'][0]
        self.assertEqual(row['retained_parts'][0]['start'], .54)
        self.assertEqual(row['removed_spans'][0]['end'], .54)
        self.assertEqual(report['total_seconds'], 2.46)
        self.assertEqual(row['audio_verification']['status'], 'adjusted')
        self.assertEqual(recognize.call_count, 3)

    def test_missing_or_interior_words_keep_entire_original_sentence(self):
        for response in ['个人很爱这件的', '我个人爱这件的', '', '我个人不爱这件的']:
            with self.subTest(response=response):
                report, recognize = self.refine([response])
                self.assertEqual(report['cleaned_candidates'], [self.source])
                self.assertEqual(report['rows'][0]['status'], 'skipped')
                self.assertNotIn('retained_parts', report['rows'][0])
                self.assertEqual(recognize.call_count, 1)

    def test_rejected_probes_never_replace_body_and_search_is_bounded(self):
        report, recognize = self.refine(['以我个人很爱这件的'] + ['个人很爱这件的'] * 20)
        self.assertEqual(report['cleaned_candidates'], [self.source])
        self.assertLessEqual(recognize.call_count, 11)
        self.assertGreater(recognize.call_count, 1)
        checks = report['rows'][0]['audio_verification']['failed_checks']
        self.assertLessEqual(max(c['start'] for c in checks), .7)

    def test_new_noise_at_untouched_edge_cannot_certify_adjustment(self):
        report, _ = self.refine(['以我个人很爱这件的', '我个人很爱这件的啊', '我个人很爱这件的'])
        self.assertEqual(report['cleaned_candidates'][0]['start'], .54)

    def test_short_edge_word_is_protected(self):
        self.words[1]['e'] = .6
        report, recognize = self.refine(['以我个人很爱这件的'])
        self.assertEqual(report['rows'][0]['status'], 'skipped')
        self.assertEqual(recognize.call_count, 1)

    def test_tail_and_middle_deletions_verify_every_remaining_part(self):
        source = {'id': 2, 'start': 0., 'end': 3., 'text': '好看所以显瘦所以'}
        words = [{'s': 0., 'e': .8, 'w': '好看'}, {'s': .8, 'e': 1.2, 'w': '所以'},
                 {'s': 1.2, 'e': 2.6, 'w': '显瘦'}, {'s': 2.6, 'e': 3., 'w': '所以'}]
        report = filler_rules.clean_selection([source], words, self.report['rules'])
        recognize = Mock(side_effect=['好看所', '好看', '以显瘦所', '显瘦所', '显瘦'])
        result = audio.refine_cleanup(report, words, 'source.mp4', recognize=recognize)
        self.assertEqual([(c['start'], c['end']) for c in result['cleaned_candidates']],
                         [(0., .78), (1.22, 2.58)])
        self.assertEqual(len(result['rows'][0]['audio_verification']['checks']), 2)

    def test_unmodified_outer_edge_must_not_be_trimmed(self):
        report, recognize = self.refine(['我个人很爱这件的啊'])
        self.assertEqual(report['rows'][0]['status'], 'skipped')
        self.assertEqual(recognize.call_count, 1)

    def test_missing_local_model_preserves_original(self):
        with patch.object(audio, 'get_recognizer', side_effect=RuntimeError('offline')):
            report = audio.refine_cleanup(self.report, self.words, 'source.mp4')
        self.assertEqual(report['cleaned_candidates'], [self.source])
        self.assertIn('本地模型', report['rows'][0]['reason'])

    def test_no_hits_never_initializes_model(self):
        self.report['rows'][0]['status'] = 'unchanged'
        with patch.object(audio, 'get_recognizer') as factory:
            audio.refine_cleanup(self.report, self.words, 'source.mp4')
        factory.assert_not_called()

    def test_encoded_export_requires_complete_retained_speech(self):
        with patch.object(audio, '_run'):
            self.assertEqual(audio.verify_export('test.mp4', '我个人很爱这件的',
                             recognize=lambda path: '我個人很愛這件的。'), '我個人很愛這件的。')
            for actual in ['以我个人很爱这件的', '个人很爱这件的']:
                with self.assertRaisesRegex(ValueError, '导出音频复核未通过'):
                    audio.verify_export('test.mp4', '我个人很爱这件的', recognize=lambda path: actual)


if __name__ == '__main__':
    unittest.main()
