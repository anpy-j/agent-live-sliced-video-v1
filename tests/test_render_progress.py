import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_video.db import Store
from agent_video.runner import JobRunner


class RenderProgressTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.store = Store(self.root / "agent.db")
        self.job_id = self.store.create_job(title="progress", source_path="fixture.mp4",
                                            workspace=str(self.workspace),
                                            export_dir=str(self.root / "exports"),
                                            export_mode="segments")
        self.store.stage_start(self.job_id, "render", "start")
        self.runner = JobRunner(self.store, self.root)

    def render_stage(self):
        return next(s for s in self.store.get_job(self.job_id)["stages"] if s["stage_id"] == "render")

    def test_encoded_frames_only_count_completed_segments(self):
        (self.workspace / "render.progress.json").write_text(
            json.dumps({"total": 3, "frame_ends": [25, 60, 100]}), encoding="utf-8")
        for frames, count, percent in [(0, 0, 0), (24, 0, 0), (25, 1, 33), (60, 2, 66), (100, 3, 99)]:
            (self.workspace / "render.progress.json.ffmpeg").write_text(
                f"frame={frames}\nprogress=continue\n", encoding="utf-8")
            self.runner._sync_render_progress(self.job_id, self.workspace)
            stage = self.render_stage()
            self.assertEqual(stage["result"]["completed"], count)
            self.assertEqual(round(stage["progress"] * 100), percent)
            self.assertEqual(stage["status"], "running")
            self.assertIsNone(stage["finished_at"])

    def test_render_done_stays_running_until_all_exports_finish(self):
        segments = [{"id": i, "start": i * 3, "end": i * 3 + 3} for i in range(3)]

        def pipeline(job_id, source, workspace, target, on_stage, **kwargs):
            on_stage("render", "start", "rendering")
            self.runner._set_render_progress(job_id, 3, 3)
            on_stage("render", "done", "preview complete")
            self.assertEqual(self.render_stage()["status"], "running")
            return {"output": "preview.mp4", "segments": segments}

        def render(media, segment, output, workdir, index, **kwargs):
            stage = self.render_stage()
            self.assertEqual(stage["status"], "running")
            self.assertIsNone(stage["finished_at"])
            self.assertEqual(stage["result"]["completed"], index - 1)
            self.assertEqual(stage["result"]["total"], 3)
            Path(output).write_bytes(b"clip")

        with patch.object(self.runner, "_execute_pipeline", side_effect=pipeline), \
                patch("agent_video.runner.render_segment", side_effect=render):
            self.runner._run(self.store.get_job(self.job_id))
        stage = self.render_stage()
        self.assertEqual(stage["status"], "succeeded")
        self.assertEqual(stage["progress"], 1)
        self.assertEqual(stage["result"]["completed"], 3)
        self.assertEqual(self.store.get_job(self.job_id)["status"], "completed")

    def test_export_failure_does_not_claim_all_segments_completed(self):
        manifest = {"output": "preview.mp4", "segments": [
            {"id": i, "start": i * 3, "end": i * 3 + 3} for i in range(3)]}

        def render(media, segment, output, workdir, index, **kwargs):
            if index == 2:
                raise RuntimeError("failed second clip")
            Path(output).write_bytes(b"clip")

        with patch("agent_video.runner.render_segment", side_effect=render):
            self.runner._succeed(self.job_id, self.workspace, manifest,
                                 export_dir=str(self.root / "exports"), export_mode="segments")
        self.assertEqual(self.render_stage()["result"]["completed"], 1)
        self.assertEqual(self.render_stage()["status"], "failed")
        self.assertEqual(self.store.get_job(self.job_id)["status"], "failed")
