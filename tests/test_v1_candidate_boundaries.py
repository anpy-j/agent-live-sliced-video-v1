"""Regression tests: retained text and physical video intervals must agree."""
import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_video.pipeline import run as pipeline
from agent_video.pipeline.errors import PipelineError
from agent_video.pipeline.filter import filter_clauses
from agent_video.pipeline.split import _merge_short
from agent_video.pipeline.units import build_units, order_candidates
from agent_video.server import Application


def row(cid, text, start, end, usable=True, unit=0):
    return dict(id=cid, text=text, start=start, end=end, usable=usable, unit=unit)


class CandidateBoundariesTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.work = self.root / 'work'; self.work.mkdir()
        env = patch.dict(os.environ, {
            'LIVECUT_RULE_PIPELINE': str(self.root / 'rules.json'),
            'LIVECUT_PINYIN_RULES': str(self.root / 'pinyin.json'),
            'LIVECUT_FILLER_RULES': str(self.root / 'filler.json')})
        env.start(); self.addCleanup(env.stop)
        (self.root / 'rules.json').write_text(json.dumps({'features': {
            'pinyin': {'enabled': False}, 'filler': {'enabled': False}, 'breath': {'enabled': False}}}))
        self.rows = [row(0, '这个面料', 0, 1.2), row(1, '这里中间一段内容', 1.2, 2.4, False),
                     row(2, '摸起来很柔软', 2.4, 3.6)]

    def write_upstream(self, rows=None, filename='timeline.json'):
        (self.work / filename).write_text(json.dumps({'source': 'source.mp4', 'duration': 3.6,
            'clauses': rows if rows is not None else self.rows}, ensure_ascii=False), encoding='utf-8')

    def fake_ai(self, model, prompt, schema, timeout):
        if schema is pipeline.DECISION_SCHEMA:
            return {'decisions': [dict(id=i, usable=i != 1, reason='测试') for i in range(3)]}
        payload = json.loads(prompt.split('可用句单元（按时间序，JSON）：\n')[1])
        self.assertEqual([c['id'] for c in payload], [0, 2])
        self.assertAlmostEqual(sum(c['seconds'] for c in payload), 2.4)
        return {'main_product': '面料', 'sections': [{'role': 'hook', 'ids': [0, 2]}], 'ordered_ids': [0, 2]}

    def test_rejected_s3_clause_is_a_physical_boundary(self):
        candidates = order_candidates(self.rows)
        self.assertEqual([(c['start'], c['end']) for c in candidates], [(0, 1.2), (2.4, 3.6)])
        self.assertEqual([c['members'] for c in candidates], [[0], [2]])

    def test_short_s2_rejection_breaks_units_and_candidates(self):
        rows = [row(0, '这个面料', 0, 1.2), row(1, '嗯', 1.2, 1.3, False),
                row(2, '摸起来很柔软', 1.3, 2.5)]
        units = build_units(rows)
        self.assertEqual([u['text'] for u in units], ['这个面料', '摸起来很柔软'])
        self.assertNotIn('unit', rows[1])
        self.assertEqual([c['members'] for c in order_candidates(rows)], [[0], [2]])

    def test_s3_rerun_preserves_s2_rejection_boundaries(self):
        self.write_upstream(filename='clauses.filtered.json')
        with patch.object(pipeline, 'ai_call', side_effect=self.fake_ai), patch.object(
                pipeline, 'build_units', wraps=build_units) as build:
            result = pipeline.run_pipeline_stage('source.mp4', str(self.work), 'judge')
        self.assertEqual([r['id'] for r in build.call_args.args[0]], [0, 1, 2])
        self.assertEqual(result['candidates'], 2)

    def test_missing_unit_long_gap_and_overlap_cannot_merge(self):
        for unit, start in [(None, 1.2), (0, 4), (0, 1.0)]:
            with self.subTest(unit=unit, start=start):
                rows = [row(0, '前句', 0, 1.2, unit=unit), row(1, '后句', start, start+1.2, unit=unit)]
                self.assertEqual(len(order_candidates(rows)), 2)

    def test_valid_adjacent_retained_clauses_still_merge(self):
        rows = [row(0, '这个面料', 0, 1.2), row(1, '摸起来很柔软', 1.2, 2.4)]
        self.assertEqual(order_candidates(rows)[0]['members'], [0, 1])

    def test_short_clause_merge_checks_actual_span(self):
        groups = [[dict(w='前句', s=0, e=2)], [dict(w='后句', s=8, e=8.2)]]
        self.assertEqual(len(_merge_short(groups, .8, 6)), 2)

    def run_full(self, media='source.mp4', real_render=False):
        rows = self.rows
        sentences = [dict(start=r['start'], end=r['end']) for r in rows]
        words = [dict(w=r['text'], s=r['start'], e=r['end']) for r in rows]
        with patch.object(pipeline, 'ai_call', side_effect=self.fake_ai):
            if real_render:
                return pipeline.run_pipeline(str(media), str(self.work), transcript=(sentences, words),
                    target_seconds=(2, 4), plugin_config={'enabled': False}, render_size=(160, 120), preset='ultrafast')
            with patch.object(pipeline, 'render_video'):
                return pipeline.run_pipeline(str(media), str(self.work), transcript=(sentences, words),
                    target_seconds=(2, 4), plugin_config={'enabled': False})

    def test_full_s4_rerun_and_render_have_identical_intervals(self):
        full = self.run_full()
        with patch.object(pipeline, 'ai_call') as ai, patch.object(pipeline, 'render_video'):
            direct_rerender = pipeline.run_pipeline_stage('source.mp4', str(self.work), 'render', target_seconds=(2, 4))
        ai.assert_not_called()
        self.assertEqual(full['segments'], direct_rerender['segments'])
        with patch.object(pipeline, 'ai_call', side_effect=self.fake_ai):
            order = pipeline.run_pipeline_stage('source.mp4', str(self.work), 'order',
                target_seconds=(2, 4), plugin_config={'enabled': False})
        self.assertEqual(order['ordered_ids'], [0, 2])
        with patch.object(pipeline, 'ai_call') as ai, patch.object(pipeline, 'render_video') as render:
            rerender = pipeline.run_pipeline_stage('source.mp4', str(self.work), 'render', target_seconds=(2, 4))
        ai.assert_not_called()
        self.assertEqual(full['segments'], rerender['segments'])
        self.assertEqual([(c['start'], c['end']) for c in render.call_args.args[1]], [(0, 1.2), (2.4, 3.6)])

    def test_full_execution_cannot_bridge_a_short_s2_rejection(self):
        self.rows = [row(0, '这个面料', 0, 1.2), row(1, '嗯', 1.2, 1.3),
                     row(2, '摸起来很柔软', 1.3, 2.5)]
        with patch.object(pipeline, 'split_clauses', return_value=copy.deepcopy(self.rows)):
            full = self.run_full()
        self.assertEqual([(s['start'], s['end']) for s in full['segments']], [(0, 1.2), (1.3, 2.5)])

    def test_missing_or_invalid_render_ids_fail_before_rendering(self):
        self.write_upstream()
        for ids in ([0, 99], [0, 0], [True], ['0'], [], None):
            with self.subTest(ids=ids):
                (self.work / 'order.json').write_text(json.dumps({'ordered_ids': ids}))
                with patch.object(pipeline, 'render_video') as render:
                    with self.assertRaisesRegex(PipelineError, '重新执行 S4'):
                        pipeline.run_pipeline_stage('source.mp4', str(self.work), 'render')
                render.assert_not_called()
        (self.work / 'order.json').write_text('[]')
        with self.assertRaisesRegex(PipelineError, '重新执行 S4'):
            pipeline.run_pipeline_stage('source.mp4', str(self.work), 'render')

    def test_filler_job_trial_uses_the_same_boundaries(self):
        self.write_upstream()
        (self.work / 'order.json').write_text(json.dumps({'ordered_ids': [0, 2]}))
        app = Application(self.root)
        jid = app.store.create_job(title='测试', source_path='source.mp4', workspace=str(self.work))
        app.store.update_job(jid, status='completed')
        from agent_video import filler_rules
        with patch.object(filler_rules, 'clean_selection', return_value={'rows': []}) as clean:
            app.test_filler_rules({'job_id': jid})
        self.assertEqual([c['members'] for c in clean.call_args.args[0]], [[0], [2]])

    def test_numeric_and_negated_claims_are_not_similarity_duplicates(self):
        for first, second in [
            ('这件衣服不是纯羊毛面料', '这件衣服是纯羊毛面料'),
            ('这件衣服面料含有百分之九十的羊毛', '这件衣服面料含有百分之八十的羊毛'),
            ('这件衣服的面料含有95%的羊毛', '这件衣服的面料含有85%的羊毛'),
            ('这件衣服的面料含有3.5%的羊毛', '这件衣服的面料含有35%的羊毛')]:
            with self.subTest(first=first):
                rows = [row(0, first, 0, 2), row(1, second, 2, 4)]
                filter_clauses(rows, pinyin_rules={'rules': []})
                self.assertTrue(all(r['usable'] for r in rows))
        rows = [row(0, '面料柔软穿着舒适', 0, 2), row(1, '面料柔软穿着舒适。', 2, 4)]
        filter_clauses(rows, pinyin_rules={'rules': []})
        self.assertEqual(rows[1]['reason'], 'duplicate')

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'requires ffmpeg')
    def test_real_video_excludes_middle_color_in_full_and_rerender(self):
        media = self.root / 'colors.mp4'
        subprocess.run(['ffmpeg', '-y', '-v', 'error',
            '-f', 'lavfi', '-i', 'color=c=red:s=160x120:r=25:d=1.2',
            '-f', 'lavfi', '-i', 'color=c=blue:s=160x120:r=25:d=1.2',
            '-f', 'lavfi', '-i', 'color=c=green:s=160x120:r=25:d=1.2',
            '-f', 'lavfi', '-i', 'sine=frequency=440:duration=3.6',
            '-filter_complex', '[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]',
            '-map', '[v]', '-map', '3:a', '-c:v', 'libx264', '-c:a', 'aac', str(media)],
            check=True, capture_output=True)
        full = self.run_full(media, real_render=True)
        with patch.object(pipeline, 'ai_call', side_effect=self.fake_ai):
            pipeline.run_pipeline_stage(str(media), str(self.work), 'order', target_seconds=(2, 4))
        with patch.object(pipeline, 'ai_call') as ai:
            rerender = pipeline.run_pipeline_stage(str(media), str(self.work), 'render',
                target_seconds=(2, 4), render_size=(160, 120), preset='ultrafast')
        ai.assert_not_called()
        for manifest in (full, rerender):
            video = self.work / manifest['output']
            rgb = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(video),
                '-vf', 'scale=1:1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'],
                check=True, capture_output=True).stdout
            pixels = [tuple(rgb[i:i+3]) for i in range(0, len(rgb), 3)]
            self.assertTrue(pixels)
            self.assertTrue(any(r > 150 for r, g, b in pixels))
            self.assertTrue(any(g > 70 and r < 50 for r, g, b in pixels))
            self.assertTrue(all(b < 50 for r, g, b in pixels), 'Rejected blue frames returned')
            self.assertAlmostEqual(len(pixels) / 25, 2.4, delta=.08)


if __name__ == '__main__':
    unittest.main()
