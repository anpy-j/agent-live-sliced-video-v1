import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from agent_video.app_runtime import DataLock, configure_desktop
from agent_video.server import Application, ServiceSession
from agent_video.api_transport import json_completion, validate_base_url
from agent_video.pipeline.ai import _call_llm


class MemoryVault:
    def __init__(self): self.values = {}
    def get(self, name): return self.values.get(name, '')
    def set(self, name, value): self.values[name] = value
    def delete(self, name): self.values.pop(name, None)


class DesktopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = (Path(self.tmp.name) / 'resources').resolve()
        self.data = (Path(self.tmp.name) / 'user-data').resolve()
        (self.root / 'web').mkdir(parents=True)
        (self.root / 'web/index.html').write_text('desktop fixture')
        (self.root / 'integrations/skill').mkdir(parents=True)
        (self.root / 'integrations/skill/SKILL.md').write_text('original')
        self.vault = MemoryVault()

    def tearDown(self): self.tmp.cleanup()

    def test_resources_are_read_only_user_data_survives_restart(self):
        with patch.dict(os.environ, {}, clear=False):
            configure_desktop(self.root, self.data)
            app = Application(self.root, self.data, vault=self.vault)
            self.assertEqual(app.web_root, self.root / 'web')
            self.assertEqual(app.store.path, self.data / 'data/agent.db')
            self.assertFalse((self.root / 'data').exists())
            self.assertTrue(app.settings()['engine_path'].startswith(str(self.root)))
            skill = self.data / 'integrations/skill/SKILL.md'
            skill.write_text('user edit')
            configure_desktop(self.root, self.data)
            self.assertEqual(skill.read_text(), 'user edit')
            app.store.set_setting('engine_python', '/old/application/python')
            app.store.set_setting('custom', 'keep')
            self.assertEqual(Application(self.root, self.data, vault=self.vault).store.get_setting('custom'), 'keep')
            self.assertEqual(Application(self.root, self.data, vault=self.vault).store.get_setting('engine_python'), __import__('sys').executable)
            self.assertTrue(str(app.smart.data_root).startswith(str(self.data)))
            self.assertTrue(str(app.viral.data_root).startswith(str(self.data)))

    def test_duplicate_instance_lock(self):
        with DataLock(self.data):
            with self.assertRaises(RuntimeError):
                with DataLock(self.data): pass
        with DataLock(self.data): pass

    def test_key_is_never_returned_or_written_to_database_and_worker_environment_restores(self):
        app = Application(self.root, self.data, vault=self.vault)
        secret = 'test-secret-only-not-a-real-key'
        result = app.ai_access.save({'api_key': secret, 'model': 'test-model', 'base_url': 'https://example.com/v1'})
        self.assertNotIn(secret, json.dumps(result))
        self.assertNotIn(secret.encode(), app.store.path.read_bytes())
        with patch.dict(os.environ, {'LIVE_CUT_API_KEY': 'previous'}):
            with app.runner._ai_environment():
                self.assertEqual(os.environ['LIVE_CUT_API_KEY'], secret)
                self.assertEqual(os.environ['PIPELINE_AI_PROVIDER'], 'api')
            self.assertEqual(os.environ['LIVE_CUT_API_KEY'], 'previous')
        app.ai_access.save({'model': 'new-model', 'base_url': 'https://example.com/v1'})
        self.assertEqual(self.vault.get('model-api-key'), secret)
        app.ai_access.clear_key()
        self.assertFalse(app.ai_access.configuration()['api_key_configured'])

    def test_service_session_serves_resources_and_releases_port_and_lock(self):
        with ServiceSession(self.root, port=0, data_root=self.data, vault=self.vault) as session:
            address = session.server.server_address
            with urllib.request.urlopen(session.url) as response:
                self.assertEqual(response.read(), b'desktop fixture')
            request = urllib.request.Request(session.url + '/api/ai/config', data=b'{}',
                headers={'Content-Type': 'application/json', 'Origin': 'https://other.example'})
            with self.assertRaises(urllib.error.HTTPError) as result: urllib.request.urlopen(request)
            self.assertEqual(result.exception.code, 403)
        with ServiceSession(self.root, port=address[1], data_root=self.data, vault=self.vault): pass

    def test_codex_login_is_background_and_cancel_terminates_owned_process(self):
        app = Application(self.root, self.data, vault=self.vault)
        with patch('agent_video.ai_access.shutil.which', return_value='/fake/codex'), \
             patch('agent_video.ai_access.subprocess.Popen') as spawn, \
             patch('agent_video.ai_access.threading.Thread'):
            spawn.return_value.poll.return_value = None
            result = app.ai_access.login()
            self.assertEqual(result['status'], 'waiting')
            app.ai_access.login()
            self.assertEqual(spawn.call_count, 1)
            with patch.object(app.ai_access, '_terminate') as terminate:
                app.ai_access.cancel()
                terminate.assert_called_once_with(spawn.return_value)


class ApiTransportTest(unittest.TestCase):
    def test_api_transport_and_pipeline_use_supplied_endpoint_and_key(self):
        captured = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_POST(self):
                captured.append((self.path, self.headers.get('Authorization'), json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()
                self.wfile.write(json.dumps({'choices': [{'message': {'content': '{"ok": true}'}}]}).encode())
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        base = f'http://127.0.0.1:{server.server_port}/v1'
        try:
            result, _ = json_completion(base_url=base, api_key='fixture', model='model', prompt='hello')
            self.assertEqual(result, {'ok': True})
            with patch.dict(os.environ, {'PIPELINE_AI_PROVIDER': 'api', 'LIVE_CUT_API_BASE_URL': base, 'LIVE_CUT_API_KEY': 'fixture'}):
                self.assertEqual(_call_llm('model', 'hi', {'required': ['ok']}, 5), {'ok': True})
            self.assertEqual(captured[0][0], '/v1/chat/completions')
            self.assertEqual(captured[0][1], 'Bearer fixture')
        finally: server.shutdown(); server.server_close(); thread.join()

    def test_url_validation_and_http_errors_do_not_expose_secrets(self):
        for url in ['ftp://example.com', 'https://secret@example.com', 'https://example.com?q=secret', 'http://example.com']:
            with self.assertRaises(ValueError): validate_base_url(url)
        self.assertEqual(validate_base_url('http://localhost:123/v1/'), 'http://localhost:123/v1')
        with patch('agent_video.api_transport.urllib.request.OpenerDirector.open', side_effect=urllib.error.HTTPError('https://fixture', 401, 'secret', {}, None)):
            with self.assertRaisesRegex(RuntimeError, 'HTTP 401') as result:
                json_completion(base_url='https://fixture', api_key='secret', model='model', prompt='hi')
            self.assertNotIn('secret', str(result.exception))
