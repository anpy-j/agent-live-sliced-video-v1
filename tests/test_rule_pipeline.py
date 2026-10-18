import copy
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from agent_video import rule_pipeline, filler_rules, pinyin_rules
from agent_video.pipeline import run as pipeline
from agent_video.pipeline_v3 import run as v3
from agent_video.pipeline_v2.common import prepare
from agent_video.server import Application, Server


def row(cid, text, usable=True):
    return {'id': cid, 'text': text, 'start': cid * 3, 'end': cid * 3 + 3, 'usable': usable}


class RulePipelineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'data/rule_pipeline.json'
        self.pinyin = self.root / 'data/pinyin_rules.json'
        self.filler = self.root / 'data/filler_rules.json'
        env = patch.dict(os.environ, {'LIVECUT_RULE_PIPELINE': str(self.path),
                                     'LIVECUT_PINYIN_RULES': str(self.pinyin),
                                     'LIVECUT_FILLER_RULES': str(self.filler)})
        env.start(); self.addCleanup(env.stop)

    def set_switch(self, name, enabled, **kwargs):
        return rule_pipeline.save_setting(self.path, name, {'enabled': enabled, **kwargs})

    def test_independent_persistent_stage_fixed_switches(self):
        defaults = rule_pipeline.load_settings()
        self.assertTrue(defaults['features']['pinyin']['enabled'])
        self.assertTrue(defaults['features']['filler']['enabled'])
        self.assertFalse(defaults['features']['repetition']['enabled'])
        self.set_switch('pinyin', False)
        saved = self.set_switch('repetition', True, options={'mode': 'sentence', 'min_count': 3})
        self.assertFalse(saved['features']['pinyin']['enabled'])
        self.assertTrue(saved['features']['filler']['enabled'])
        self.assertEqual(rule_pipeline.load_settings(), saved)
        for payload in [{'enabled': 'false'}, {'enabled': True, 'stage': 'S4'},
                        {'enabled': True, 'version': 'v2'}]:
            with self.assertRaises(ValueError):
                rule_pipeline.save_setting(self.path, 'pinyin', payload)
        self.assertEqual(rule_pipeline.load_settings(), saved)

    def test_s2_switch_on_off_and_trial_unaffected(self):
        pinyin_rules.save_rule(self.pinyin, {'pinyin': 'yang qi'})
        work = self.root / 'work'; work.mkdir()
        original = [row(0, '你秋冬这样穿又洋气又好看。')]
        self.set_switch('pinyin', False)
        clauses = copy.deepcopy(original); pipeline._filter_s2(clauses, str(work), .8)
        self.assertTrue(clauses[0]['usable'])
        self.assertTrue(pinyin_rules.match_text(original[0]['text'], pinyin_rules.load_rules())['hits'])
        self.set_switch('pinyin', True)
        clauses = copy.deepcopy(original); pipeline._filter_s2(clauses, str(work), .8)
        self.assertFalse(clauses[0]['usable'])
        self.assertEqual(clauses[0]['reason'], 'pinyin_rule')
        snapshot = json.loads((work / 's2-rule-pipeline.snapshot.json').read_text())
        self.assertTrue(snapshot['features']['pinyin']['enabled'])

    def test_repetition_only_removes_whole_later_duplicates(self):
        clauses = [row(0, '超级超级超级超级好看。'), row(1, '超级好看。'),
                   row(2, '版型很宽松，超级好看。'), row(3, '超级好看。', False)]
        report = rule_pipeline.apply_repetition(clauses, rule_pipeline.DEFAULTS['repetition']['options'])
        self.assertEqual(report['removed_ids'], [1])
        self.assertTrue(clauses[0]['usable']); self.assertTrue(clauses[2]['usable'])
        self.assertFalse(clauses[3]['usable']); self.assertEqual(clauses[1]['repetition_reference'], 0)
        clauses = [row(0, '版型宽松。超级好看。'), row(1, '超级好看。')]
        self.assertEqual(rule_pipeline.apply_repetition(clauses, rule_pipeline.DEFAULTS['repetition']['options'])['removed_ids'], [])

    def test_repetition_switch_runs_only_in_v1_s2(self):
        work = self.root / 'work'; work.mkdir()
        original = [row(0, '超级超级超级超级好看。'), row(1, '超级好看。')]
        self.set_switch('repetition', False)
        clauses = copy.deepcopy(original); pipeline._filter_s2(clauses, str(work), .8)
        self.assertTrue(clauses[1]['usable'])
        self.set_switch('repetition', True)
        clauses = copy.deepcopy(original); pipeline._filter_s2(clauses, str(work), .8)
        self.assertEqual(clauses[1]['reason'], 'repetition_rule')

    def test_filler_off_preserves_cuts_and_trial_still_matches(self):
        snapshot = filler_rules.save_rule(self.filler, {'phrase': '真的'})
        work = self.root / 'work'; work.mkdir()
        original = [row(0, '真的你秋冬这样穿又洋气又好看。')]
        (work / 'clauses.json').write_text(json.dumps({'words': [
            {'w': '真的', 's': 0, 'e': .4}, {'w': original[0]['text'][2:], 's': .4, 'e': 3}]}))
        self.set_switch('filler', False)
        report = pipeline._clean_s4(original, [0], original, str(work))
        self.assertEqual(report['cleaned_candidates'], original)
        self.assertFalse(report['integration']['enabled'])
        self.assertTrue(filler_rules.match_text(original[0]['text'], snapshot)['hits'])
        self.set_switch('filler', True)
        report = pipeline._clean_s4(original, [0], original, str(work))
        self.assertEqual(report['cleaned_candidates'][0]['start'], .4)
        self.set_switch('filler', False)
        self.assertEqual(pipeline._restore_s4_cleaning({'filler_cleanup': report}, original)[0]['start'], .4)

    def test_full_run_and_single_stages_keep_phase_order(self):
        work = self.root / 'work'
        words = [{'w': '真的', 's': 0, 'e': .4}, {'w': '你秋冬这样穿又洋气又好看。', 's': .4, 'e': 3}]
        filler_rules.save_rule(self.filler, {'phrase': '真的'})
        def fake_ai(model, prompt, schema, timeout):
            if schema is pipeline.DECISION_SCHEMA:
                return {'decisions': [{'id': 0, 'usable': True, 'reason': '具体效果'}]}
            return {'main_product': '穿搭', 'sections': [{'role': 'hook', 'ids': [0]}], 'ordered_ids': [0]}
        sequences = []
        for enabled in (False, True):
            for name in rule_pipeline.DEFAULTS:
                self.set_switch(name, enabled)
            events = []
            with patch.object(pipeline, 'ai_call', side_effect=fake_ai), patch.object(pipeline, 'render_video'):
                result = pipeline.run_pipeline('source.mp4', str(work), transcript=([], words),
                    target_seconds=(2, 4), on_stage=lambda s,status,m: events.append((s,status)))
            sequences.append(events)
            self.assertEqual(result['segments'][0]['start'], .4 if enabled else 0)
            with patch.object(pipeline, 'ai_call', side_effect=fake_ai), patch.object(pipeline, 'render_video'):
                for stage in ('filter', 'judge', 'order', 'render'):
                    pipeline.run_pipeline_stage('source.mp4', str(work), stage, target_seconds=(2, 4))
        self.assertEqual(sequences[0], sequences[1])
        self.assertEqual([s for s,status in sequences[0] if status == 'start'],
                         ['asr', 'filter', 'judge', 'order', 'render'])

    def test_v2_v3_s2_not_affected_by_v1_switches(self):
        pinyin_rules.save_rule(self.pinyin, {'pinyin': 'yang qi'})
        self.set_switch('pinyin', False); self.set_switch('repetition', True)
        words = [{'w': '你秋冬这样穿又洋气又好看。', 's': 0, 'e': 3}]
        for name in ('v2', 'v3'):
            work = self.root / name; work.mkdir()
            if name == 'v2':
                run = lambda: prepare('source.mp4', work, transcript=([], words), min_duration=.8)
            else:
                run = lambda: v3.run_pipeline('source.mp4', str(work), transcript=([], words), min_duration=.8)
            from agent_video.pipeline_v3.errors import RuleFilterEmpty
            with self.assertRaises(RuleFilterEmpty):
                run()
            self.assertFalse((work / 's2-rule-pipeline.snapshot.json').exists())

    def test_http_switches_and_disabled_text_trial(self):
        app = Application(self.root)
        filler_rules.save_rule(self.filler, {'phrase': '真的'})
        server = Server(('127.0.0.1', 0), app)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def request(method, route, body=None):
            with opener.open(urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/rules/' + route,
                data=json.dumps(body).encode() if body is not None else None, method=method,
                headers={'Content-Type': 'application/json'})) as response:
                return json.loads(response.read())
        try:
            self.assertTrue(request('GET', 'pipeline')['features']['filler']['enabled'])
            result = request('PUT', 'pipeline/filler', {'enabled': False})
            self.assertFalse(result['features']['filler']['enabled'])
            self.assertTrue(result['features']['pinyin']['enabled'])
            self.assertEqual(request('POST', 'filler/test', {'text': '真的很好看'})['matched'], 1)
        finally:
            server.shutdown(); thread.join(); server.server_close()


if __name__ == '__main__':
    unittest.main()
