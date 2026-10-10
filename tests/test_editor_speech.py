import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from agent_video.editor.transcribe import main
from agent_video.editor.speech import SpeechQueue


class EditorSpeechTest(unittest.TestCase):
    def test_cancel_terminates_windows_process_tree(self):
        process = MagicMock(pid=4321)
        process.poll.return_value = None
        with patch("agent_video.editor.speech.os.name", "nt"), patch("agent_video.editor.speech.subprocess.run") as run:
            SpeechQueue.terminate(process)
        self.assertEqual(run.call_args.args[0], ["taskkill", "/PID", "4321", "/T", "/F"])

    def test_trimmed_speech_maps_to_sped_up_timeline(self):
        with tempfile.TemporaryDirectory() as directory:
            request = Path(directory) / "speech.json"
            request.write_text(json.dumps({"path": "source.mp4", "in": 7, "start": 12,
                                           "speed": 2, "duration": 3, "model": "small"}))
            rows = [{"start": .5, "end": 2.5, "text": "第一句"},
                    {"start": 5, "end": 7, "text": "尾句"},
                    {"start": 7, "end": 8, "text": "片段外"}]
            with patch.object(sys, "argv", ["speech", "--request", str(request)]), \
                    patch("agent_video.editor.transcribe.subprocess.run") as run, \
                    patch("agent_video.engine.scripts.asr_backend.Transcriber") as transcriber:
                transcriber.return_value.transcribe.return_value = rows
                main()
            cmd = run.call_args.args[0]
            self.assertEqual(cmd[cmd.index("-ss") + 1], "7")
            self.assertEqual(cmd[cmd.index("-t") + 1], "6")
            result = json.loads(request.with_suffix(".result.json").read_text(encoding="utf-8"))
            self.assertEqual(result, [{"start": 12.25, "duration": 1, "text": "第一句"},
                                      {"start": 14.5, "duration": .5, "text": "尾句"}])

    def test_failed_model_removes_temporary_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            request = Path(directory) / "speech.json"
            request.write_text(json.dumps({"path": "source.mp4", "in": 0, "start": 0,
                                           "speed": 1, "duration": 1, "model": "missing"}))
            request.with_suffix(".wav").write_bytes(b"audio")
            with patch.object(sys, "argv", ["speech", "--request", str(request)]), \
                    patch("agent_video.editor.transcribe.subprocess.run"), \
                    patch("agent_video.engine.scripts.asr_backend.Transcriber", side_effect=RuntimeError("missing model")):
                with self.assertRaises(RuntimeError):
                    main()
            self.assertFalse(request.with_suffix(".wav").exists())
