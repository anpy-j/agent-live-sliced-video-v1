import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_video.jianying_crypto import decrypt_jianying_file


class DecryptLauncherTest(unittest.TestCase):
    def test_python_and_frozen_launchers_return_worker_result(self):
        for frozen in (False, True):
            with self.subTest(frozen=frozen), tempfile.TemporaryDirectory() as folder:
                directory = Path(folder)
                source = directory / "draft_content.json"
                source.write_bytes(b"encrypted")

                def worker(command, **kwargs):
                    prefix = (["backend.exe", "--jianying-decrypt"] if frozen else
                              ["backend.exe", "-m", "agent_video.jianying_crypto"])
                    self.assertEqual(command[:len(prefix)], prefix)
                    index = command.index("--worker-decrypt")
                    self.assertEqual(command[index + 1], str(source.resolve()))
                    Path(command[index + 2]).write_text(json.dumps({"tracks": []}), encoding="utf-8")
                    return subprocess.CompletedProcess(command, 0, "", "")

                with patch("agent_video.jianying_crypto.sys.platform", "win32"), \
                     patch("agent_video.jianying_crypto.sys.executable", "backend.exe"), \
                     patch("agent_video.jianying_crypto.sys.frozen", frozen, create=True), \
                     patch("agent_video.jianying_crypto.find_jianying_install_dir", return_value=directory), \
                     patch("agent_video.jianying_crypto.subprocess.run", side_effect=worker):
                    self.assertEqual(decrypt_jianying_file(source), {"tracks": []})
                self.assertEqual(source.read_bytes(), b"encrypted")
