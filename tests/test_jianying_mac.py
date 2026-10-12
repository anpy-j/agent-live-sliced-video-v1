"""macOS private-ABI isolation, discovery, validation and bridge smoke tests."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from agent_video import jianying_mac as native
from agent_video.jianying_crypto import JianyingDecryptError, decrypt_jianying_file


class MacReaderTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.frameworks = self.root / 'Renamed Jianying.app/Contents/Frameworks'
        self.frameworks.mkdir(parents=True)
        self.library = self.frameworks / 'libvideoeditor.dylib'
        self.library.write_bytes(b'library-fixture')
        self.source = self.root / 'draft_content.json'
        self.source.write_bytes(b'encrypted-fixture')

    def tearDown(self):
        self.temp.cleanup()

    def test_explicit_app_framework_and_library_paths(self):
        with patch.dict(os.environ, {}, clear=True):
            for path in (self.frameworks.parent.parent, self.frameworks, self.library):
                self.assertEqual(native.find_mac_library(path), self.library.resolve())

    def test_bad_explicit_path_never_falls_back(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(JianyingDecryptError, '指定的'):
                native.find_mac_library(self.root / 'missing.app')

    def test_environment_library_override(self):
        with patch.dict(os.environ, {'JIANYING_MAC_LIBRARY': str(self.library)}, clear=True):
            self.assertEqual(native.find_mac_library(), self.library.resolve())

    def test_spotlight_finds_renamed_app(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(Path, 'glob', return_value=[]), patch('agent_video.jianying_mac.shutil.which', return_value='/usr/bin/mdfind'), patch('agent_video.jianying_mac.subprocess.run', return_value=subprocess.CompletedProcess([], 0, str(self.frameworks.parent.parent)+'\n', '')):
            self.assertEqual(native.find_mac_library(), self.library.resolve())

    def run_reader(self, content='{"tracks":[]}', returncode=0, timeout=False):
        def child(command, **kwargs):
            if timeout:
                raise subprocess.TimeoutExpired(command, 1)
            Path(command[-1]).write_text(content)
            self.assertEqual(kwargs['env']['DYLD_LIBRARY_PATH'], str(self.library.parent.resolve()))
            self.assertEqual(Path(command[-2]), self.source.resolve())
            return subprocess.CompletedProcess(command, returncode, '', 'native failure' if returncode else '')
        with patch('agent_video.jianying_mac.native_helper', return_value=Path('/fixture/helper')), patch('agent_video.jianying_mac.subprocess.run', side_effect=child):
            return native.decrypt_mac_file(self.source.resolve(), self.library)

    def test_valid_json_original_unchanged_and_temporary_result_removed(self):
        before = self.source.read_bytes()
        self.assertEqual(self.run_reader(), {'tracks': []})
        self.assertEqual(before, self.source.read_bytes())

    def test_invalid_json_is_rejected(self):
        with self.assertRaisesRegex(JianyingDecryptError, '有效 JSON'):
            self.run_reader('not-json')

    def test_json_array_is_rejected(self):
        with self.assertRaisesRegex(JianyingDecryptError, 'JSON 对象'):
            self.run_reader('[]')

    def test_native_crash_or_failure_is_reported(self):
        with self.assertRaisesRegex(JianyingDecryptError, 'native failure'):
            self.run_reader(returncode=-11)

    def test_timeout_is_reported(self):
        with self.assertRaisesRegex(JianyingDecryptError, '超时'):
            self.run_reader(timeout=True)

    def test_bundled_helper_needs_no_compiler(self):
        helper = self.root / 'tools/bin/jianying-decrypt'; helper.parent.mkdir(parents=True)
        helper.write_bytes(b'fixture'); helper.chmod(0o700)
        with patch('agent_video.jianying_mac.resource_dir', return_value=self.root), patch('agent_video.jianying_mac.shutil.which') as compiler:
            self.assertEqual(native.native_helper(), helper)
            compiler.assert_not_called()

    def test_cached_helper_needs_no_compiler(self):
        architecture = 'arm64'
        source = Path(native.__file__).parent / 'native/jianying_decrypt.cpp'
        key = hashlib.sha256(source.read_bytes()+architecture.encode()).hexdigest()[:20]
        helper = self.root / 'cache/jianying-native' / ('decrypt-'+key)
        helper.parent.mkdir(parents=True); helper.write_bytes(b'fixture'); helper.chmod(0o700)
        with patch('agent_video.jianying_mac.resource_dir', return_value=self.root), patch('agent_video.jianying_mac.user_data_dir', return_value=self.root), patch('agent_video.jianying_mac.platform.machine', return_value=architecture), patch('agent_video.jianying_mac.shutil.which') as compiler:
            self.assertEqual(native.native_helper(), helper)
            compiler.assert_not_called()

    def test_missing_compiler_has_actionable_error(self):
        with patch('agent_video.jianying_mac.resource_dir', return_value=self.root), patch('agent_video.jianying_mac.user_data_dir', return_value=self.root), patch('agent_video.jianying_mac.platform.machine', return_value='arm64'), patch('agent_video.jianying_mac.shutil.which', return_value=None):
            with self.assertRaisesRegex(JianyingDecryptError, 'xcode-select --install'):
                native.native_helper()

    def test_smart_v3_uses_same_mac_reader(self):
        from agent_video.smart_v3.jianying_crypto import decrypt_jianying_file as smart_read
        with patch('agent_video.smart_v3.jianying_crypto.sys.platform', 'darwin'), patch('agent_video.jianying_mac.decrypt_mac_file', return_value={'tracks': []}) as read:
            self.assertEqual(smart_read(self.source), {'tracks': []})
            read.assert_called_once()


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('xcrun'), 'macOS C++ toolchain required')
class NativeBridgeTest(unittest.TestCase):
    def test_real_cpp_bridge_calls_fixture_library_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); cpp = root / 'fixture.cpp'; library = root / 'libvideoeditor.dylib'
            cpp.write_text('''#include <string>
namespace lvve { class EncryptUtils { public: std::string decrypt(const std::string&, const std::string&, bool&); };
std::string EncryptUtils::decrypt(const std::string& value, const std::string&, bool& ok) {
    ok = value == "encrypted-fixture";
    return ok ? "{\\\"tracks\\\":[],\\\"fixture\\\":true}" : "";
}}
''')
            subprocess.run(['xcrun', 'clang++', '-std=c++17', '-dynamiclib', str(cpp), '-o', str(library)], check=True, capture_output=True)
            source = root / 'draft_content.json'; source.write_bytes(b'encrypted-fixture')
            before = source.read_bytes()
            with patch('agent_video.jianying_mac.user_data_dir', return_value=root), patch('agent_video.jianying_mac.resource_dir', return_value=root):
                self.assertEqual(decrypt_jianying_file(source, install_dir=library), {'tracks': [], 'fixture': True})
                self.assertEqual(before, source.read_bytes())
                source.write_bytes(b'invalid-fixture')
                with self.assertRaisesRegex(JianyingDecryptError, 'rejected draft'):
                    decrypt_jianying_file(source, install_dir=library)
