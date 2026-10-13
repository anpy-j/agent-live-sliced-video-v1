"""Exercise actual FFmpeg joins at fractional frame rates under memory pressure."""
import array
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent_video.engine.scripts import render_multi
from agent_video.engine.scripts import render_resources as resources


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class RenderBatchIntegrationTest(unittest.TestCase):
    def test_low_memory_batches_preserve_frames_audio_clock_and_progress(self):
        with tempfile.TemporaryDirectory(prefix="render-batch-test-") as directory:
            root = Path(directory)
            source = root / "source.mp4"
            subprocess.run([
                "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                "testsrc2=s=96x160:r=30000/1001:d=4", "-f", "lavfi", "-i",
                "sine=frequency=440:sample_rate=48000:duration=4",
                "-c:v", "libx264", "-threads", "1", "-preset", "ultrafast",
                "-c:a", "aac", "-shortest", str(source)], check=True, capture_output=True)
            second = root / "second.mp4"
            subprocess.run([
                "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                "testsrc2=s=128x192:r=24:d=4", "-f", "lavfi", "-i",
                "sine=frequency=880:sample_rate=48000:duration=4",
                "-c:v", "libx264", "-threads", "1", "-preset", "ultrafast",
                "-c:a", "aac", "-shortest", str(second)], check=True, capture_output=True)
            rows = [{"start": 0.123 + i * 0.4, "end": 0.31 + i * 0.4,
                     "src": 1 + i % 2,
                     "speed": [1.0, 1.25, 0.75][i % 3]} for i in range(8)]
            timeline = root / "timeline.json"
            timeline.write_text(json.dumps(rows), encoding="utf-8")
            fps = 30000 / 1001
            counts = render_multi.segment_frame_counts(rows, fps)
            expected_frames = sum(counts)
            expected_duration = expected_frames / fps
            low = resources.Snapshot(10, int(5.65 * resources.GIB), 32 * resources.GIB)
            moderate = resources.Snapshot(10, int(5.9 * resources.GIB), 32 * resources.GIB)
            ample = resources.Snapshot(10, 24 * resources.GIB, 32 * resources.GIB)
            for normalize in (False, True):
                samples = []
                for mode, state in (("normal", ample), ("batched", low), ("batched-four", moderate)):
                    with self.subTest(mode=mode, normalize=normalize):
                        output = root / f"{mode}-{normalize}.mp4"
                        progress = root / f"{mode}-{normalize}.progress.json"
                        argv = ["render_multi.py", str(timeline), str(output), "--src", f"1={source}",
                                "--src", f"2={second}",
                                "--width", "96", "--height", "160", "--fps", str(fps),
                                "--preset", "fast", "--progress-file", str(progress)]
                        if not normalize:
                            argv.append("--no-loudnorm")
                        with patch("sys.argv", argv), \
                                patch.dict(os.environ, {"RENDER_MAX_THREADS": "1"}), \
                                patch.object(render_multi, "snapshot", return_value=state), \
                                patch.object(resources, "snapshot", return_value=state), \
                                patch.object(render_multi, "run_render", wraps=render_multi.run_render) as render:
                            render_multi.main()
                        commands = [call.args[0] for call in render.call_args_list]
                        if mode.startswith("batched"):
                            self.assertGreater(len(commands), 2)
                            self.assertEqual(max(cmd.count("-i") for cmd in commands),
                                             1 if mode == "batched" else 4)
                            self.assertEqual(commands[-1][commands[-1].index("-c:v") + 1], "copy")
                        else:
                            self.assertEqual(len(commands), 1)
                            self.assertEqual(commands[0].count("-i"), len(rows))
                        probe = json.loads(subprocess.check_output([
                            "ffprobe", "-v", "error", "-count_frames", "-show_streams",
                            "-show_packets", "-of", "json", str(output)]))
                        video = next(s for s in probe["streams"] if s["codec_type"] == "video")
                        audio = next(s for s in probe["streams"] if s["codec_type"] == "audio")
                        self.assertEqual(int(video["nb_read_frames"]), expected_frames)
                        self.assertAlmostEqual(float(video["duration"]), expected_duration, delta=1 / fps)
                        self.assertAlmostEqual(float(audio["duration"]), expected_duration, delta=0.025)
                        packets = [p for p in probe["packets"] if p["codec_type"] == "video"]
                        dts = [float(p["dts_time"]) for p in packets]
                        self.assertTrue(all(b > a for a, b in zip(dts, dts[1:])))
                        pts = sorted(float(p["pts_time"]) for p in packets)
                        self.assertAlmostEqual(pts[0], 0, delta=0.001)
                        self.assertTrue(all(abs(b - a - 1 / fps) < 0.001 for a, b in zip(pts, pts[1:])))
                        pcm = subprocess.check_output([
                            "ffmpeg", "-v", "error", "-i", str(output), "-vn", "-ac", "1",
                            "-ar", "48000", "-f", "f32le", "-"])
                        signal = array.array("f")
                        signal.frombytes(pcm)
                        samples.append(signal)
                        self.assertEqual(json.loads(progress.read_text())["total"], len(rows))
                        frames = [int(line.split("=", 1)[1]) for line in
                                  Path(str(progress) + ".ffmpeg").read_text().splitlines()
                                  if line.startswith("frame=")]
                        self.assertEqual(frames[-1], expected_frames)
                        self.assertEqual(frames, sorted(frames))
                        self.assertEqual(list(root.glob(".render-batches-*")), [])
                        self.assertFalse(Path(str(output) + ".partial.mp4").exists())
                for signal in samples[1:]:
                    count = min(len(samples[0]), len(signal))
                    rms_error = math.sqrt(sum((a - b) ** 2 for a, b in
                                              zip(samples[0][:count], signal[:count])) / count)
                    # A join must not introduce a timestamp shift or silence. AAC
                    # encoding differences and one-sample rounding are acceptable.
                    self.assertLess(rms_error, 0.01)
