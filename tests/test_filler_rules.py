import copy
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from agent_video import filler_rules as rules
from agent_video.pipeline import run as pipeline
from agent_video.pipeline.errors import PipelineError
from agent_video.server import Application, Server


WORDS = [{"w": "真的", "s": 0, "e": .4},
         {"w": "你秋冬这样穿又洋气又好看。", "s": .4, "e": 3}]
TEXT = "真的你秋冬这样穿又洋气又好看。"
CANDIDATE = {"id": 0, "text": TEXT, "start": 0, "end": 3, "usable": True, "members": [0]}


class FillerRulesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "data/filler_rules.json"
        self.env = patch.dict(os.environ, {"LIVECUT_FILLER_RULES": str(self.path),
                                          "LIVECUT_RULE_PIPELINE": str(self.root / "data/rule_pipeline.json"),
                                          "LIVECUT_PINYIN_RULES": str(self.root / 'empty.json')})
        self.env.start(); self.addCleanup(self.env.stop)
        self.snapshot = rules.save_rule(self.path, {"phrase": "真的"})

    def test_crud_and_validation(self):
        rule = self.snapshot['rules'][0]
        self.assertEqual(rule['scope'], 'all')
        self.assertEqual(rules.load_rules(self.path), self.snapshot)
        for phrase in ['真的', '', '真 的', '真的，', 'a', '真' * 33]:
            with self.assertRaises(ValueError):
                rules.save_rule(self.path, {'phrase': phrase})
        with self.assertRaises(ValueError):
            rules.save_rule(self.path, {'phrase': '其实', 'enabled': 'false'})
        with self.assertRaises(ValueError):
            rules.save_rule(self.path, {'pinyin': 'qi shi', 'scope': 'middle'})
        saved = rules.save_rule(self.path, {**rule, 'enabled': False}, rule['id'])
        self.assertNotEqual(saved['revision'], self.snapshot['revision'])
        self.assertFalse(rules.match_text(TEXT, saved)['hits'])
        self.assertEqual(rules.delete_rule(self.path, rule['id'])['rules'], [])

    def test_scope_leading_only_cleans_clip_start(self):
        snapshot = rules.save_rule(self.path, {'pinyin': 'qi shi', 'scope': 'leading'})
        leading = rules.match_text('其实你秋冬这样穿好看', snapshot)
        self.assertEqual(leading['preview_text'], '你秋冬这样穿好看')
        self.assertEqual([h['text'] for h in leading['hits']], ['其实'])
        middle = rules.match_text('你秋冬其实这样穿好看', snapshot)
        self.assertEqual(middle['hits'], [])
        self.assertEqual([h['text'] for h in middle['leading_skipped']], ['其实'])
        self.assertEqual(middle['preview_text'], '你秋冬其实这样穿好看')
        clip, trial = rules.clean_candidate({**CANDIDATE, 'text': '你秋冬其实这样穿好看。'}, [], snapshot)
        self.assertEqual(clip['text'], '你秋冬其实这样穿好看。')
        self.assertEqual(trial['status'], 'unchanged')
        self.assertIn('句首', trial['reason'])
        # Switching a rule back to 全部 clears every position again.
        qi = next(r for r in snapshot['rules'] if r['pinyin'] == 'qi shi')
        plain = rules.save_rule(self.path, {**qi, 'scope': 'all'}, qi['id'])
        self.assertEqual(rules.match_text('你秋冬其实这样穿好看', plain)['preview_text'], '你秋冬这样穿好看')

    def test_any_position_homophones_overlaps_and_multiple_hits(self):
        for pinyin in ['wo gao su ni', 'qi shi', 'er qie', 'zhen']:
            self.snapshot = rules.save_rule(self.path, {'pinyin': pinyin})
        result = rules.match_text('我告诉你，其实真的你这样穿好看，而且真得显瘦', self.snapshot)
        self.assertEqual(result['preview_text'], '你这样穿好看，显瘦')
        self.assertIn('真得', [h['text'] for h in result['hits']])
        for text in ['这是真的羊毛', '不是真的羊毛', '你这样穿真的好看', '你这样穿好看真的']:
            self.assertTrue(rules.match_text(text, self.snapshot)['hits'])
        self.assertFalse(rules.match_text('真的羊毛', {**self.snapshot, 'rules': [
            {'id': 'x', 'pinyin': 'zhen de yang qi', 'name': 'test', 'enabled': True}]})['hits'])

    def test_pinyin_input_validation_and_legacy_rule_read(self):
        saved = rules.save_rule(self.path, {'pinyin': 'QI   SHI'})
        self.assertEqual(saved['rules'][-1]['pinyin'], 'qi shi')
        for value in ['真的', 'zhen de1', 'invalid', '', 'zhen * de']:
            with self.assertRaises(ValueError):
                rules.save_rule(self.path, {'pinyin': value})
        legacy = {'revision': 'old', 'rules': [
            {'id': 'old', 'phrase': '真的', 'note': '旧备注', 'enabled': False}]}
        self.path.write_text(json.dumps(legacy), encoding='utf-8')
        loaded = rules.load_rules(self.path)
        self.assertEqual(loaded['rules'][0]['pinyin'], 'zhen de')
        self.assertEqual(loaded['rules'][0]['note'], '旧备注')
        self.assertFalse(loaded['rules'][0]['enabled'])
        self.assertEqual(loaded['rules'][0]['scope'], 'all')

    def test_middle_tail_and_multiple_cuts_preserve_only_remaining_ranges(self):
        snapshot = rules.save_rule(self.path, {'pinyin': 'qi shi'})
        text = '真得你这样穿其实显瘦真的'
        words = [{'w': '真得', 's': 0, 'e': .4}, {'w': '你这样穿', 's': .4, 'e': 1.4},
                 {'w': '其实', 's': 1.4, 'e': 1.8}, {'w': '显瘦', 's': 1.8, 'e': 2.6},
                 {'w': '真的', 's': 2.6, 'e': 3}]
        candidate = {**CANDIDATE, 'text': text}
        report = rules.clean_selection([candidate], words, snapshot)
        self.assertEqual([(c['start'], c['end']) for c in report['cleaned_candidates']], [(.4, 1.4), (1.8, 2.6)])
        self.assertEqual(report['rows'][0]['cleaned_text'], '你这样穿显瘦')
        self.assertEqual(report['total_seconds'], 1.8)
        self.assertEqual(pipeline._restore_s4_cleaning({'filler_cleanup': report}, [candidate]), report['cleaned_candidates'])
        report['cleaned_candidates'][0]['end'] = 1.5
        with self.assertRaises(PipelineError):
            pipeline._restore_s4_cleaning({'filler_cleanup': report}, [candidate])

    def test_partial_alignment_retains_other_hits(self):
        candidate = {**CANDIDATE, 'text': '真的你这样穿真的好看。'}
        words = [{'w': '真的', 's': 0, 'e': .4}, {'w': '你这样穿真的好看。', 's': .4, 'e': 3}]
        clip, trial = rules.clean_candidate(candidate, words, self.snapshot)
        self.assertEqual(clip['text'], '你这样穿真的好看。')
        self.assertEqual(len(trial['hits']), 2)
        self.assertIn('其余命中', trial['reason'])

    def test_legacy_leading_only_snapshot_still_renders(self):
        clip = {**CANDIDATE, 'start': .4, 'text': TEXT[2:]}
        report = {'version': 1, 'source_candidates': [CANDIDATE], 'cleaned_candidates': [clip]}
        self.assertEqual(pipeline._restore_s4_cleaning({'filler_cleanup': report}, [CANDIDATE]), [clip])


    def test_precise_cut_does_not_mutate_input(self):
        original = copy.deepcopy(CANDIDATE)
        clip, trial = rules.clean_candidate(CANDIDATE, WORDS, self.snapshot)
        self.assertEqual(CANDIDATE, original)
        self.assertEqual((clip['start'], clip['end'], clip['text']), (.4, 3, TEXT[2:]))
        self.assertEqual(trial['removed_seconds'], .4)
        self.assertEqual(trial['status'], 'cleaned')

    def test_no_estimated_cuts_for_missing_or_misaligned_words(self):
        for words in [[], [{'w': TEXT, 's': 0, 'e': 3}],
                      [{'w': '真的', 's': 0, 'e': 1}, {'w': TEXT[2:], 's': .4, 'e': 3}],
                      [{'w': '真的其他正文', 's': 0, 'e': 3}]]:
            clip, trial = rules.clean_candidate(CANDIDATE, words, self.snapshot)
            self.assertEqual(clip, CANDIDATE)
            self.assertEqual(trial['status'], 'skipped')
        clip, trial = rules.clean_candidate({**CANDIDATE, 'text': '真的'}, WORDS[:1], self.snapshot)
        self.assertEqual(trial['status'], 'skipped')

    def fake_ai(self, model, prompt, schema, timeout):
        if schema is pipeline.DECISION_SCHEMA:
            return {'decisions': [{'id': 0, 'usable': True, 'reason': '具体穿搭效果'}]}
        return {'main_product': '秋冬穿搭', 'sections': [{'role': 'hook', 'ids': [0]}], 'ordered_ids': [0]}

    def run_sample(self, work):
        with patch.object(pipeline, 'ai_call', side_effect=self.fake_ai), patch.object(pipeline, 'render_video') as render:
            manifest = pipeline.run_pipeline(str(self.root / 'source.mp4'), str(work),
                                             transcript=([], WORDS), target_seconds=(2, 4))
        self.assertEqual(render.call_args.args[1][0]['start'], .4)
        self.assertEqual(manifest['total_seconds'], 2.6)
        self.assertEqual(manifest['segments'][0]['text'], TEXT[2:])

        return manifest

    def test_full_s4_rerun_and_saved_render_snapshot(self):
        work = self.root / 'job'
        self.run_sample(work)
        self.assertEqual(json.loads((work / 'clauses.json').read_text(encoding='utf-8'))['words'], WORDS)
        before = (work / 'clauses.json').read_bytes()
        # A changed rule does not silently change an already compiled render.
        rule = self.snapshot['rules'][0]
        rules.delete_rule(self.path, rule['id'])
        with patch.object(pipeline, 'render_video') as render:
            manifest = pipeline.run_pipeline_stage(str(self.root / 'source.mp4'), str(work), 'render')
        self.assertEqual(render.call_args.args[1][0]['start'], .4)
        self.assertEqual(manifest['total_seconds'], 2.6)
        with patch.object(pipeline, 'ai_call', side_effect=self.fake_ai):
            result = pipeline.run_pipeline_stage(str(self.root / 'source.mp4'), str(work), 'order', target_seconds=(2, 4))
        self.assertEqual(result['total_seconds'], 3)
        self.assertEqual(result['filler_cleanup']['cleaned_candidates'][0]['start'], 0)
        self.assertEqual((work / 'clauses.json').read_bytes(), before)
        rules.save_rule(self.path, {'phrase': '真的'})
        with patch.object(pipeline, 'ai_call', side_effect=self.fake_ai):
            result = pipeline.run_pipeline_stage(str(self.root / 'source.mp4'), str(work), 'order', target_seconds=(2, 4))
        self.assertEqual(result['total_seconds'], 2.6)
        with patch.object(pipeline, 'render_video') as render:
            pipeline.run_pipeline_stage(str(self.root / 'source.mp4'), str(work), 'render')
        self.assertEqual(render.call_args.args[1][0]['start'], .4)
        timeline = json.loads((work / 'timeline.json').read_text(encoding='utf-8'))
        timeline['clauses'][0]['text'] = '已改变的上游'
        (work / 'timeline.json').write_text(json.dumps(timeline), encoding='utf-8')
        with self.assertRaises(PipelineError):
            pipeline.run_pipeline_stage(str(self.root / 'source.mp4'), str(work), 'render')

    def test_s1_cache_preserves_word_boundaries(self):
        work = self.root / 'job'
        self.run_sample(work)
        with patch.object(pipeline, 'ai_call', side_effect=self.fake_ai), patch.object(pipeline, 'render_video'):
            manifest = pipeline.run_pipeline(str(self.root / 'source.mp4'), str(work), target_seconds=(2, 4))
        self.assertEqual(manifest['segments'][0]['start'], .4)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg unavailable')
    def test_actual_video_render_removes_prefix_duration(self):
        media = self.root / 'source.mp4'
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                        'color=c=red:size=160x120:rate=25:duration=3', '-f', 'lavfi', '-i',
                        'sine=frequency=440:duration=3', '-c:v', 'libx264', '-c:a', 'aac',
                        '-shortest', str(media)], check=True, capture_output=True)
        work = self.root / 'video-job'
        with patch.object(pipeline, 'ai_call', side_effect=self.fake_ai):
            manifest = pipeline.run_pipeline(str(media), str(work), transcript=([], WORDS),
                                             target_seconds=(2, 4))
        output = work / manifest['output']
        self.assertTrue(output.is_file())
        duration = float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries',
                                                  'format=duration', '-of', 'default=nw=1:nk=1',
                                                  str(output)], text=True))
        self.assertAlmostEqual(duration, 2.6, delta=.15)
        self.assertEqual(manifest['segments'][0]['source_start'], .4)
        self.assertEqual(manifest['segments'][0]['text'], TEXT[2:])

        # A middle and a tail hit must create two physical cuts with one logical export ID.
        middle_words = [{'w': '你秋冬', 's': 0, 'e': .6}, {'w': '真的', 's': .6, 'e': 1},
                        {'w': '这样穿又洋气又好看', 's': 1, 'e': 2.6}, {'w': '真的。', 's': 2.6, 'e': 3}]
        middle_work = self.root / 'middle-video-job'
        with patch.object(pipeline, 'ai_call', side_effect=self.fake_ai):
            manifest = pipeline.run_pipeline(str(media), str(middle_work), transcript=([], middle_words),
                                             target_seconds=(2, 4))
        self.assertEqual([(s['start'], s['end']) for s in manifest['segments']], [(0, .6), (1, 2.6)])
        self.assertEqual(manifest['total_seconds'], 2.2)
        from agent_video.pipeline.render import group_segments_for_export
        self.assertEqual(len(group_segments_for_export(manifest['segments'])), 1)
        with patch.object(pipeline, 'ai_call', side_effect=self.fake_ai):
            order = pipeline.run_pipeline_stage(str(media), str(middle_work), 'order', target_seconds=(2, 4))
        self.assertEqual(len(order['filler_cleanup']['cleaned_candidates']), 2)
        rendered = pipeline.run_pipeline_stage(str(media), str(middle_work), 'render')
        duration = float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries',
                                                  'format=duration', '-of', 'default=nw=1:nk=1',
                                                  str(middle_work / rendered['output'])], text=True))
        self.assertAlmostEqual(duration, 2.2, delta=.15)
        self.assertEqual([(s['start'], s['end']) for s in rendered['segments']], [(0, .6), (1, 2.6)])

    def test_http_crud_text_and_readonly_actual_s4_trial(self):
        app = Application(self.root)
        work = self.root / 'job'
        self.run_sample(work)
        job = {'id': app.store.create_job(title='测试穿搭', source_path='source.mp4', workspace=str(work))}
        app.store.update_job(job['id'], status='completed')
        server = Server(('127.0.0.1', 0), app)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def request(method, route, body=None):
            raw = json.dumps(body).encode() if body is not None else None
            with opener.open(urllib.request.Request(
                f'http://127.0.0.1:{server.server_port}/api/rules/filler' + route,
                data=raw, method=method, headers={'Content-Type': 'application/json'})) as response:
                return json.loads(response.read())
        try:
            self.assertEqual(len(request('GET', '')['rules']), 1)
            added = request('POST', '', {'phrase': '其实', 'scope': 'leading'})['rules'][-1]
            self.assertEqual(added['scope'], 'leading')
            request('PUT', '/' + added['id'], {**added, 'enabled': False})
            self.assertEqual(request('POST', '/test', {'text': '其实很好看', 'rule_id': added['id']})['matched'], 1)
            self.assertEqual(request('POST', '/test', {'text': '面料其实很好看', 'rule_id': added['id']})['matched'], 0)
            request('DELETE', '/' + added['id'])
            self.assertEqual(request('POST', '/test', {'text': TEXT})['rows'][0]['cleaned_text'], TEXT[2:])
            self.assertEqual(request('GET', '/jobs')['jobs'][0]['id'], job['id'])
            before = {p.name: p.read_bytes() for p in work.iterdir() if p.is_file()}
            result = request('POST', '/test', {'job_id': job['id']})
            self.assertEqual((result['matched'], result['cleaned']), (1, 1))
            self.assertEqual(result['rows'][0]['cleaned_start'], .4)
            self.assertEqual(before, {p.name: p.read_bytes() for p in work.iterdir() if p.is_file()})
        finally:
            server.shutdown(); thread.join(); server.server_close()


if __name__ == '__main__':
    unittest.main()
