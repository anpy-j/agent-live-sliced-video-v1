import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent_video import rule_pipeline, semantic_groups as plugin
from agent_video.db import Store, STAGE_DEFINITIONS
from agent_video.pipeline import run as pipeline
from agent_video.pipeline.ai import DECISION_SCHEMA
from agent_video.pipeline.errors import AIReturnError
from agent_video.pipeline_plugins import PLUGIN_IDS
from agent_video.runner import JobRunner


class SemanticGroupsTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.work = self.root / 'work'
        self.work.mkdir()
        self.settings = self.root / 'data/rule_pipeline.json'
        env = patch.dict(os.environ, {'LIVECUT_RULE_PIPELINE': str(self.settings),
            'LIVECUT_PINYIN_RULES': str(self.root / 'empty-pinyin.json'),
            'LIVECUT_FILLER_RULES': str(self.root / 'empty-filler.json')})
        env.start(); self.addCleanup(env.stop)
        self.rows = [dict(id=i, text=text, start=i * 4, end=i * 4 + 3, usable=True, unit=i)
                     for i, text in enumerate(['穿着特别舒服。', '穿着非常舒适。', '领口宽松不会勒脖子。'])]
        self.candidates = pipeline.order_candidates(self.rows)
        self.events = []
        self.calls = []

    def fake_ai(self, model, prompt, schema, timeout):
        self.calls.append((prompt, schema))
        if schema is plugin.EXTRACT_SCHEMA:
            return {'items': [dict(id=c['id'], subject='同一外套', claims=[c['text']],
                conditions='', standalone=True) for c in self.candidates]}
        if schema is plugin.GROUP_SCHEMA:
            return {'groups': [dict(name='穿着舒适', reason='同对象相同感受，没有新增信息', ids=[0, 1])]}
        if schema is DECISION_SCHEMA:
            return {'decisions': [dict(id=i, usable=True, reason='卖点') for i in range(3)]}
        return {'main_product': '外套', 'sections': [{'role': 'hook', 'ids': [1, 2]}], 'ordered_ids': [1, 2]}

    def prepare(self, enabled=True, **kwargs):
        return plugin.prepare(self.candidates, self.work, 'test-model', 20, self.fake_ai,
            lambda *event: self.events.append(event), {'enabled': enabled}, **kwargs)

    def write_upstream(self):
        for name in ('timeline.json', 'clauses.judged.json'):
            (self.work / name).write_text(json.dumps({'source': 'source.mp4', 'duration': 12,
                'clauses': self.rows}, ensure_ascii=False), encoding='utf-8')

    def test_disabled_does_no_io_no_calls_no_events(self):
        before = copy.deepcopy(self.candidates)
        self.assertIsNone(self.prepare(False))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.events, [])
        self.assertFalse((self.work / 'plugins').exists())
        self.assertEqual(self.candidates, before)
        self.assertEqual(plugin.order_suffix(None), '')

    def test_two_nodes_cache_and_no_candidate_mutation(self):
        before = copy.deepcopy(self.candidates)
        first = self.prepare()
        self.assertEqual(first['ai_calls'], 2)
        self.assertEqual([e[0] for e in self.events], ['claim_extract'] * 2 + ['claim_group'] * 2)
        second = self.prepare()
        self.assertEqual(second['ai_calls'], 0)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.candidates, before)
        self.candidates[2]['text'] += '改动'
        self.prepare()
        self.assertEqual(len(self.calls), 4)

    def test_extract_change_invalidates_group_cache(self):
        self.prepare()
        path = self.work / 'plugins/semantic_groups/extract.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        data['items'][0]['claims'] = ['人工纠正的信息点']
        path.write_text(json.dumps(data), encoding='utf-8')
        self.prepare()
        self.assertEqual(len(self.calls), 3)

    def test_invalid_extraction_and_groups_are_rejected(self):
        for items in ([], [{'id': True}], [dict(id=0, subject='外套', claims=[], conditions='', standalone=True)]):
            with self.assertRaises(AIReturnError):
                plugin.validate_items({'items': items}, {0})
        for groups in ([{'name': 'a', 'reason': 'b', 'ids': [0, 99]}],
                       [{'name': 'a', 'reason': 'b', 'ids': [0, 0]}],
                       [{'name': 'a', 'reason': 'b', 'ids': [0, 1]}, {'name': 'c', 'reason': 'd', 'ids': [1, 2]}]):
            with self.assertRaises(AIReturnError):
                plugin.validate_groups({'groups': groups}, {0, 1, 2})

    def test_group_constraint_is_hard_validated_but_groups_optional(self):
        report = self.prepare()
        for ids in ([0, 2], [1, 2], [2]):
            plugin.validate_order({'ordered_ids': ids}, report)
        with self.assertRaises(AIReturnError):
            plugin.validate_order({'ordered_ids': [0, 1, 2]}, report)

    def test_single_plugin_steps_and_stale_extraction(self):
        self.prepare(stop_after='claim_extract')
        self.assertEqual(len(self.calls), 1)
        self.events.clear()
        self.prepare(stop_after='claim_group')
        self.assertEqual([e[0] for e in self.events], ['claim_group', 'claim_group'])
        self.candidates[0]['start'] = .1
        with self.assertRaises(AIReturnError):
            self.prepare(stop_after='claim_group')

    def test_s4_off_has_original_prompt_and_only_original_call(self):
        self.write_upstream()
        with patch.object(pipeline, 'ai_call', side_effect=self.fake_ai):
            result = pipeline.run_pipeline_stage('source.mp4', str(self.work), 'order',
                target_seconds=(5, 8), plugin_config={'enabled': False},
                on_stage=lambda *e: self.events.append(e))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][0], pipeline._order_prompt(self.candidates, (5, 8), None, 9))
        self.assertNotIn('semantic_groups', result)
        self.assertEqual([e[0] for e in self.events], ['order', 'order'])
        self.assertFalse((self.work / 'plugins').exists())

    def test_s4_on_adds_nodes_constraints_without_editing_upstream(self):
        self.write_upstream()
        upstream = (self.work / 'clauses.judged.json').read_bytes()
        with patch.object(pipeline, 'ai_call', side_effect=self.fake_ai):
            result = pipeline.run_pipeline_stage('source.mp4', str(self.work), 'order',
                target_seconds=(5, 8), plugin_config={'enabled': True},
                on_stage=lambda *e: self.events.append(e))
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(result['ordered_ids'], [1, 2])
        self.assertIn('可选插件', self.calls[-1][0])
        self.assertEqual(upstream, (self.work / 'clauses.judged.json').read_bytes())
        self.assertEqual([e[0] for e in self.events if e[1] == 'start'], ['claim_extract', 'claim_group', 'order'])

    def test_full_pipeline_off_on_stage_order_and_original_judge(self):
        words = [dict(w=r['text'], s=r['start'], e=r['end']) for r in self.rows]
        judge_prompts = []
        for enabled in (False, True):
            self.calls.clear(); self.events.clear()
            work = self.root / str(enabled)
            with patch.object(pipeline, 'ai_call', side_effect=self.fake_ai), patch.object(pipeline, 'render_video'):
                manifest = pipeline.run_pipeline('source.mp4', str(work), transcript=([], words),
                    target_seconds=(5, 8), plugin_config={'enabled': enabled},
                    on_stage=lambda *e: self.events.append(e))
            judge_prompts.append(self.calls[0][0])
            expected = ['asr', 'filter', 'judge'] + (['claim_extract', 'claim_group'] if enabled else []) + ['order', 'render']
            self.assertEqual([e[0] for e in self.events if e[1] == 'start'], expected)
            self.assertEqual(manifest['ai_calls'], 4 if enabled else 2)
        self.assertEqual(judge_prompts[0], judge_prompts[1])

    def test_store_nodes_are_optional_version_isolated_and_survive_restart(self):
        store = Store(self.root / 'data/agent.db')
        def create(version='v1', kind='direct'):
            return store.create_job(title='test', source_path='source.mp4', workspace=str(self.work),
                pipeline_version=version, job_type=kind)
        plain = create()
        original = [s['stage_id'] for s in store.get_job(plain)['stages']]
        self.assertEqual(original, [s[0] for s in STAGE_DEFINITIONS])
        rule_pipeline.save_setting(self.settings, 'semantic_groups', {'enabled': True})
        extended = create()
        self.assertEqual([s['stage_id'] for s in store.get_job(extended)['stages']],
            ['asr', 'filter', 'judge', 'claim_extract', 'claim_group', 'order', 'render'])
        for version, kind in [('v2', 'direct'), ('v3', 'direct'), ('v1', 'remix')]:
            self.assertFalse(PLUGIN_IDS.intersection(s['stage_id'] for s in store.get_job(create(version, kind))['stages']))
        reopened = Store(store.path)
        self.assertIsNotNone(reopened.get_job(extended))
        store.configure_plugin_stages(extended, False)
        self.assertEqual([s['stage_id'] for s in store.get_job(extended)['stages']], original)

    def test_plugin_rerun_resets_only_downstream_and_uses_current_config(self):
        self.write_upstream()
        rule_pipeline.save_setting(self.settings, 'semantic_groups', {'enabled': True})
        store = Store(self.root / 'data/agent.db')
        jid = store.create_job(title='test', source_path='source.mp4', workspace=str(self.work))
        for s in store.get_job(jid)['stages']:
            store.stage_done(jid, s['stage_id'], 'done')
        store.update_job(jid, status='completed')
        runner = JobRunner(store, self.root)
        runner.rerun_stage(jid, 'claim_extract')
        stages = {s['stage_id']: s for s in store.get_job(jid)['stages']}
        self.assertEqual(stages['judge']['status'], 'succeeded')
        self.assertEqual(stages['claim_extract']['status'], 'pending')
        self.assertEqual(stages['order']['status'], 'pending')
        self.assertTrue(json.loads((self.work / 'plugins.snapshot.json').read_text())['enabled'])
        store.update_job(jid, status='completed')
        rule_pipeline.save_setting(self.settings, 'semantic_groups', {'enabled': False})
        with self.assertRaises(ValueError):
            runner.rerun_stage(jid, 'claim_group')
        runner.rerun_stage(jid, 'order')
        self.assertFalse(PLUGIN_IDS.intersection(s['stage_id'] for s in store.get_job(jid)['stages']))


if __name__ == '__main__':
    unittest.main()
