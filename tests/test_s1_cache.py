import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent_video import s1_cache
from agent_video.runner import _run_pipeline_child
from agent_video.timeline import TimelineSegment, VirtualTimeline


class S1CacheTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.media = self.root / "source.mp4"
        self.media.write_bytes(b"original video")
        self.timeline = VirtualTimeline("draft", "草稿", [
            TimelineSegment("one", 0, 2, str(self.media), 0, 2)])
        self.old = self.root / "edits" / "old"
        self.new = self.root / "edits" / "new"
        self.old.mkdir(parents=True)
        self.new.mkdir()

    def seed(self, version="v1"):
        key, _ = s1_cache.prepare(self.old, self.timeline, version)
        data = {"source": self.timeline.title, "duration": 2,
                "virtual_timeline": self.timeline.to_dict(),
                "clauses": [{"id": 0, "text": "面料柔软。", "start": 0, "end": 2,
                             "usable": None, "order": None}]}
        (self.old / "clauses.json").write_text(json.dumps(data), encoding="utf-8")
        s1_cache.save(self.old, key)
        return data

    def test_new_job_reuses_only_s1_for_each_version(self):
        for version in ("v1", "v2", "v3"):
            with self.subTest(version=version):
                expected = self.seed(version)
                (self.old / "timeline.json").write_text('{"stale":"S4"}')
                (self.old / "clauses.filtered.json").write_text("{}")
                _, reused = s1_cache.prepare(self.new, self.timeline, version)
                self.assertEqual(reused, self.old)
                self.assertEqual(json.loads((self.new / "clauses.json").read_text(encoding="utf-8")), expected)
                self.assertFalse((self.new / "timeline.json").exists())
                self.assertFalse((self.new / "clauses.filtered.json").exists())

    def test_changed_bytes_even_with_same_size_and_mtime_cannot_reuse(self):
        self.seed()
        original = self.media.stat()
        self.media.write_bytes(b"modified video")
        os.utime(self.media, ns=(original.st_atime_ns, original.st_mtime_ns))
        self.assertIsNone(s1_cache.prepare(self.new, self.timeline, "v1")[1])
        self.assertFalse((self.new / "clauses.json").exists())

    def test_geometry_speed_and_version_changes_invalidate(self):
        self.seed()
        for field, value in (("source_start", 1), ("source_end", 3),
                             ("speed", 2), ("timeline_end", 3)):
            with self.subTest(field=field):
                segment = self.timeline.segments[0]
                before = getattr(segment, field)
                setattr(segment, field, value)
                self.assertIsNone(s1_cache.prepare(self.new, self.timeline, "v1")[1])
                setattr(segment, field, before)
        self.assertIsNone(s1_cache.prepare(self.new, self.timeline, "v3")[1])

    def test_old_unverified_data_and_audio_are_regenerated(self):
        for name in ("clauses.json", "timeline.json", "virtual_audio.wav",
                     "virtual_audio.wav.manifest.json"):
            (self.new / name).write_text("old")
        s1_cache.prepare(self.new, self.timeline, "v1")
        self.assertEqual(list(self.new.iterdir()), [])

    def test_corrupt_snapshot_falls_back_to_asr(self):
        self.seed()
        path = self.old / s1_cache.SNAPSHOT
        data = json.loads(path.read_text(encoding="utf-8"))
        data["data"]["clauses"][0]["text"] = "tampered"
        path.write_text(json.dumps(data))
        self.assertIsNone(s1_cache.prepare(self.new, self.timeline, "v1")[1])
        path.write_text("{")
        self.assertIsNone(s1_cache.prepare(self.new, self.timeline, "v1")[1])

    def test_multiple_materials_are_all_checked(self):
        other = self.root / "other.mp4"
        other.write_bytes(b"second")
        self.timeline.segments.append(TimelineSegment("two", 2, 4, str(other), 0, 2))
        self.seed()
        other.write_bytes(b"edited")
        self.assertIsNone(s1_cache.prepare(self.new, self.timeline, "v1")[1])

    def test_child_saves_before_downstream_and_next_click_skips_transcription(self):
        for version, module in (("v1", "agent_video.runner"),
                                ("v2", "agent_video.pipeline_v2.run"),
                                ("v3", "agent_video.pipeline_v3.run")):
            with self.subTest(version=version):
                calls = []
                for folder in (self.old, self.new):
                    (folder / "virtual_timeline.json").write_text(
                        json.dumps(self.timeline.to_dict()), encoding="utf-8")

                def pipeline(source, workspace, *, on_stage, **kwargs):
                    folder = Path(workspace)
                    cached = folder / "clauses.json"
                    if not cached.exists():
                        calls.append("transcribe")
                        cached.write_text(json.dumps({"source": self.timeline.title,
                            "virtual_timeline": self.timeline.to_dict(),
                            "duration": 2, "clauses": [{"id": 0, "text": "original"}]}))
                    on_stage("asr", "done", "S1 done")
                    # Simulate later mutable timeline output without contaminating S1.
                    (folder / "timeline.json").write_text('{"clauses":[{"order":4}]}')
                    on_stage("filter", "done", "S2 done")
                    return {}

                with patch(f"{module}.run_pipeline", side_effect=pipeline), \
                        patch("agent_video.runner._windows_kill_on_close_job"), \
                        patch("agent_video.runner.os.setsid", create=True):
                    for folder in (self.old, self.new):
                        queue = Mock()
                        _run_pipeline_child(queue, "display", str(folder), (70, 90),
                                            pipeline_version=version)
                        self.assertEqual(queue.put.call_args.args[0][0], "success")
                self.assertEqual(calls, ["transcribe"])
                snapshot = json.loads((self.new / s1_cache.SNAPSHOT).read_text(encoding="utf-8"))
                self.assertEqual(snapshot["data"]["clauses"][0]["text"], "original")

    def test_real_s1_s2_pipeline_reuses_and_invalidates_for_all_versions(self):
        from agent_video.pipeline.errors import PipelineError
        for version, asr_module in (("v1", "agent_video.pipeline.asr"),
                                    ("v2", "agent_video.pipeline_v3.asr"),
                                    ("v3", "agent_video.pipeline_v3.asr")):
            with self.subTest(version=version):
                for folder in (self.old, self.new):
                    (folder / "virtual_timeline.json").write_text(
                        json.dumps(self.timeline.to_dict()), encoding="utf-8")

                def stop_after_s2(event):
                    if event[:3] == ("stage", "filter", "done"):
                        raise PipelineError("test stops before paid AI")

                with patch(f"{asr_module}.transcribe", return_value=([], [
                        {"w": "面料柔软亲肤。", "s": 0, "e": 2}])) as transcribe, \
                        patch("agent_video.timeline.extract_virtual_timeline_audio", return_value="fake.wav"), \
                        patch("agent_video.runner._windows_kill_on_close_job"), \
                        patch("agent_video.runner.os.setsid", create=True):
                    for folder in (self.old, self.new):
                        queue = Mock()
                        queue.put.side_effect = stop_after_s2
                        _run_pipeline_child(queue, "display", str(folder), (70, 90),
                                            pipeline_version=version)
                        self.assertTrue((folder / "clauses.filtered.json").is_file())
                        self.assertEqual(queue.put.call_args.args[0][0], "pipeline_error")
                    self.assertEqual(transcribe.call_count, 1)
                    self.media.write_bytes(self.media.read_bytes() + b"change")
                    queue = Mock()
                    queue.put.side_effect = stop_after_s2
                    _run_pipeline_child(queue, "display", str(self.new), (70, 90),
                                        pipeline_version=version)
                    self.assertEqual(transcribe.call_count, 2)


if __name__ == "__main__":
    unittest.main()
