import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_video.pipeline import run as pipeline
from agent_video.pipeline.errors import AIReturnError, PipelineError
from agent_video.pipeline.filter import activate_account_vocab
from agent_video.pipeline.render import group_segments_for_export
from agent_video.pipeline.selection import compile_selection, selection_candidates


def clause(cid, text, start, end, usable=True):
    return {"id": cid, "text": text, "start": start, "end": end,
            "usable": usable, "unit": 0}


class SelectionTest(unittest.TestCase):
    def setUp(self):
        activate_account_vocab()
        self.clauses = [clause(0, "这件马甲是羊毛的。", 0, 2),
                        clause(1, "然后再给大家穿一个牛仔裤子搭配", 2, 4),
                        clause(2, "黑灰撞色适合通勤。", 4, 6)]

    def test_s4_can_discard_a_member_of_one_s3_unit(self):
        candidates = selection_candidates(self.clauses)
        self.assertEqual([c["id"] for c in candidates], [0, 1, 2])
        self.assertTrue(all(len(c["members"]) == 1 for c in candidates))
        clips = compile_selection(self.clauses, [{"role": "selling_point", "ids": [0, 2]}])
        self.assertEqual([(c["start"], c["end"]) for c in clips], [(0, 2), (4, 6)])
        self.assertNotIn("给大家", "".join(c["text"] for c in clips))

    def test_s3_rejection_and_missing_s2_ids_break_context(self):
        self.clauses[1]["usable"] = False
        candidates = selection_candidates(self.clauses)
        self.assertEqual([c["id"] for c in candidates], [0, 2])
        self.assertNotEqual(candidates[0]["context"], candidates[1]["context"])
        candidates = selection_candidates([self.clauses[0], self.clauses[2]])
        self.assertNotEqual(candidates[0]["context"], candidates[1]["context"])

    def test_original_version_is_excluded_even_from_stale_s3(self):
        bad = clause(3, "你们可以看一下，这就是原版，看到没。", 6, 8)
        self.assertEqual(selection_candidates([bad]), [])
        with self.assertRaises(AIReturnError):
            compile_selection([bad], [{"role": "proof", "ids": [3]}])

    def test_useful_trouser_styling_is_not_a_hard_ban(self):
        useful = clause(0, "里面搭黑色打底，下面配牛仔裤，黑灰撞色适合通勤。", 0, 3)
        self.assertEqual(len(selection_candidates([useful])), 1)

    def test_adjacent_complete_group_merges_but_other_groups_do_not(self):
        clips = compile_selection(self.clauses, [
            {"role": "selling_point", "ids": [0, 1]}, {"role": "styling", "ids": [2]}])
        self.assertEqual([c["members"] for c in clips], [[0, 1], [2]])

    def test_positive_gap_and_reverse_order_never_merge(self):
        rows = [clause(0, "羊毛马甲", 0, 2), clause(1, "版型宽松", 2.01, 4)]
        for ids in ([0, 1], [1, 0]):
            self.assertEqual(len(compile_selection(rows, [{"role": "selling_point", "ids": ids}])), 2)

    def test_joined_fragments_are_checked_for_hard_vocab(self):
        rows = [clause(0, "这是原", 0, 2), clause(1, "版的材质", 2, 4)]
        with self.assertRaises(AIReturnError):
            compile_selection(rows, [{"role": "proof", "ids": [0, 1]}])

    def test_lantiao_skin_feel_group_does_not_invent_order_chatter(self):
        rows = [clause(70, "亲服感非常强", 231.57, 232.61),
                clause(71, "双面贴在身上", 232.61, 233.71),
                clause(72, "单纯算一点都不会扎嗯", 233.71, 235.59)]
        sections = [{"role": "selling_point", "ids": [70, 71, 72]}]
        order = pipeline._selection_record("双面呢", [70, 71, 72], 4.02, sections, rows)
        clips, total, _ = pipeline._resolve_render_selection(order, rows, (120, 180), 1)
        self.assertEqual(clips[0]["members"], [70, 71, 72])
        self.assertEqual(clips[0]["text"], "亲服感非常强双面贴在身上单纯算一点都不会扎嗯")
        self.assertAlmostEqual(total, 4.02)

    def test_real_order_chatter_across_clauses_is_still_rejected(self):
        rows = [clause(0, "来给大家上", 0, 2), clause(1, "单", 2, 4)]
        with self.assertRaisesRegex(AIReturnError, "命中「上单」"):
            compile_selection(rows, [{"role": "proof", "ids": [0, 1]}])

    def test_wearing_description_is_also_allowed_before_s4(self):
        for text in ("贴在身上单纯不扎", "穿在身上单薄但保暖", "上身上单穿很好看"):
            self.assertEqual(len(selection_candidates([clause(0, text, 0, 2)])), 1)
        for text in ("来上单", "上单了姐妹们", "身上这件上单了"):
            self.assertEqual(selection_candidates([clause(0, text, 0, 2)]), [])

    def test_legacy_rerender_keeps_rejected_clause_as_barrier(self):
        self.clauses[1]["usable"] = False
        clips, total, _ = pipeline._resolve_render_selection(
            {"ordered_ids": [0, 2]}, self.clauses, (110, 120), 1)
        self.assertEqual([(c["start"], c["end"]) for c in clips], [(0, 2), (4, 6)])
        self.assertEqual(total, 4)
        with self.assertRaises(PipelineError):
            pipeline._resolve_render_selection({"ordered_ids": [0, 99]}, self.clauses, (110, 120), 1)

    def test_changed_snapshot_or_upstream_data_requires_new_s4(self):
        sections = [{"role": "selling_point", "ids": [0, 2]}]
        order = pipeline._selection_record("马甲", [0, 2], 4, sections, self.clauses)
        changed = copy.deepcopy(order)
        changed["selected_clauses"][0]["end"] = 4
        with self.assertRaises(PipelineError):
            pipeline._resolve_render_selection(changed, self.clauses, (110, 120), 1)
        self.clauses[0]["text"] = "修改后的口播"
        with self.assertRaises(PipelineError):
            pipeline._resolve_render_selection(order, self.clauses, (110, 120), 1)

    def test_stage_order_and_rerender_use_identical_selection_without_extra_ai(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            (work / "timeline.json").write_text(json.dumps({"duration": 6, "clauses": self.clauses}), encoding="utf-8")
            answer = {"main_product": "马甲", "sections": [{"role": "selling_point", "ids": [0, 2]}],
                      "ordered_ids": [0, 2]}
            with patch.object(pipeline, "ai_call", return_value=answer) as ai:
                order = pipeline.run_pipeline_stage("source.mp4", temp, "order", target_seconds=(110, 120))
                self.assertEqual(ai.call_count, 1)
                prompt = ai.call_args.args[1]
                payload = json.loads(prompt.split("S3候选（按原始时间序，JSON）：\n")[1])
                self.assertEqual([r["id"] for r in payload], [0, 1, 2])
                self.assertTrue(all(set(r) == {"id", "text", "seconds", "context"} for r in payload))
            snapshot = (work / "order.json").read_bytes()
            with patch.object(pipeline, "ai_call") as ai, patch.object(pipeline, "render_video") as render:
                result = pipeline.run_pipeline_stage("source.mp4", temp, "render", target_seconds=(110, 120))
                ai.assert_not_called()
                self.assertEqual([(r["start"], r["end"]) for r in render.call_args.args[1]], [(0, 2), (4, 6)])
            self.assertEqual(snapshot, (work / "order.json").read_bytes())
            self.assertEqual(result["total_seconds"], order["total_seconds"])
            self.assertEqual(len(group_segments_for_export(result["segments"])), 2)


if __name__ == "__main__":
    unittest.main()
