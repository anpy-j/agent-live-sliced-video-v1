"""Three-version routing, isolation, and job lifetime regressions."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent_video.db import Store
from agent_video.server import Application
from agent_video.runner import JobRunner, _run_pipeline_child
from agent_video.pipeline_versions import normalize_version
from agent_video.pipeline import run as v1
from agent_video.pipeline_v3 import run as v3
from agent_video.pipeline.filter import activate_account_vocab as activate_v1
from agent_video.pipeline_v3.filter import activate_account_vocab as activate_v3
from agent_video.engine.scripts import badvocab as vocab_v1
from agent_video.pipeline_v3 import badvocab as vocab_v3


class VersionsTest(unittest.TestCase):
    def test_all_versions_and_invalid_versions(self):
        self.assertEqual(normalize_version(), 'v1')
        for version in ('v1', 'v2', 'v3'):
            self.assertEqual(normalize_version(version.upper()), version)
        with self.assertRaises(ValueError):
            normalize_version('v4')

    def test_v1_v3_vocab_remain_independent_in_both_orders(self):
        for first, second in ((activate_v1, activate_v3), (activate_v3, activate_v1)):
            first(); second()
            self.assertIsNone(vocab_v1.BAD_RE.search('原版材质'))
            self.assertIsNotNone(vocab_v3.BAD_RE.search('原版材质'))
        vocab_v3.set_profile({'hard_add': ['独立测试词']}, None)
        self.assertIsNone(vocab_v1.BAD_RE.search('独立测试词'))
        vocab_v3.set_profile({}, None)
        activate_v3()

    def test_v1_and_v3_accept_duration_outside_target(self):
        candidates = [{'id': 0, 'start': 0, 'end': 2, 'text': '面料柔软'}]
        order = {'main_product': '马甲', 'ordered_ids': [0],
                 'sections': [{'role': 'hook', 'ids': [0]}]}
        self.assertEqual(v1._validate_order(order, candidates, (70, 90), 1)[2], 2)
        self.assertEqual(v3._validate_order(order, candidates, (70, 90), 1)[2], 2)

    def test_v1_requires_hook_while_v3_accepts_selling_point(self):
        candidates = [{'id': 0, 'start': 0, 'end': 2, 'text': '面料柔软'}]
        order = {'main_product': '马甲', 'ordered_ids': [0],
                 'sections': [{'role': 'selling_point', 'ids': [0]}]}
        with self.assertRaises(v1.AIReturnError):
            v1._validate_order(order, candidates, (1, 3), 1)
        v3._validate_order(order, candidates, (1, 3), 1)

    def test_api_and_database_create_three_fixed_versions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'fixture.mp4'; source.write_bytes(b'fixture')
            app = Application(root)
            for version in ('v1', 'v2', 'v3'):
                job = app.create_job({'source_path': str(source), 'pipeline_version': version})
                self.assertEqual(job['pipeline_version'], version)
                self.assertEqual(len(job['stages']), 6 if version == 'v2' else 5)
                with self.assertRaises(ValueError):
                    app.store.update_job(job['id'], pipeline_version='v2')
                self.assertEqual(app.store.get_job(job['id'])['pipeline_version'], version)
            for version in ('v2', 'v3'):
                with self.assertRaises(ValueError):
                    app.create_job({'job_type': 'remix', 'pipeline_version': version})

    def test_child_routes_each_version_and_each_rerun(self):
        with tempfile.TemporaryDirectory() as temp:
            for version in ('v1', 'v2', 'v3'):
                for stage in (None, 'order'):
                    queue = Mock()
                    with patch('agent_video.runner.os.setsid', create=True), \
                         patch('agent_video.runner.run_pipeline', return_value={}) as full_v1, \
                         patch('agent_video.runner.run_pipeline_stage', return_value={}) as stage_v1, \
                         patch('agent_video.pipeline_v2.run.run_pipeline', return_value={}) as full_v2, \
                         patch('agent_video.pipeline_v3.run.run_pipeline', return_value={}) as full_v3, \
                         patch('agent_video.pipeline_v3.run.run_pipeline_stage', return_value={}) as stage_v3:
                        _run_pipeline_child(queue, 'fixture', temp, (70, 90), stage, pipeline_version=version)
                    expected = full_v2 if version == 'v2' else (stage_v3 if stage else full_v3) if version == 'v3' else (stage_v1 if stage else full_v1)
                    expected.assert_called_once()
                    for fn in (full_v1, stage_v1, full_v2, full_v3, stage_v3):
                        if fn is not expected: fn.assert_not_called()
                    self.assertEqual(queue.put.call_args.args[0][0], 'success')

    def test_v3_pause_resume_and_stage_rerun_preserve_version(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); workspace = root / 'work'; workspace.mkdir()
            store = Store(root / 'test.db')
            jid = store.create_job(title='V3', source_path='fixture', workspace=str(workspace), pipeline_version='v3')
            for stage in ('asr', 'filter', 'judge'):
                store.stage_done(jid, stage, 'done')
            store.update_job(jid, status='paused', run_stage='order')
            runner = JobRunner(store, root); runner.resume(jid)
            job = store.get_job(jid)
            self.assertEqual(job['pipeline_version'], 'v3')
            self.assertEqual(job['run_stage'], 'order')
            self.assertEqual(job['stages'][2]['status'], 'succeeded')
            store.update_job(jid, status='failed')
            (workspace / 'clauses.judged.json').write_text('{}')
            runner.rerun_stage(jid, 'order')
            self.assertEqual(store.get_job(jid)['pipeline_version'], 'v3')

    def test_v3_api_provider_keeps_local_transport_support(self):
        from agent_video.pipeline_v3.ai import _call_llm
        with patch.dict('os.environ', {'PIPELINE_AI_PROVIDER': 'api'}), \
             patch('agent_video.api_transport.json_completion', return_value=({'ok': True}, {})) as call:
            self.assertEqual(_call_llm('fixture', 'prompt', {}, 10), {'ok': True})
            call.assert_called_once()

    def test_segment_exports_select_version_renderer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); runner = JobRunner(Store(root / 'test.db'), root)
            manifest = {'pipeline_version': 'v3', 'segments': [{'id': 0, 'start': 0, 'end': 2}]}
            with patch('agent_video.pipeline_v3.render.render_segment') as modern, patch('agent_video.runner.render_segment') as legacy:
                runner._export_outputs(root, manifest, str(root / 'out'), 'segments', 'V3', 'fixture.mp4')
                modern.assert_called_once(); legacy.assert_not_called()

    def test_v2_rejects_api_without_fallback(self):
        from agent_video.pipeline_v2.ai import Calls
        from agent_video.pipeline.errors import PipelineConfigError
        with tempfile.TemporaryDirectory() as temp, patch.dict('os.environ', {'PIPELINE_AI_PROVIDER': 'api', 'PIPELINE_AI_ENGINE': 'llm'}):
            calls = Calls(Path(temp), 'fixture')
            with patch('agent_video.pipeline_v2.ai.resolve_provider') as resolve:
                with self.assertRaisesRegex(PipelineConfigError, '直接 API'):
                    calls._native('prompt', {}, Path(temp))
                resolve.assert_not_called()
