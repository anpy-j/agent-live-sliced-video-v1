"""V1 isolation and V2 failure gates. No paid AI calls or real user tasks."""
import copy
import io
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent_video.db import Store, STAGE_DEFINITIONS
from agent_video.runner import JobRunner, _run_pipeline_child
from agent_video.server import Application
from agent_video.pipeline.errors import PipelineError, AIReturnError
from agent_video.pipeline_v2 import contracts as schemas
from agent_video.pipeline_v2.ai import Calls
from agent_video.pipeline_v2.common import (block_issues, compile_clips, digest, make_block,
                                            plan_blocks, prepare, read, save, source_fingerprint)
from agent_video.pipeline_v2.content import extract
from agent_video.pipeline_v2.run import run_pipeline, invalidate_stage
from agent_video.pipeline.render import group_segments_for_export, build_segments


def row(cid, text, start=None, usable=True):
    start = float(cid * 3) if start is None else start
    return {"id": cid, "text": text, "start": start, "end": start + 3, "usable": usable}


ROWS = [row(1, "肩线往里收，肩膀显得窄。"), row(2, "面料有弹力，抬手不会紧。"),
        row(3, "下摆开衩，坐下来也不会绷。")]


def fake_render(media, segments, output, workspace, **kwargs):
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_bytes(b"fixture-render")


class V2ContentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = {"source": "fixture.mp4", "duration": 20, "clauses": copy.deepcopy(ROWS)}
        save(self.root / "clauses.filtered.json", self.data)
        self.invocations = []

    def caller(self, model, prompt, schema, timeout):
        self.invocations.append(schema)
        if schema is schemas.EXTRACT:
            return {"blocks": [{"ids": [r["id"]], "topic": "版型", "fact": r["text"]}
                               for r in self.data["clauses"]], "fragments": [], "rejected": []}
        if schema is schemas.PLAN:
            blocks = read(self.root / "v2" / "candidates.json")["blocks"]
            return {"selected": [b["id"] for b in blocks], "reserve": []}
        if schema is schemas.REVIEW:
            return {"issues": []}
        self.fail("Unexpected call")

    def run_stage(self, stage, **kwargs):
        return run_pipeline("fixture.mp4", str(self.root), only_stage=stage,
                            target_seconds=(8, 10), ai_fn=kwargs.pop("ai_fn", self.caller),
                            render_fn=kwargs.pop("render_fn", fake_render), **kwargs)

    def prepare_plan(self):
        self.run_stage("judge")
        self.run_stage("order")

    def test_separate_stages_cache_and_approved_render(self):
        self.prepare_plan()
        self.run_stage("review")
        self.assertEqual(len(self.invocations), 3)
        self.run_stage("judge")
        self.run_stage("order")
        self.run_stage("review")
        self.assertEqual(len(self.invocations), 3, "unchanged stages must not pay again")
        render = Mock(side_effect=fake_render)
        manifest = self.run_stage("render", render_fn=render)
        self.assertEqual(manifest["pipeline_version"], "v2")
        self.assertTrue(manifest["text_review_passed"])
        self.assertFalse(manifest["visual_reviewed"])
        self.assertEqual(manifest["total_seconds"], 9)
        self.assertFalse((self.root / "order.json").exists())
        self.assertFalse((self.root / "clauses.judged.json").exists())
        render.assert_called_once()

    def test_failed_review_cannot_render_even_after_one_repair(self):
        self.prepare_plan()
        bid = read(self.root / "v2" / "candidates.json")["blocks"][0]["id"]

        def bad(model, prompt, schema, timeout):
            if schema is schemas.REVIEW:
                # Persistent global defect exercises one repair and a second audit.
                return {"issues": [{"block": "GLOBAL", "code": "contradiction", "reason": "整片比较关系矛盾"}]}
            return self.caller(model, prompt, schema, timeout)

        with self.assertRaisesRegex(PipelineError, "未通过"):
            self.run_stage("review", ai_fn=bad)
        report = read(self.root / "v2" / "review.json")
        self.assertFalse(report["passed"])
        self.assertEqual(len(report["attempts"]), 2)
        self.assertTrue((self.root / "v2" / "成片文本.txt").exists())
        renderer = Mock()
        with self.assertRaisesRegex(PipelineError, "禁止渲染"):
            self.run_stage("render", render_fn=renderer)
        renderer.assert_not_called()

    def test_changed_source_invalidates_approval(self):
        self.prepare_plan()
        self.run_stage("review")
        self.data["clauses"][0]["text"] = "前襟有扣子，可以敞开穿。"
        save(self.root / "clauses.filtered.json", self.data)
        with self.assertRaisesRegex(PipelineError, "上游不一致"):
            self.run_stage("render")

    def test_changed_plan_invalidates_approval(self):
        self.prepare_plan()
        self.run_stage("review")
        path = self.root / "v2" / "order.json"
        data = read(path)
        data["plan"]["selected"].reverse()
        save(path, data)
        with self.assertRaisesRegex(PipelineError, "验收已失效"):
            self.run_stage("render")

    def test_short_film_cannot_pass_even_when_ai_reports_no_issues(self):
        self.prepare_plan()
        order = read(self.root / "v2" / "order.json")
        from agent_video.pipeline_v2.content import review
        candidates = read(self.root / "v2" / "candidates.json")
        with self.assertRaisesRegex(PipelineError, "实际9.00秒"):
            review(Calls(self.root / "v2", "auto", caller=self.caller), candidates, order,
                   (110, 125), self.root / "v2")
        self.assertFalse(read(self.root / "v2" / "review.json")["passed"])

    def test_s3_missing_id_is_not_silently_rejected(self):
        def missing(*args):
            return {"blocks": [{"ids": [1], "topic": "版型", "fact": "肩线收窄"}],
                    "fragments": [], "rejected": []}
        with self.assertRaisesRegex(PipelineError, "遗漏"):
            self.run_stage("judge", ai_fn=missing)

    def test_impossible_duration_stops_before_paying_for_s4(self):
        self.run_stage("judge")
        before = len(self.invocations)
        with self.assertRaisesRegex(PipelineError, "时长上限"):
            run_pipeline("fixture.mp4", str(self.root), only_stage="order",
                         target_seconds=(110, 125), ai_fn=self.caller)
        self.assertEqual(len(self.invocations), before)

    def test_s3_bad_source_reference_fails(self):
        def unknown(*args):
            return {"blocks": [{"ids": [999], "topic": "版型", "fact": "不存在"}],
                    "fragments": [], "rejected": [1, 2, 3]}
        with self.assertRaises(PipelineError):
            self.run_stage("judge", ai_fn=unknown)

    def test_rejected_continuation_does_not_discard_useful_prefix(self):
        self.data["clauses"] = [row(1, "这件的肩线"), row(2, "帮我踢单。", usable=False),
                                row(7, "往里收，肩膀显得窄。")]
        save(self.root / "clauses.filtered.json", self.data)

        def rescue(model, prompt, schema, timeout):
            if schema is schemas.EXTRACT:
                return {"blocks": [], "fragments": [
                    {"ids": [1], "need": "肩线效果"}, {"ids": [7], "need": "肩线主语"}], "rejected": []}
            self.assertIs(schema, schemas.RESCUE)
            return {"blocks": [{"ids": [1, 7], "topic": "肩线", "fact": "肩线收窄"}]}

        self.run_stage("judge", ai_fn=rescue)
        blocks = read(self.root / "v2" / "candidates.json")["blocks"]
        self.assertEqual(blocks[0]["ids"], [1, 7])
        self.assertNotIn(2, blocks[0]["ids"])

    def test_manual_invalidation_retains_audit_and_recomputes_only_downstream(self):
        self.prepare_plan()
        self.run_stage("review")
        before = read(self.root / "v2" / "usage.json")["total_calls"]
        invalidate_stage(self.root, "order")
        self.assertTrue((self.root / "v2" / "candidates.json").exists())
        self.assertFalse((self.root / "v2" / "review.json").exists())
        self.run_stage("order")
        self.run_stage("review")
        self.assertEqual(read(self.root / "v2" / "usage.json")["total_calls"], before + 2)


class V2GuardsTest(unittest.TestCase):
    def test_condition_can_be_replaced_by_equivalent_cross_location_source(self):
        rows = [row(1, "你搭丝绒裤的话"), row(2, "特别显高显瘦。"),
                row(3, "搭牛仔裤会更休闲。"), row(9, "搭丝绒裤，")]
        wrong = make_block({"ids": [3, 2], "topic": "搭配", "fact": "显瘦"}, rows)
        self.assertIn("changed_meaning", block_issues(wrong))
        right = make_block({"ids": [9, 2], "topic": "搭配", "fact": "丝绒搭配显瘦"}, rows)
        self.assertEqual(block_issues(right), [])

    def test_complete_expression_export_keeps_cross_location_clips_together(self):
        rows = [row(1, "肩线"), row(9, "往里收，肩膀显得窄。")]
        block = make_block({"ids": [9, 1], "topic": "肩线", "fact": "肩线"}, rows)
        clips = compile_clips([block], rows)
        self.assertEqual(len(clips), 2)
        groups = group_segments_for_export(build_segments(clips))
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 2)

    def test_shared_clause_cannot_be_used_twice(self):
        b1 = make_block({"ids": [1], "topic": "版型", "fact": "肩线"}, ROWS)
        b2 = make_block({"ids": [1, 2], "topic": "版型", "fact": "肩线和弹力"}, ROWS)
        with self.assertRaisesRegex(PipelineError, "重复使用"):
            plan_blocks({"selected": [b1["id"], b2["id"]], "reserve": []}, [b1, b2])

    def test_boolean_id_and_extra_schema_fields_fail(self):
        with self.assertRaises(ValueError):
            schemas.validate({"blocks": [{"ids": [True], "topic": "版型", "fact": "肩线"}],
                              "fragments": [], "rejected": []}, schemas.EXTRACT)
        with self.assertRaises(ValueError):
            schemas.validate({"selected": ["b1"], "reserve": [], "text": "新写口播"}, schemas.PLAN)

    def test_known_bad_examples_are_detected_without_banning_garment_words(self):
        for text in ("我给你们穿那个米吧。", "也是那种就正常来说", "原版的材质。",
                     "小个子选5分还是8分，哎我们没有8分，吓我一跳。"):
            block = {"text": text, "constraints": []}
            self.assertTrue(block_issues(block), text)
        self.assertFalse(block_issues({"text": "搭牛仔裤会更休闲，搭西装裤会更正式。", "constraints": []}))


class V2UsageTest(unittest.TestCase):
    def test_failed_calls_are_recorded_and_not_retried_on_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            fn = Mock(side_effect=RuntimeError("test failure"))
            calls = Calls(Path(temp), "auto", caller=fn)
            for _ in range(2):
                with self.assertRaises(AIReturnError):
                    calls.call("review", "audit", "测试", schemas.REVIEW)
            self.assertEqual(fn.call_count, 1)
            usage = read(Path(temp) / "usage.json")
            self.assertEqual(usage["total_calls"], 1)
            self.assertEqual(usage["unknown_usage_calls"], 1)

    def test_cumulative_budget_blocks_following_call(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"PIPELINE_V2_MAX_OUTPUT_TOKENS": "10"}):
            fn = Mock(return_value={"data": {"issues": []}, "usage": {"input_tokens": 10, "output_tokens": 11}})
            calls = Calls(Path(temp), "auto", caller=fn)
            with self.assertRaisesRegex(AIReturnError, "超出预算"):
                calls.call("review", "a", "测试", schemas.REVIEW)
            with self.assertRaisesRegex(AIReturnError, "预算已用完"):
                calls.call("review", "b", "测试2", schemas.REVIEW)
            self.assertEqual(fn.call_count, 1)
            self.assertEqual(read(Path(temp) / "usage.json")["output_tokens"], 11)

    def test_prompt_limit_stops_without_calling_ai(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"PIPELINE_V2_PROMPT_CHARS": "5"}):
            fn = Mock()
            with self.assertRaisesRegex(AIReturnError, "字符预算"):
                Calls(Path(temp), "auto", caller=fn).call("review", "a", "123456", schemas.REVIEW)
            fn.assert_not_called()

    def test_hidden_cli_usage_is_checked_even_when_data_exists(self):
        # A single result reports hidden recovery usage. Never launch the installed CLI.
        from agent_video.ai import WorkBuddyCli
        terminal = {"type": "result", "structured_output": {"issues": []},
                    "usage": {"input_tokens": 500, "output_tokens": 18000}, "is_error": False}

        class Process:
            def __init__(self):
                self.stdin = io.StringIO()
                self.stdout = io.StringIO(json.dumps(terminal) + "\n")
                self.stderr = io.StringIO()
                self.returncode = 0
            def poll(self):
                return 0

        with tempfile.TemporaryDirectory() as temp:
            calls = Calls(Path(temp), "auto")
            provider = WorkBuddyCli(Path("fake-cli"))
            with patch.object(provider, "_ensure_available"), patch.object(provider, "validate_model"), \
                 patch("agent_video.pipeline_v2.ai.subprocess.Popen", return_value=Process()):
                result = calls._workbuddy(provider, "auto", "test", schemas.REVIEW, Path(temp))
            self.assertEqual(result["usage"]["output_tokens"], 18000)
            self.assertIn("超", result["error"])


class V1IsolationTest(unittest.TestCase):
    def test_store_defaults_and_v2_reopen_does_not_archive_existing_database(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "test.db"
            store = Store(path)
            old = store.create_job(title="V1", source_path="fixture", workspace="w1")
            new = store.create_job(title="V2", source_path="fixture", workspace="w2", pipeline_version="v2")
            self.assertEqual([s["stage_id"] for s in store.get_job(old)["stages"]], [s[0] for s in STAGE_DEFINITIONS])
            self.assertEqual(store.get_job(old)["pipeline_version"], "v1")
            with self.assertRaises(ValueError):
                store.update_job(old, pipeline_version="v2")
            reopened = Store(path)
            self.assertIsNotNone(reopened.get_job(old))
            self.assertIn("review", [s["stage_id"] for s in reopened.get_job(new)["stages"]])
            self.assertEqual(list(Path(temp).glob("*.legacy-*")), [])
            reopened.stage_done(new, "order", "done")
            self.assertEqual(reopened.get_job(new)["progress"], 75)
            reopened.stage_done(old, "order", "done")
            self.assertEqual(reopened.get_job(old)["progress"], 80)

    def test_migration_adds_default_without_resetting_old_jobs(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "test.db"
            store = Store(path)
            jid = store.create_job(title="旧任务", source_path="fixture", workspace="w")
            store.update_job(jid, status="completed", progress=100)
            with sqlite3.connect(path) as con:
                con.execute("ALTER TABLE jobs DROP COLUMN pipeline_version")
            con.close()
            job = Store(path).get_job(jid)
            self.assertEqual(job["pipeline_version"], "v1")
            self.assertEqual(job["status"], "completed")

    def test_v2_review_rerun_does_not_reset_upstream(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / "test.db")
            jid = store.create_job(title="V2", source_path="fixture", workspace="w", pipeline_version="v2")
            for stage in ("asr", "filter", "judge", "order", "review"):
                store.stage_done(jid, stage, "done")
            store.prepare_stage_rerun(jid, "review")
            states = {s["stage_id"]: s["status"] for s in store.get_job(jid)["stages"]}
            self.assertEqual(states["order"], "succeeded")
            self.assertEqual(states["review"], "pending")

    def test_api_creates_version_per_job_and_rejects_unknown_version(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "fixture.mp4"
            source.write_bytes(b"fixture")
            app = Application(root)
            v1 = app.create_job({"source_path": str(source)})
            v2 = app.create_job({"source_path": str(source), "pipeline_version": "V2"})
            self.assertEqual(v1["pipeline_version"], "v1")
            self.assertEqual(v2["pipeline_version"], "v2")
            with self.assertRaises(ValueError):
                app.create_job({"source_path": str(source), "pipeline_version": "v99"})
            self.assertEqual(app.store.get_job(v1["id"])["pipeline_version"], "v1")

    def test_v2_resume_stays_at_review_instead_of_restarting_the_whole_pipeline(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / "test.db")
            jid = store.create_job(title="V2", source_path="fixture", workspace=str(root / "w"), pipeline_version="v2")
            store.update_job(jid, status="paused", run_stage="review")
            runner = JobRunner(store, root)
            runner.resume(jid)
            self.assertEqual(store.get_job(jid)["run_stage"], "review")

    def test_v2_child_never_dispatches_to_v1_downstream(self):
        with tempfile.TemporaryDirectory() as temp:
            q = Mock()
            with patch("agent_video.pipeline_v2.run.run_pipeline", return_value={"pipeline_version": "v2"}) as v2, \
                 patch("agent_video.runner.run_pipeline") as v1, patch("agent_video.runner.run_pipeline_stage") as v1_stage:
                _run_pipeline_child(q, "fixture", temp, (110, 125), pipeline_version="v2")
            v2.assert_called_once()
            v1.assert_not_called()
            v1_stage.assert_not_called()
            self.assertEqual(q.put.call_args.args[0][0], "success")

    def test_reuses_actual_v1_s1_s2_without_calling_v1_ai_or_renderer(self):
        with tempfile.TemporaryDirectory() as temp:
            words = [{"w": "肩线往里收，肩膀显得窄。", "s": 0, "e": 3},
                     {"w": "面料有弹力，抬手不会紧。", "s": 4, "e": 7}]
            with patch("agent_video.pipeline.run.ai_call") as ai, patch("agent_video.pipeline.run.render_video") as renderer:
                result = prepare("fixture.mp4", Path(temp), transcript=([], words))
            self.assertGreater(len(result["clauses"]), 0)
            ai.assert_not_called()
            renderer.assert_not_called()


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg unavailable")
class V2RenderTest(unittest.TestCase):
    def test_full_pipeline_with_real_shared_s1_s2_and_ffmpeg(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            media = root / "fixture.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=blue:s=160x240:r=25:d=7",
                            "-f", "lavfi", "-i", "sine=duration=7", "-c:v", "libx264", "-c:a", "aac", str(media)],
                           check=True, capture_output=True)
            work = root / "work"
            words = [{"w": "肩线往里收，肩膀显得窄。", "s": 0, "e": 3},
                     {"w": "面料有弹力，抬手不会紧。", "s": 3, "e": 6}]

            def ai(model, prompt, schema, timeout):
                if schema is schemas.EXTRACT:
                    ids = [r["id"] for r in read(work / "clauses.filtered.json")["clauses"] if r["usable"]]
                    return {"blocks": [{"ids": ids, "topic": "版型面料", "fact": "肩线收窄且面料有弹力"}],
                            "fragments": [], "rejected": []}
                if schema is schemas.PLAN:
                    ids = [b["id"] for b in read(work / "v2" / "candidates.json")["blocks"]]
                    return {"selected": ids, "reserve": []}
                return {"issues": []}

            manifest = run_pipeline(str(media), str(work), target_seconds=(5.5, 6.5),
                                    transcript=([], words), ai_fn=ai)
            output = work / manifest["output"]
            self.assertTrue(output.is_file())
            duration = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                             "-of", "default=nw=1:nk=1", str(output)],
                                            capture_output=True, text=True, check=True).stdout.strip())
            self.assertAlmostEqual(duration, 6, delta=.4)
            self.assertEqual(len(group_segments_for_export(manifest["segments"])), 1)


if __name__ == "__main__":
    unittest.main()
