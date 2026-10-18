import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent_video.ai import ProviderResponseError
from agent_video.ai_contract import json_prompt, save_failure
from agent_video.pipeline.judge_progress import JudgeProgress, progress_writer


MODULES = ("agent_video.pipeline", "agent_video.pipeline_v3")


class JsonResilienceTest(unittest.TestCase):
    def test_contract_is_sent_to_every_provider_and_model(self):
        for module in MODULES:
            ai = importlib.import_module(module + ".ai")
            for provider_id in ("workbuddy", "codex", "opencode", "antigravity"):
                for model in ("hy3", "another-model"):
                    with self.subTest(module=module, provider=provider_id, model=model):
                        provider = Mock()
                        provider.normalize_model.return_value = model
                        provider.generate_json.return_value = {"data": {"decisions": []}}
                        with patch.dict(os.environ, {"PIPELINE_AI_PROVIDER": provider_id}), \
                                patch.object(ai, "resolve_provider", return_value=(provider_id, "/fake/cli")), \
                                patch.dict(ai._CLI_BUILDERS, {provider_id: Mock(return_value=provider)}):
                            self.assertEqual(ai._call_llm(model, "business input", ai.DECISION_SCHEMA, 5),
                                             {"decisions": []})
                        sent = provider.generate_json.call_args.kwargs
                        self.assertEqual(sent["model"], model)
                        self.assertIn("StructuredOutput", sent["prompt"])
                        self.assertIn('"required":["decisions"]', sent["prompt"])
                        self.assertIn("business input", sent["prompt"])
                        provider.generate_json.assert_called_once()

    def test_failed_response_is_preserved_and_error_identifies_model(self):
        for module in MODULES:
            ai = importlib.import_module(module + ".ai")
            with self.subTest(module=module), tempfile.TemporaryDirectory() as directory:
                provider = Mock()
                provider.normalize_model.return_value = "another-model"
                provider.generate_json.side_effect = ProviderResponseError("missing JSON", {"stdout": "plain answer"})
                with patch.dict(os.environ, {"PIPELINE_AI_PROVIDER": "workbuddy"}), \
                        patch("agent_video.ai_contract.user_data_dir", return_value=Path(directory)), \
                        patch.object(ai, "resolve_provider", return_value=("workbuddy", "/fake/cli")), \
                        patch.dict(ai._CLI_BUILDERS, {"workbuddy": Mock(return_value=provider)}):
                    with self.assertRaisesRegex(ai.AIReturnError, "another-model.*decisions"):
                        ai._call_llm("another-model", "input", ai.DECISION_SCHEMA, 5)
                files = list(Path(directory).rglob("*.json"))
                self.assertEqual(len(files), 1)
                self.assertIn("plain answer", files[0].read_text(encoding="utf-8"))

    def test_diagnostics_are_bounded_and_redacted(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch("agent_video.ai_contract.user_data_dir", return_value=Path(directory)):
            error = ProviderResponseError("bad", {"stderr": "Bearer secret-123 " + "x" * 200000})
            path = save_failure("provider", "model", {}, error)
            text = Path(path).read_text(encoding="utf-8")
            json.loads(text)
            self.assertNotIn("secret-123", text)
            self.assertLess(len(text), 170000)


class JudgeResumeTest(unittest.TestCase):
    @staticmethod
    def batch(cid, text="source"):
        return [{"id": cid, "members": [{"id": cid, "text": text}]}]

    def test_only_failed_batch_is_retried_after_restart(self):
        for module in MODULES:
            pipeline = importlib.import_module(module + ".run")
            with self.subTest(module=module), tempfile.TemporaryDirectory() as directory, \
                    patch.dict(os.environ, {"PIPELINE_AI_ENGINE": "llm", "PIPELINE_AI_PROVIDER": "workbuddy"}):
                batches = [self.batch(1), self.batch(2)]

                def first(model, batch, product, timeout):
                    cid = batch[0]["id"]
                    if cid == 2:
                        raise pipeline.AIReturnError("invalid JSON")
                    return {cid: {"id": cid, "usable": True, "reason": "ok"}}

                with patch.object(pipeline, "_judge_batch_once", side_effect=first):
                    with self.assertRaises(pipeline.AIReturnError):
                        pipeline._run_judge_batches("any-model", batches, None, 5, retries=0,
                                                    on_batch=progress_writer(directory))
                with patch.object(pipeline, "_judge_batch_once", return_value={
                        2: {"id": 2, "usable": False, "reason": "ok"}}) as call:
                    result, attempts = pipeline._run_judge_batches(
                        "any-model", batches, None, 5, on_batch=progress_writer(directory))
                self.assertEqual(set(result), {1, 2})
                self.assertEqual(attempts, 1)
                self.assertEqual(call.call_args.args[1], batches[1])

    def test_model_provider_prompt_and_input_changes_invalidate_cache(self):
        with patch.dict(os.environ, {"PIPELINE_AI_PROVIDER": "workbuddy", "PIPELINE_AI_ENGINE": "llm"}):
            schema = {"required": ["decisions"]}
            key = JudgeProgress.key
            original = key("model-a", "source-a", schema)
            self.assertNotEqual(original, key("model-b", "source-a", schema))
            self.assertNotEqual(original, key("model-a", "source-b", schema))
            self.assertNotEqual(original, key("model-a", "source-a", {"required": ["other"]}))
            self.assertIsNone(key("auto", "source-a", schema))
            with patch.dict(os.environ, {"PIPELINE_AI_PROVIDER": "codex"}):
                self.assertNotEqual(original, key("model-a", "source-a", schema))

    def test_damaged_cached_verdict_is_recomputed(self):
        for module in MODULES:
            pipeline = importlib.import_module(module + ".run")
            with self.subTest(module=module), tempfile.TemporaryDirectory() as directory, \
                    patch.dict(os.environ, {"PIPELINE_AI_ENGINE": "llm", "PIPELINE_AI_PROVIDER": "workbuddy"}):
                batch = self.batch(1)
                writer = progress_writer(directory)
                key = writer.key("model", json_prompt(pipeline._judge_prompt(batch), pipeline.DECISION_SCHEMA),
                                 pipeline.DECISION_SCHEMA)
                writer.store(key, {1: {"id": 1, "usable": "false"}})
                with patch.object(pipeline, "_judge_batch_once", return_value={
                        1: {"id": 1, "usable": True, "reason": "ok"}}) as call:
                    result, attempts = pipeline._run_judge_batches(
                        "model", [batch], None, 5, on_batch=progress_writer(directory))
                self.assertTrue(result[1]["usable"])
                self.assertEqual(attempts, 1)
                call.assert_called_once()
