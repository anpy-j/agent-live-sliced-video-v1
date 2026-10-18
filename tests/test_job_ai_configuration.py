"""Manual attempts adopt current settings; running/resumed attempts stay consistent."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from agent_video.db import Store
from agent_video.runner import JobRunner


class JobAIConfigurationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.store = Store(self.root / 'test.db'); self.runner = JobRunner(self.store, self.root)
        for key, value in {'ai_engine':'llm','ai_provider':'workbuddy','ai_model':'hy3'}.items():
            self.store.set_setting(key, value)

    def tearDown(self):
        self.temp.cleanup()

    def job(self, version='v1'):
        workspace = self.root / version; workspace.mkdir(exist_ok=True)
        jid = self.store.create_job(title=version, source_path='fixture', workspace=str(workspace), pipeline_version=version)
        self.store.update_job(jid, status='failed', current_stage='judge', remote_model_json=json.dumps({'ai_engine':'llm','ai_provider':'auto','ai_model':'auto'}))
        (workspace/'clauses.json').write_text('{}')
        (workspace/'clauses.filtered.json').write_text('{}')
        return jid, workspace

    def assert_workbuddy(self, jid):
        job=self.store.get_job(jid); config=json.loads(job['remote_model_json'])
        self.assertEqual(config, {'ai_engine':'llm','ai_provider':'workbuddy','ai_model':'hy3','ai_fallbacks':[]})
        with patch.dict(os.environ, {'PIPELINE_AI_PROVIDER':'opencode','PIPELINE_AI_MODEL':'old-model'}):
            with self.runner._ai_environment(job):
                self.assertEqual(os.environ['PIPELINE_AI_PROVIDER'],'workbuddy')
                self.assertEqual(os.environ['PIPELINE_AI_MODEL'],'hy3')
            self.assertEqual(os.environ['PIPELINE_AI_PROVIDER'],'opencode')

    def test_retry_uses_current_provider_and_model_for_all_three_versions(self):
        for version in ('v1','v2','v3'):
            jid, workspace=self.job(version)
            original=(workspace/'clauses.json').read_bytes()
            self.runner.retry(jid)
            self.assert_workbuddy(jid)
            self.assertEqual((workspace/'clauses.json').read_bytes(),original)
            self.assertEqual(self.store.get_job(jid)['pipeline_version'],version)

    def test_single_stage_rerun_uses_current_configuration(self):
        for version in ('v1','v2','v3'):
            jid,_=self.job(version);self.runner.rerun_stage(jid,'judge')
            self.assert_workbuddy(jid)

    def test_restart_uses_current_configuration(self):
        jid,_=self.job();self.runner.restart(jid)
        self.assert_workbuddy(jid)

    def test_resume_keeps_the_existing_attempt_configuration(self):
        jid,_=self.job();self.store.update_job(jid,status='paused',run_stage='judge')
        self.runner.resume(jid)
        config=json.loads(self.store.get_job(jid)['remote_model_json'])
        self.assertEqual(config['ai_provider'],'auto')

    def test_configuration_change_keeps_previous_snapshot_in_audit(self):
        jid,_=self.job();self.runner._refresh_ai_configuration(jid,'fixture')
        events=[e for e in self.store.get_job(jid)['events'] if e['kind']=='ai_configuration_updated']
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]['payload']['previous']['ai_provider'],'auto')
        self.assertEqual(events[0]['payload']['current']['ai_provider'],'workbuddy')
        self.runner._refresh_ai_configuration(jid,'same')
        self.assertEqual(len([e for e in self.store.get_job(jid)['events'] if e['kind']=='ai_configuration_updated']),1)

    def test_invalid_rerun_does_not_change_configuration(self):
        jid,_=self.job();old=self.store.get_job(jid)['remote_model_json']
        with self.assertRaises(ValueError):self.runner.rerun_stage(jid,'render')
        self.assertEqual(self.store.get_job(jid)['remote_model_json'],old)

    def test_changed_global_settings_do_not_change_running_attempt_environment(self):
        jid,_=self.job();self.runner._refresh_ai_configuration(jid,'first')
        self.store.set_setting('ai_provider','codex');self.store.set_setting('ai_model','new-model')
        with self.runner._ai_environment(self.store.get_job(jid)):
            self.assertEqual(os.environ['PIPELINE_AI_PROVIDER'],'workbuddy')
            self.assertEqual(os.environ['PIPELINE_AI_MODEL'],'hy3')
