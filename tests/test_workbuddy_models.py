"""Portable model discovery without real accounts, paid calls, or client edits."""
import json
import os
import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_video import ai
from agent_video.workbuddy_models import desktop_model_context, model_environment


class ModelDirectoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.client = self.root / 'workbuddy'; self.data = self.root / 'livecut'
        self.environment = patch.dict(os.environ, {'WORKBUDDY_CONFIG_DIR': str(self.client)}, clear=True)
        self.environment.start(); self.addCleanup(self.environment.stop)
        data = patch('agent_video.workbuddy_models.user_data_dir', return_value=self.data)
        data.start(); self.addCleanup(data.stop)
        ai._workbuddy_catalog_cache.clear(); ai._workbuddy_catalog_metadata.clear()
        self.account = self.client / 'storage/skeleton/account-snapshot.json'
        self.account.parent.mkdir(parents=True)
        self.account.write_text(json.dumps({'primary': {'uid': 'current', 'type': 'personal'}}))
        self.snapshot = self.client / 'storage/user-current-personal/scoped/fixture/wb-product-config.json'
        self.snapshot.parent.mkdir(parents=True)
        self.write_snapshot()
        self.entry = self.root / 'cli/bin/codebuddy'; self.entry.parent.mkdir(parents=True)
        self.entry.write_text('#!/usr/bin/env node\n'); self.entry.chmod(0o700)

    def tearDown(self):
        self.temp.cleanup()

    def write_snapshot(self, model_id='new-model'):
        models = [{'id': model_id, 'name': 'New Model', 'supportsToolCall': True, 'supportsImages': True},
                  {'id': 'not-enabled', 'supportsToolCall': True},
                  {'id': 'image-only', 'supportsToolCall': False}]
        data = {'models': models, 'agents': [{'name': 'cli', 'models': [model_id, 'image-only'],
                                             'tags': ['default'], 'commands': ['unrelated']}],
                'authentication': {'token': 'never-copy'}, 'endpoint': 'never-copy'}
        self.snapshot.write_text(json.dumps({'product-config-snapshot': {'savedAt': 1, 'data': data}}))

    def test_active_client_model_allowlist_excludes_other_models(self):
        overlay, _ = desktop_model_context()
        self.assertEqual([m['id'] for m in overlay['models']], ['auto', 'new-model'])
        self.assertEqual(overlay['agents'][0]['models'], ['auto', 'new-model'])
        self.assertEqual(overlay['agents'][0]['tags'], ['default'])
        self.assertNotIn('commands', overlay['agents'][0])
        self.assertNotIn('authentication', overlay); self.assertNotIn('endpoint', overlay)

    def test_newer_other_account_is_never_selected(self):
        other = self.client / 'storage/user-other-personal/scoped/fixture/wb-product-config.json'
        other.parent.mkdir(parents=True); other.write_text(self.snapshot.read_text().replace('new-model', 'other-model'))
        self.assertEqual([m['id'] for m in desktop_model_context()[0]['models']], ['auto', 'new-model'])

    def test_ambiguous_accounts_without_active_snapshot_are_not_merged(self):
        self.account.unlink()
        other = self.client / 'storage/user-other'; other.mkdir()
        self.assertEqual(desktop_model_context()[0], {})

    def test_account_switch_invalidates_model_fingerprint(self):
        old = desktop_model_context()[1]
        self.write_snapshot('newer-model')
        self.assertNotEqual(old, desktop_model_context()[1])
        self.assertEqual([m['id'] for m in desktop_model_context()[0]['models']], ['auto', 'newer-model'])

    def test_overlay_is_private_and_original_snapshot_is_unchanged(self):
        before = self.snapshot.read_bytes()
        env = model_environment(); file = Path(env['ACC_PRODUCT_CONFIG_PATH'])
        data = json.loads(file.read_text())
        self.assertEqual(data, desktop_model_context()[0])
        self.assertNotIn('never-copy', file.read_text())
        if os.name != 'nt':
            self.assertEqual(file.stat().st_mode & 0o077, 0)
        self.assertEqual(self.snapshot.read_bytes(), before)
        self.assertEqual(model_environment(), env)

    def test_explicit_operator_configuration_is_respected(self):
        with patch.dict(os.environ, {'ACC_PRODUCT_CONFIG_PATH': '/operator/config.json'}):
            self.assertEqual(model_environment(), {})
            self.assertEqual(desktop_model_context()[0], {})

    def test_broken_snapshot_falls_back_without_crashing(self):
        self.snapshot.write_text('invalid-json')
        self.assertEqual(desktop_model_context()[0], {})

    def test_cli_help_uses_the_same_overlay_as_generation(self):
        result = subprocess.CompletedProcess([], 0, 'Currently supported: (auto, new-model)', '')
        with patch('agent_video.ai.subprocess.run', return_value=result) as run:
            models = ai.workbuddy_model_catalog(self.entry)
        self.assertEqual(models, [('auto', '自动选择'), ('new-model', 'New Model')])
        path = run.call_args.kwargs['env']['ACC_PRODUCT_CONFIG_PATH']
        self.assertEqual(ai.WorkBuddyCli(self.entry).runtime_environment()['ACC_PRODUCT_CONFIG_PATH'], path)
        self.assertEqual(ai.workbuddy_catalog_info()['source'], 'cli_desktop')

    def test_catalog_cache_refresh_and_snapshot_changes(self):
        result = subprocess.CompletedProcess([], 0, 'Currently supported: (auto, new-model)', '')
        with patch('agent_video.ai.subprocess.run', return_value=result) as run:
            ai.workbuddy_model_catalog(self.entry); ai.workbuddy_model_catalog(self.entry)
            self.assertEqual(run.call_count, 1)
            ai.workbuddy_model_catalog(self.entry, refresh=True)
            self.assertEqual(run.call_count, 2)
            self.write_snapshot('newer-model')
            ai.workbuddy_model_catalog(self.entry)
            self.assertEqual(run.call_count, 3)

    def test_failed_cli_returns_explicit_unconfirmed_client_directory(self):
        with patch('agent_video.ai.subprocess.run', side_effect=subprocess.TimeoutExpired('cli', 1)):
            models = ai.workbuddy_model_catalog(self.entry)
        self.assertIn(('new-model', 'New Model'), models)
        self.assertEqual(ai.workbuddy_catalog_info()['source'], 'desktop')
        self.assertFalse(ai.workbuddy_catalog_info()['confirmed'])

    def test_no_snapshot_or_cli_reports_reference_catalog(self):
        self.snapshot.write_text('{}')
        with patch('agent_video.ai._workbuddy_cli_entries', return_value=[]), patch('agent_video.ai._workbuddy_cli_roots', return_value=[]):
            models = ai.workbuddy_model_catalog()
        self.assertEqual(models, ai.WORKBUDDY_MODELS)
        self.assertEqual(ai.workbuddy_catalog_info()['source'], 'builtin')

    def test_generation_passes_overlay_without_global_environment_changes(self):
        provider = ai.WorkBuddyCli(self.entry)
        with patch.object(provider, '_ensure_available'), patch.object(provider, 'validate_model'), \
             patch.object(provider, '_complete', return_value=('{"decisions":[]}', '', 0)) as complete:
            provider.generate_json(model='new-model', prompt='fixture', schema={'required': ['decisions']}, cwd=self.root)
        self.assertIn('ACC_PRODUCT_CONFIG_PATH', complete.call_args.kwargs['env_overrides'])
        self.assertNotIn('ACC_PRODUCT_CONFIG_PATH', os.environ)

    def test_windows_localappdata_install_is_found(self):
        base = self.root / 'custom-local'
        entry = base / 'Programs/WorkBuddy/resources/app.asar.unpacked/cli/bin/codebuddy'
        entry.parent.mkdir(parents=True); entry.write_text('')
        with patch.dict(os.environ, {'LOCALAPPDATA': str(base)}), patch('agent_video.ai.sys.platform', 'win32'), patch('agent_video.ai.shutil.which', return_value=None):
            self.assertIn(entry, ai._workbuddy_cli_entries())

    def test_explicit_cli_path_is_shared_with_v1_and_v3(self):
        with patch.dict(os.environ, {'WORKBUDDY_CLI': str(self.entry)}):
            from agent_video.pipeline.ai import _provider_executable as v1
            from agent_video.pipeline_v3.ai import _provider_executable as v3
            self.assertEqual(v1('workbuddy'), str(self.entry))
            self.assertEqual(v3('workbuddy'), str(self.entry))

    def test_managed_node_works_without_node_on_path(self):
        node = self.client / 'binaries/node/versions/22/bin/node'
        node.parent.mkdir(parents=True); node.write_text('fixture')
        with patch('agent_video.ai.shutil.which', return_value=None):
            self.assertEqual(ai._workbuddy_node(), str(node))

    def test_renamed_mac_app_is_discovered_by_bundle_identifier(self):
        app = self.root / 'Renamed.app'
        entry = app / 'Contents/Resources/app.asar.unpacked/cli/bin/codebuddy'
        entry.parent.mkdir(parents=True); entry.write_text('')
        with (app / 'Contents/Info.plist').open('wb') as stream:
            plistlib.dump({'CFBundleIdentifier': 'com.tencent.workbuddy.mac'}, stream)
        original_glob = Path.glob
        def glob(path, pattern):
            if str(path) == '/Applications': return iter([app]) if pattern == '*.app' else iter([])
            return original_glob(path, pattern)
        with patch('agent_video.ai.sys.platform', 'darwin'), patch.object(Path, 'glob', glob), patch('agent_video.ai.shutil.which', return_value=None):
            self.assertIn(entry, ai._workbuddy_cli_entries())
