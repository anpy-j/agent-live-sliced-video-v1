"""Native draft discovery across Windows and macOS."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from agent_video.jianying import default_draft_root, default_export_dir, DEFAULT_EXPORT_DIR
from agent_video.jianying_crypto import JianyingDecryptError, decrypt_jianying_file


class PlatformPathsTest(unittest.TestCase):
    def test_override_wins_on_all_platforms(self):
        for platform in ('darwin', 'win32'):
            with patch('agent_video.jianying.sys.platform', platform), patch.dict(os.environ, {'JIANYING_DRAFT_ROOT': '~/custom-drafts'}):
                self.assertEqual(default_draft_root(), Path.home() / 'custom-drafts')

    def test_mac_movies_registry_without_localappdata(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True), patch('agent_video.jianying.sys.platform', 'darwin'), patch('agent_video.jianying.Path.home', return_value=Path(temp)):
            root = Path(temp) / 'Movies/JianyingPro/User Data/Projects/com.lveditor.draft'
            root.mkdir(parents=True); (root / 'root_meta_info.json').write_text('{}')
            self.assertEqual(default_draft_root(), root)

    def test_mac_prefers_index_over_stale_default_directory(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True), patch('agent_video.jianying.sys.platform', 'darwin'), patch('agent_video.jianying.Path.home', return_value=Path(temp)):
            (Path(temp) / 'Movies/JianyingPro/User Data/Projects/com.lveditor.draft').mkdir(parents=True)
            root = Path(temp) / 'Library/Application Support/JianyingPro/User Data/Projects/com.lveditor.draft'
            root.mkdir(parents=True); (root / 'root_meta_info.json').write_text('{}')
            self.assertEqual(default_draft_root(), root)

    def test_mac_container_registry(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True), patch('agent_video.jianying.sys.platform', 'darwin'), patch('agent_video.jianying.Path.home', return_value=Path(temp)):
            root = Path(temp) / 'Library/Containers/com.lemon.lvpro/Data/Movies/JianyingPro/User Data/Projects/com.lveditor.draft'
            root.mkdir(parents=True); (root / 'root_meta_info.json').write_text('{}')
            self.assertEqual(default_draft_root(), root)

    def test_windows_path_is_unchanged(self):
        with patch.dict(os.environ, {'LOCALAPPDATA': '/fixture/local'}, clear=True), patch('agent_video.jianying.sys.platform', 'win32'):
            self.assertEqual(default_draft_root(), Path('/fixture/local/JianyingPro/User Data/Projects/com.lveditor.draft'))
            self.assertEqual(default_export_dir(), DEFAULT_EXPORT_DIR)

    def test_mac_export_does_not_use_windows_drive(self):
        with patch('agent_video.jianying.sys.platform', 'darwin'), patch('agent_video.jianying.Path.home', return_value=Path('/fixture/home')):
            self.assertEqual(Path(default_export_dir()), Path('/fixture/home/Movies/LiveCut/exports'))

    def test_mac_missing_registry_reports_native_path(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True), patch('agent_video.jianying.sys.platform', 'darwin'), patch('agent_video.jianying.Path.home', return_value=Path(temp)):
            self.assertEqual(default_draft_root(), Path(temp) / 'Movies/JianyingPro/User Data/Projects/com.lveditor.draft')

    def test_mac_encrypted_draft_dispatches_to_native_reader(self):
        with patch('agent_video.jianying_crypto.sys.platform', 'darwin'), patch('agent_video.jianying_mac.decrypt_mac_file', return_value={'tracks': []}) as native:
            self.assertEqual(decrypt_jianying_file('fixture.json'), {'tracks': []})
            native.assert_called_once()
