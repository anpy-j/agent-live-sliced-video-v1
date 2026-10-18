import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_video.filler_rules import save_rule
from agent_video.server import Application


class FillerTrialTest(unittest.TestCase):
    def setUp(self):
        def checked(report, *args, **kwargs):
            for row in report['rows']:
                row['audio_verification'] = {'status': 'verified'}
            return report
        for target, callback in [('refine_cleanup', checked), ('verify_export', lambda path, text: text)]:
            mocked = patch('agent_video.filler_trial.' + target, side_effect=callback)
            mocked.start(); self.addCleanup(mocked.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.app = Application(self.root)
        self.work = self.root / 'job'
        self.work.mkdir()
        self.media = self.root / 'source.mp4'
        self.media.touch()
        # Identical wording appears twice: only the requested occurrence may render.
        clauses = [{'id': i, 'text': '衣服真的好看', 'start': i * 3,
                    'end': i * 3 + 3, 'usable': True, 'unit': i} for i in range(2)]
        words = [dict(w=text, s=i * 3 + start, e=i * 3 + end)
                 for i in range(2) for text, start, end in
                 [('衣服', 0, 1), ('真的', 1, 2), ('好看', 2, 3)]]
        for name, data in [('timeline.json', {'clauses': clauses}),
                           ('clauses.json', {'words': words}),
                           ('order.json', {'ordered_ids': [0, 1]})]:
            (self.work / name).write_text(json.dumps(data), encoding='utf-8')
        self.revision = save_rule(self.app.data_dir / 'filler_rules.json',
                                  {'pinyin': 'zhen de'})['revision']
        job_id = self.app.store.create_job(title='重复文案', source_path=str(self.media),
                                           workspace=str(self.work))
        self.app.store.update_job(job_id, status='completed')
        self.payload = {'job_id': job_id, 'candidate_id': 1, 'revision': self.revision}

    def wait_result(self, token):
        self.assertTrue(self.app.filler_trials.lock.acquire(timeout=10))
        self.app.filler_trials.lock.release()
        return self.app.filler_trials.read(token)

    def test_only_selected_occurrence_and_retained_parts_are_rendered(self):
        before = {p.name: p.read_bytes() for p in self.work.iterdir()}
        def render(media, segments, output, workdir, **options):
            Path(output).write_bytes(b'video')
        with patch('agent_video.filler_trial.render_segment', side_effect=render) as renderer:
            token = self.app.filler_trials.start(self.payload)['id']
            state = self.wait_result(token)
        self.assertEqual(state['status'], 'completed')
        self.assertEqual(renderer.call_count, 2)
        segments = [call.args[1][0] for call in renderer.call_args_list]
        self.assertEqual([(s['id'], s['start'], s['end']) for s in segments],
                         [(1, 3, 4), (1, 5, 6)])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.work.iterdir()})
        self.assertEqual(self.app.filler_trials.media(token, 'part-1.mp4').read_bytes(), b'video')
        for token_arg, name in [(token, '../source.mp4'), ('../', 'part-1.mp4')]:
            with self.assertRaises(ValueError):
                self.app.filler_trials.media(token_arg, name)

    def test_reject_missing_invalid_stale_and_unaligned_selection(self):
        with patch('agent_video.filler_trial.render_segment') as renderer:
            for payload in [{}, {'job_id': self.payload['job_id']},
                            {**self.payload, 'candidate_id': 99},
                            {**self.payload, 'revision': 'old'}]:
                with self.assertRaises(ValueError):
                    self.app.filler_trials.start(payload)
            (self.work / 'clauses.json').write_text('{"words": []}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '无法精确清理'):
                self.app.filler_trials.start(self.payload)
            renderer.assert_not_called()

    def test_render_failure_is_reported_and_allows_retry(self):
        with patch('agent_video.filler_trial.render_segment', side_effect=RuntimeError('编码失败')):
            state = self.wait_result(self.app.filler_trials.start(self.payload)['id'])
        self.assertEqual(state['status'], 'failed')
        self.assertEqual(state['error'], '编码失败')
        self.assertIsNone(self.app.filler_trials.active)

    def test_virtual_timeline_maps_only_retained_ranges(self):
        from agent_video.timeline import VirtualTimeline, TimelineSegment
        virtual = VirtualTimeline('test', segments=[TimelineSegment(
            segment_id='one', source_path=str(self.media), timeline_start=0,
            timeline_end=6, source_start=10, source_end=22, speed=2)])
        (self.work / 'virtual_timeline.json').write_text(
            json.dumps(virtual.to_dict()), encoding='utf-8')
        with patch('agent_video.filler_trial.render_segment') as renderer:
            state = self.wait_result(self.app.filler_trials.start(self.payload)['id'])
        self.assertEqual(state['status'], 'completed')
        segments = [call.args[1][0] for call in renderer.call_args_list]
        self.assertEqual([(s['source_start'], s['source_end'], s['speed']) for s in segments],
                         [(16, 18, 2), (20, 22, 2)])

    def test_unverified_export_is_never_downloadable(self):
        def render(media, segments, output, workdir, **options):
            Path(output).write_bytes(b'not-verified')
        with patch('agent_video.filler_trial.render_segment', side_effect=render), \
                patch('agent_video.filler_trial.verify_export', side_effect=ValueError('残音未清除')):
            state = self.wait_result(self.app.filler_trials.start(self.payload)['id'])
        self.assertEqual(state['status'], 'failed')
        self.assertIn('残音未清除', state['error'])
        with self.assertRaisesRegex(ValueError, '尚未通过音频复核'):
            self.app.filler_trials.media(state['id'], 'part-1.mp4')

    def test_calibration_failure_does_not_render_original_as_cleaned(self):
        def unverified(report, *args, **kwargs):
            report['rows'][0].update(status='skipped', reason='正文缺字',
                                     audio_verification={'status': 'skipped'})
            return report
        with patch('agent_video.filler_trial.refine_cleanup', side_effect=unverified), \
                patch('agent_video.filler_trial.render_segment') as renderer:
            state = self.wait_result(self.app.filler_trials.start(self.payload)['id'])
        self.assertEqual(state['status'], 'failed')
        renderer.assert_not_called()


if __name__ == '__main__':
    unittest.main()
