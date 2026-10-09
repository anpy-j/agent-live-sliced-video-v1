import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent_video.engine.scripts import render_multi
from agent_video.engine.scripts import render_resources as resources


class RenderResourceTest(unittest.TestCase):
    def test_busy_cpu_receives_smaller_budget_and_one_decoder_per_input(self):
        idle = resources.Snapshot(5, 24 * resources.GIB, 32 * resources.GIB)
        busy = resources.Snapshot(75, 24 * resources.GIB, 32 * resources.GIB)
        self.assertEqual(resources.choose_budget(idle, 20).encoder_threads, 8)
        budget = resources.choose_budget(busy, 20)
        self.assertEqual(budget.encoder_threads, 1)
        self.assertEqual(budget.decoder_threads, 1)
        self.assertEqual(budget.filter_threads, 1)

    def test_memory_headroom_limits_threads_even_when_cpu_is_idle(self):
        # 8 GiB system, 3.5 GiB free: 2 GiB reserve + 0.5 GiB base + 4*0.25 GiB.
        state = resources.Snapshot(0, int(3.5 * resources.GIB), 8 * resources.GIB)
        self.assertEqual(resources.choose_budget(state, 20).encoder_threads, 4)
        with self.assertRaisesRegex(RuntimeError, "渲染内存不足"):
            resources.choose_budget(state, 20, decoder_bytes=resources.GIB)

    def test_missing_monitor_is_conservative_not_automatic_threads(self):
        budget = resources.choose_budget(None, 20)
        self.assertEqual(budget.encoder_threads, 1)
        self.assertEqual(budget.filter_threads, 1)
        self.assertFalse(budget.monitored)

    def test_single_core_and_inherited_affinity_are_respected(self):
        state = resources.Snapshot(0, 24 * resources.GIB, 32 * resources.GIB)
        self.assertEqual(resources.choose_budget(state, 1).encoder_threads, 1)
        with patch.object(resources, "snapshot", return_value=state), \
                patch.object(resources.psutil, "Process") as process:
            process.return_value.cpu_affinity.return_value = [2, 4, 6]
            budget, _ = resources.admit_render()
        self.assertEqual(budget.encoder_threads, 1)

    def test_governor_reduces_immediately_but_recovers_after_three_samples(self):
        budget = resources.Budget(8, 2, reserve_bytes=4 * resources.GIB)
        governor = resources.Governor(budget, 8)
        busy = resources.Snapshot(90, 20 * resources.GIB, 32 * resources.GIB)
        idle = resources.Snapshot(20, 20 * resources.GIB, 32 * resources.GIB)
        self.assertEqual(governor.update(busy), 7)
        self.assertEqual(governor.update(idle), 7)
        self.assertEqual(governor.update(idle), 7)
        self.assertEqual(governor.update(idle), 8)
        self.assertEqual(governor.update(idle), 8)

    def test_governor_requires_sustained_critical_memory_and_resets_counter(self):
        governor = resources.Governor(resources.Budget(4, 2), 4)
        critical = resources.Snapshot(30, 256 * resources.MIB, 32 * resources.GIB)
        healthy = resources.Snapshot(30, 20 * resources.GIB, 32 * resources.GIB)
        governor.update(critical)
        governor.update(healthy)
        governor.update(critical)
        governor.update(critical)
        with self.assertRaisesRegex(RuntimeError, "持续过低"):
            governor.update(critical)

    def test_memory_abort_terminates_ffmpeg_and_records_reason(self):
        critical = resources.Snapshot(30, 256 * resources.MIB, 32 * resources.GIB)
        process = Mock(pid=123, returncode=None)
        process.communicate.side_effect = [
            subprocess.TimeoutExpired("ffmpeg", 5),
            subprocess.TimeoutExpired("ffmpeg", 5),
            subprocess.TimeoutExpired("ffmpeg", 5), ("", ""),
        ]
        process.poll.return_value = None
        with tempfile.TemporaryDirectory() as directory:
            output = str(Path(directory) / "out.mp4")
            with patch.object(resources.subprocess, "Popen", return_value=process), \
                    patch.object(resources, "snapshot", return_value=critical), \
                    patch.object(resources, "psutil", None):
                with self.assertRaisesRegex(RuntimeError, "持续过低"):
                    resources.run_render(["ffmpeg"], resources.Budget(2, 2), critical, output)
            entries = [json.loads(line) for line in
                       Path(output + ".resources.jsonl").read_text(encoding="utf-8").splitlines()]
        process.terminate.assert_called_once()
        self.assertEqual(entries[-1]["event"], "aborted")
        self.assertIn("持续过低", entries[-1]["reason"])

    def test_running_process_affinity_shrinks_then_recovers_with_hysteresis(self):
        busy = resources.Snapshot(90, 20 * resources.GIB, 32 * resources.GIB)
        idle = resources.Snapshot(20, 20 * resources.GIB, 32 * resources.GIB)
        process = Mock(pid=123, returncode=0)
        process.communicate.side_effect = [subprocess.TimeoutExpired("ffmpeg", 5)] * 4 + [("ok", "")]
        controlled = Mock()
        controlled.cpu_affinity.return_value = list(range(20))
        with tempfile.TemporaryDirectory() as directory:
            output = str(Path(directory) / "out.mp4")
            with patch.object(resources.subprocess, "Popen", return_value=process), \
                    patch.object(resources, "snapshot", side_effect=[busy, idle, idle, idle]), \
                    patch.object(resources.psutil, "Process", return_value=controlled):
                self.assertEqual(resources.run_render(["ffmpeg"], resources.Budget(8, 2), idle, output), "ok")
            entries = [json.loads(line) for line in
                       Path(output + ".resources.jsonl").read_text(encoding="utf-8").splitlines()]
        assignments = [call.args[0] for call in controlled.cpu_affinity.call_args_list if call.args]
        self.assertEqual(assignments, [list(range(8)), list(range(7)), list(range(8))])
        self.assertEqual([entry["active_cpus"] for entry in entries if entry["event"] == "sample"],
                         [7, 7, 7, 8])

    def test_admission_timeout_reports_low_memory_without_starting_ffmpeg(self):
        low = resources.Snapshot(10, resources.GIB, 32 * resources.GIB)
        with patch.object(resources, "snapshot", return_value=low), \
                patch.object(resources.time, "monotonic", side_effect=[0, 31]), \
                patch.object(resources.time, "sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "渲染内存不足"):
                resources.admit_render()
        sleep.assert_not_called()

    def test_no_affinity_support_keeps_memory_monitoring(self):
        idle = resources.Snapshot(20, 20 * resources.GIB, 32 * resources.GIB)
        process = Mock(pid=123, returncode=0)
        process.communicate.side_effect = [subprocess.TimeoutExpired("ffmpeg", 5), ("ok", "")]
        controlled = Mock()
        controlled.cpu_affinity.side_effect = AttributeError("unsupported")
        with tempfile.TemporaryDirectory() as directory:
            output = str(Path(directory) / "out.mp4")
            with patch.object(resources.subprocess, "Popen", return_value=process), \
                    patch.object(resources, "snapshot", return_value=idle), \
                    patch.object(resources.psutil, "Process", return_value=controlled):
                resources.run_render(["ffmpeg"], resources.Budget(8, 2), idle, output)
            entries = [json.loads(line) for line in
                       Path(output + ".resources.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(entries[1]["event"], "affinity_unavailable")
        self.assertIsNone(entries[2]["active_cpus"])

    def test_ffmpeg_failure_remains_an_error_and_does_not_change_affinity_on_mac(self):
        process = Mock(pid=123, returncode=1)
        process.communicate.return_value = ("", "codec failed")
        process.poll.return_value = 1
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(resources.subprocess, "Popen", return_value=process), \
                    patch.object(resources, "psutil", None):
                with self.assertRaisesRegex(RuntimeError, "codec failed"):
                    resources.run_render(["ffmpeg"], resources.Budget(1, 1), None,
                                         str(Path(directory) / "out.mp4"))
        process.terminate.assert_not_called()

    def test_render_places_decoder_options_before_each_input_and_encoder_after(self):
        budget = resources.Budget(6, 2)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, timeline, output = root / "source.mp4", root / "timeline.json", root / "out.mp4"
            source.write_bytes(b"source")
            timeline.write_text(json.dumps([{"start": 1, "end": 2},
                                            {"start": 3, "end": 4}]), encoding="utf-8")
            commands = []

            def fake_render(command, *_):
                commands.append(command)
                Path(str(output) + ".partial.mp4").write_bytes(b"rendered")

            with patch("sys.argv", ["render_multi.py", str(timeline), str(output),
                                    "--src", f"1={source}"]), \
                    patch.object(render_multi, "source_fps", return_value=25), \
                    patch.object(render_multi, "source_size", return_value=(320, 240)), \
                    patch.object(render_multi, "admit_render", return_value=(budget, None)), \
                    patch.object(render_multi, "filter_complex_args", return_value=([], str(root / "graph"))), \
                    patch.object(render_multi, "run_render", side_effect=fake_render):
                (root / "graph").write_text("graph", encoding="utf-8")
                render_multi.main()
            command = commands[0]
            self.assertEqual(command.count("-threads"), 2)
            for at, token in enumerate(command):
                if token == "-i":
                    self.assertEqual(command[at - 6:at - 4], ["-threads", "1"])
            self.assertEqual(command[command.index("-threads:v") + 1], "6")
            self.assertEqual(command[command.index("-filter_complex_threads") + 1], "2")
            self.assertEqual(command[command.index("-preset") + 1], "slow")
            self.assertEqual(output.read_bytes(), b"rendered")


if __name__ == "__main__":
    unittest.main()
