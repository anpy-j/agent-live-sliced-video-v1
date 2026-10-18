"""Model failover with fake transports only; no paid requests or user jobs."""
import importlib
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from agent_video.ai_fallback import normalize_fallbacks, set_reporter
from agent_video.db import Store
from agent_video.runner import JobRunner
from agent_video.server import Application

BACKUPS = [{'provider': 'codex', 'model': 'gpt-fixture'},
           {'provider': 'api', 'model': 'api-fixture'}]
ENV = {'PIPELINE_AI_ENGINE': 'llm', 'PIPELINE_AI_PROVIDER': 'workbuddy',
       'PIPELINE_AI_MODEL': 'primary', 'PIPELINE_AI_FALLBACKS': json.dumps(BACKUPS)}


class ModelFallbackTest(unittest.TestCase):
    def tearDown(self):
        set_reporter(None)

    def test_cross_provider_order_and_audit_for_v1_and_v3(self):
        for version in ('pipeline', 'pipeline_v3'):
            ai = importlib.import_module('agent_video.' + version + '.ai')
            events = []
            set_reporter(events.append)
            with patch.dict(os.environ, ENV, clear=True), patch.object(
                    ai, '_call_llm', side_effect=[ai.AIReturnError('insufficient balance'),
                                                 TimeoutError('timeout'), {'decisions': []}]) as call:
                self.assertEqual(ai.ai_call('primary', 'same prompt', ai.DECISION_SCHEMA, 7), {'decisions': []})
                self.assertEqual([c.kwargs['provider'] for c in call.call_args_list], ['workbuddy', 'codex', 'api'])
                self.assertEqual([c.args[0] for c in call.call_args_list], ['primary', 'gpt-fixture', 'api-fixture'])
                self.assertTrue(all(c.args[1:] == ('same prompt', ai.DECISION_SCHEMA, 7) for c in call.call_args_list))
                self.assertEqual(os.environ['PIPELINE_AI_PROVIDER'], 'workbuddy')
            self.assertEqual([e['status'] for e in events], ['switching', 'switching', 'recovered'])
            self.assertEqual(events[-1]['selected']['provider'], 'api')

    def test_primary_success_does_not_call_backups(self):
        from agent_video.pipeline import ai
        with patch.dict(os.environ, ENV), patch.object(ai, '_call_llm', return_value={'decisions': []}) as call:
            ai.ai_call('primary', 'p', ai.DECISION_SCHEMA)
            call.assert_called_once()

    def test_order_schema_failure_switches_model(self):
        for version in ('pipeline', 'pipeline_v3'):
            ai = importlib.import_module('agent_video.' + version + '.ai')
            valid = {'main_product': 'fixture', 'sections': [{'role': 'hook', 'ids': [0]}], 'ordered_ids': [0]}
            with patch.dict(os.environ, ENV), patch.object(ai, '_call_llm', side_effect=[{'bad': True}, valid]) as call:
                self.assertEqual(ai.ai_call('primary', 'p', ai.ORDER_SCHEMA), valid)
                self.assertEqual(call.call_count, 2)

    def test_all_failures_stop_and_disabled_preserves_error(self):
        from agent_video.pipeline import ai
        with patch.dict(os.environ, ENV), patch.object(ai, '_call_llm', side_effect=ai.AIReturnError('HTTP 402')) as call:
            with self.assertRaisesRegex(ai.AIReturnError, '主模型及备用模型均调用失败'):
                ai.ai_call('primary', 'p', ai.DECISION_SCHEMA)
            self.assertEqual(call.call_count, 3)
        with patch.dict(os.environ, {**ENV, 'PIPELINE_AI_FALLBACKS': '[]'}), patch.object(
                ai, '_call_llm', side_effect=ai.AIReturnError('original')) as call:
            with self.assertRaisesRegex(ai.AIReturnError, '^original$'):
                ai.ai_call('primary', 'p', ai.DECISION_SCHEMA)
            call.assert_called_once()

    def test_duplicate_primary_is_not_retried_and_parallel_calls_do_not_mutate_env(self):
        from agent_video.pipeline import ai
        backups = [{'provider': 'workbuddy', 'model': 'primary'}, BACKUPS[0]]
        def invoke(model, prompt, schema, timeout, *, provider):
            self.assertEqual(os.environ['PIPELINE_AI_PROVIDER'], 'workbuddy')
            if provider == 'workbuddy':
                raise ai.AIReturnError('quota')
            self.assertEqual((provider, model), ('codex', 'gpt-fixture'))
            return {'decisions': []}
        with patch.dict(os.environ, {**ENV, 'PIPELINE_AI_FALLBACKS': json.dumps(backups)}), patch.object(ai, '_call_llm', side_effect=invoke) as call:
            with ThreadPoolExecutor(max_workers=4) as pool:
                self.assertEqual(list(pool.map(lambda _: ai.ai_call('primary', 'p', ai.DECISION_SCHEMA), range(4))), [{'decisions': []}] * 4)
            self.assertEqual(call.call_count, 8)

    def test_exhausted_s3_chain_does_not_repeat_under_batch_retries(self):
        for version in ('pipeline', 'pipeline_v3'):
            pipeline = importlib.import_module('agent_video.' + version + '.run')
            ai = importlib.import_module('agent_video.' + version + '.ai')
            batch = [{'id': 0, 'members': [{'id': 0, 'text': 'fixture'}]}]
            with patch.dict(os.environ, ENV), patch.object(ai, '_call_llm', side_effect=ai.AIReturnError('quota')) as call:
                with self.assertRaisesRegex(ai.AIReturnError, '主模型及备用模型均调用失败'):
                    pipeline._run_judge_batches('primary', [batch], None, 5, retries=4)
                self.assertEqual(call.call_count, 3)

    def test_s3_missing_ids_also_switches_and_context_is_reset(self):
        for version in ('pipeline', 'pipeline_v3'):
            pipeline = importlib.import_module('agent_video.' + version + '.run')
            ai = importlib.import_module('agent_video.' + version + '.ai')
            batch = [{'id': 0, 'members': [{'id': 0, 'text': 'fixture'}]}]
            valid = {'decisions': [{'id': 0, 'usable': True, 'reason': 'ok'}]}
            with patch.dict(os.environ, ENV), patch.object(ai, '_call_llm', side_effect=[{'decisions': []}, valid]) as call:
                decisions, _ = pipeline._run_judge_batches('primary', [batch], None, 5)
                self.assertEqual(set(decisions), {0})
                self.assertEqual(call.call_count, 2)
            # A later unrelated request must not retain the batch's expected IDs.
            with patch.dict(os.environ, ENV), patch.object(ai, '_call_llm', return_value={'decisions': []}) as call:
                ai.ai_call('primary', 'p', ai.DECISION_SCHEMA)
                call.assert_called_once()

    def test_api_fallback_uses_api_transport_and_selected_model(self):
        for version in ('pipeline', 'pipeline_v3'):
            ai = importlib.import_module('agent_video.' + version + '.ai')
            with patch.dict(os.environ, {**ENV, 'PIPELINE_AI_FALLBACKS': json.dumps([BACKUPS[1]]),
                                        'LIVE_CUT_API_KEY': 'fixture-secret'}), patch.object(
                    ai, 'resolve_provider', side_effect=ai.PipelineConfigError('missing CLI')), patch(
                    'agent_video.api_transport.json_completion', return_value=({'decisions': []}, {})) as transport:
                self.assertEqual(ai.ai_call('primary', 'p', ai.DECISION_SCHEMA), {'decisions': []})
                self.assertEqual(transport.call_args.kwargs['model'], 'api-fixture')
                self.assertEqual(transport.call_args.kwargs['api_key'], 'fixture-secret')

    def test_v2_attempts_keep_usage_and_stop_at_application_budget(self):
        from agent_video.pipeline_v2.ai import Calls, BudgetExceeded
        from agent_video.pipeline_v2.contracts import REVIEW
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, ENV):
            fn = Mock(side_effect=[RuntimeError('quota'), {'data': {'issues': []}, 'usage': {'input_tokens': 4, 'output_tokens': 2}}])
            calls = Calls(Path(temp), 'primary', caller=fn)
            self.assertEqual(calls.call('judge', 'batch', 'p', REVIEW), {'issues': []})
            ledger = json.loads((Path(temp) / 'usage.json').read_text(encoding='utf-8'))
            self.assertEqual(ledger['total_calls'], 2)
            self.assertEqual(ledger['output_tokens'], 2)
            self.assertEqual([c['provider'] for c in ledger['calls']], ['workbuddy', 'codex'])
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {**ENV, 'PIPELINE_V2_MAX_CALLS': '1'}):
            fn = Mock(side_effect=RuntimeError('quota'))
            with self.assertRaises(BudgetExceeded):
                Calls(Path(temp), 'primary', caller=fn).call('order', 'plan', 'p', REVIEW)
            fn.assert_called_once()


class FallbackSettingsTest(unittest.TestCase):
    def test_settings_roundtrip_validation_and_no_partial_save(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp))
            self.assertEqual(app.settings()['ai_fallbacks'], [])
            self.assertEqual(app.update_settings({'ai_fallbacks': BACKUPS})['ai_fallbacks'], BACKUPS)
            for invalid in ({}, [BACKUPS[0], BACKUPS[0]], [{'provider': 'missing', 'model': 'x'}],
                            [{'provider': [], 'model': 'x'}], [{'provider': 'api', 'model': 'auto'}], BACKUPS * 11):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    app.update_settings({'ai_fallbacks': invalid, 'ai_model': 'must-not-save'})
                self.assertNotEqual(app.store.get_setting('ai_model'), 'must-not-save')
                self.assertEqual(app.settings()['ai_fallbacks'], BACKUPS)
            self.assertEqual(app.update_settings({'ai_fallbacks': []})['ai_fallbacks'], [])

    def test_snapshot_freezes_list_loads_api_key_and_restores_environment(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, ENV):
            root = Path(temp)
            store = Store(root / 'test.db')
            vault = Mock(); vault.get.return_value = 'fixture-secret'
            runner = JobRunner(store, root, vault=vault)
            store.set_setting('ai_provider', 'workbuddy'); store.set_setting('ai_model', 'primary')
            store.set_setting('ai_fallbacks', BACKUPS)
            jid = store.create_job(title='test', source_path='fixture', workspace=str(root))
            configuration = runner._refresh_ai_configuration(jid, 'test')
            self.assertEqual(configuration['ai_fallbacks'], BACKUPS)
            store.set_setting('ai_fallbacks', [])
            with runner._ai_environment(store.get_job(jid)):
                self.assertEqual(json.loads(os.environ['PIPELINE_AI_FALLBACKS']), BACKUPS)
                self.assertEqual(os.environ['LIVE_CUT_API_KEY'], 'fixture-secret')
            self.assertNotIn('LIVE_CUT_API_KEY', os.environ)
            runner._refresh_ai_configuration(jid, 'retry')
            with runner._ai_environment(store.get_job(jid)):
                self.assertEqual(os.environ['PIPELINE_AI_FALLBACKS'], '[]')
            self.assertEqual(os.environ['PIPELINE_AI_FALLBACKS'], ENV['PIPELINE_AI_FALLBACKS'])

    def test_old_snapshot_does_not_inherit_new_global_fallbacks(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, ENV):
            store = Store(Path(temp) / 'test.db'); runner = JobRunner(store, Path(temp))
            store.set_setting('ai_fallbacks', BACKUPS)
            with runner._ai_environment({'remote_model_json': json.dumps({'ai_provider': 'codex'})}):
                self.assertEqual(os.environ['PIPELINE_AI_FALLBACKS'], '[]')

    def test_checkpoint_identity_includes_fallback_order(self):
        from agent_video.pipeline.judge_progress import JudgeProgress
        with patch.dict(os.environ, ENV):
            first = JudgeProgress.key('primary', 'p', {})
            os.environ['PIPELINE_AI_FALLBACKS'] = json.dumps(list(reversed(BACKUPS)))
            self.assertNotEqual(JudgeProgress.key('primary', 'p', {}), first)
