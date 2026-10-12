import importlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_video.pipeline.judge_progress import progress_writer


class JudgeProgressTest(unittest.TestCase):
    def test_fast_batch_is_published_before_slow_batch_finishes(self):
        for module in ("agent_video.pipeline.run", "agent_video.pipeline_v3.run"):
            with self.subTest(module=module), tempfile.TemporaryDirectory() as directory:
                pipeline = importlib.import_module(module)
                published = threading.Event()
                writer = progress_writer(directory)

                def publish(batch):
                    writer(batch)
                    if 1 in batch:
                        published.set()

                def judge(model, batch, product, timeout):
                    if batch == 0:
                        self.assertTrue(published.wait(2))
                        data = json.loads((Path(directory) / "judge.progress.json").read_text(encoding="utf-8"))
                        self.assertEqual([d["id"] for d in data["decisions"]], [1])
                    return {batch: {"id": batch, "usable": batch == 0}}

                with patch.object(pipeline, "_judge_batch_once", side_effect=judge):
                    result, attempts = pipeline._run_judge_batches("auto", [0, 1], None, 5,
                                                                   concurrency=2, on_batch=publish)
                self.assertEqual(attempts, 2)
                self.assertEqual(len(result), 2)

    def test_failed_batch_does_not_publish_a_fake_verdict(self):
        for module in ("agent_video.pipeline.run", "agent_video.pipeline_v3.run"):
            with self.subTest(module=module), tempfile.TemporaryDirectory() as directory:
                pipeline = importlib.import_module(module)
                writer = progress_writer(directory)

                def judge(model, batch, product, timeout):
                    if batch == 1:
                        raise pipeline.AIReturnError("invalid JSON")
                    return {batch: {"id": batch, "usable": True}}

                with patch.object(pipeline, "_judge_batch_once", side_effect=judge):
                    with self.assertRaises(pipeline.AIReturnError):
                        pipeline._run_judge_batches("auto", [0, 1], None, 5,
                                                    retries=1, on_batch=writer)
                data = json.loads((Path(directory) / "judge.progress.json").read_text(encoding="utf-8"))
                self.assertEqual(data["decisions"], [{"id": 0, "usable": True}])
