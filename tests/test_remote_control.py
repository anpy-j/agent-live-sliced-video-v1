import json
import os
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent_video.db import Store
from agent_video.runner import JobRunner, JobPaused, _run_pipeline_child
from agent_video.remote_control import RemoteConnector


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root/'agent.db')
        self.job = self.store.create_job(title='task', source_path=str(self.root/'source.mp4'), workspace=str(self.root/'workspace'))
        self.runner = JobRunner(self.store, self.root)
        self.app = SimpleNamespace(root=self.root, store=self.store, runner=self.runner,
            settings=lambda: {'ai_models': {'codex': [{'id': 'model', 'name': 'model'}]}})
        self.connector = RemoteConnector(self.app, 'http://relay.example', 'pc')
        self.connector.models = {'codex': [{'id': 'model'}]}

    def tearDown(self):
        self.temp.cleanup()

    def test_http_environment_needs_no_credentials(self):
        with patch.dict(os.environ, {'LIVE_CUT_REMOTE_URL': 'http://relay.example',
                                    'LIVE_CUT_DEVICE_ID': 'pc'}, clear=True):
            connector = RemoteConnector.from_environment(self.app)
            self.assertEqual(connector.url, 'http://relay.example')

    def test_queued_pause_resume_and_restart_persistence(self):
        self.runner.enqueue(self.job)
        self.runner.pause(self.job)
        self.assertEqual(self.store.get_job(self.job)['status'], 'paused')
        self.assertEqual(self.runner._queue, [])
        self.assertEqual(self.store.list_recoverable_jobs(), [])
        fresh_runner = JobRunner(self.store, self.root)
        fresh_runner.resume(self.job)
        self.assertEqual(fresh_runner._queue, [self.job])

    def test_running_pause_sets_boundary_event(self):
        self.store.update_job(self.job, status='running')
        self.runner._current = self.job
        self.runner._pause_event = threading.Event()
        self.runner.pause(self.job)
        self.assertTrue(self.runner._pause_event.is_set())
        self.assertEqual(self.store.get_job(self.job)['status'], 'pausing')

    def test_child_stops_before_next_stage(self):
        event = threading.Event()
        messages = queue.Queue()
        def pipeline(*args, on_stage, **kwargs):
            on_stage('asr', 'start', 'start')
            on_stage('asr', 'done', 'saved')
            event.set()
            on_stage('filter', 'start', 'must not run')
            self.fail('next stage ran after pause')
        with patch('agent_video.runner._windows_kill_on_close_job'), patch('agent_video.runner.os.setsid', create=True), \
             patch('agent_video.runner.run_pipeline', side_effect=pipeline):
            _run_pipeline_child(messages, 'source', str(self.root), (70,90), pause_event=event)
        output = []
        while not messages.empty():
            output.append(messages.get())
        self.assertEqual(output[-1], ('paused', 'filter'))
        self.assertEqual(len(output), 3)

    def test_model_is_task_scoped_and_validated(self):
        self.store.set_setting('ai_model', 'original')
        command = {'job_id': self.job, 'action': 'set-model', 'provider': 'codex', 'model': 'model'}
        self.store.update_job(self.job, status='running')
        with self.assertRaises(ValueError):
            self.connector.execute(command)
        self.store.update_job(self.job, status='paused')
        self.connector.execute(command)
        job = self.store.get_job(self.job)
        self.assertEqual(self.store.get_setting('ai_model'), 'original')
        with self.runner._ai_environment(job):
            self.assertEqual(os.environ['PIPELINE_AI_MODEL'], 'model')
        self.assertNotEqual(os.environ.get('PIPELINE_AI_MODEL'), 'model')
        with self.assertRaises(ValueError):
            self.connector.execute(dict(command, model='unknown'))

    def test_duplicate_command_not_executed_and_crash_not_replayed(self):
        command = {'id': 'operation', 'job_id': self.job, 'action': 'pause', 'expires': 9999999999}
        self.runner.enqueue(self.job)
        with patch.object(self.connector, 'execute', wraps=self.connector.execute) as execute:
            self.connector.accept(command)
            self.connector.accept(command)
            self.assertEqual(execute.call_count, 1)
        with self.connector.journal() as con:
            con.execute("INSERT INTO receipts(id,status,result) VALUES ('interrupted','running','{}')")
        restarted = RemoteConnector(self.app, 'http://relay.example', 'pc')
        with restarted.journal() as con:
            self.assertEqual(con.execute("SELECT status FROM receipts WHERE id='interrupted'").fetchone()[0], 'unknown')

    def test_snapshot_has_no_tokens_paths_or_payloads(self):
        self.store.set_setting('jev_api_key', 'secret-api-key')
        snapshot = json.dumps(self.connector.snapshot())
        self.assertNotIn('secret-api-key', snapshot)
        self.assertNotIn('source_path', snapshot)
        self.assertNotIn('mcp_token', snapshot)


if __name__ == '__main__':
    unittest.main()
