import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from agent_video.repetition_rules import match_rows, trial_options
from agent_video.server import Application, Server


class RepetitionRulesTest(unittest.TestCase):
    def match(self, texts, **options):
        rows = [{"id": i, "text": text, "start": i * 3, "end": i * 3 + 3,
                 "usable": True} for i, text in enumerate(texts)]
        return match_rows(rows, trial_options(options))

    def test_meaningful_phrase_matches_first_and_twentieth_clause_with_offsets(self):
        texts = ["这一件超级的难做"] + [f"第{i}段独立信息" for i in range(1, 19)] + ["超级难做"]
        result = self.match(texts)
        self.assertEqual(result["matched"], 2)
        self.assertEqual(result["group_count"], 1)
        group = result["groups"][0]
        self.assertEqual((group["phrase"], group["count"]), ("超级难做", 2))
        self.assertEqual([m["id"] for m in group["occurrences"]], [0, 19])
        hit = result["rows"][0]["hits"][0]
        self.assertEqual((hit["start"], hit["end"], hit["text"]), (3, 8, "超级的难做"))
        self.assertTrue(hit["is_first"])
        self.assertEqual(result["rows"][19]["hits"][0]["reference"]["id"], 0)
        self.assertEqual(result["newly_rejected"], 0)  # A partial phrase never rejects the whole row.

    def test_oral_emphasis_and_overlapping_characters_do_not_count(self):
        result = self.match(["是不是超级超级好看", "有没有没买到我这个粉色羽绒服的",
                             "好看好看好看", "非常的好看；非常好看"])
        self.assertEqual(result["matched"], 0)
        self.assertEqual(result["groups"], [])
        self.assertEqual(self.match(["超级难做。超级难做。"], min_count=2)["matched"], 0)
        self.assertEqual(self.match(["超级难做。超级难做。", "超级难做"], min_count=3)["matched"], 0)

    def test_whitelisted_subjects_and_modifiers_preserve_original_text(self):
        result = self.match(["我觉得这件非常的保暖", "非常保暖", "面料是柔软的", "这个面料柔软"], mode="word")
        self.assertEqual({g["phrase"] for g in result["groups"]}, {"非常保暖", "面料柔软"})
        self.assertEqual(result["matched"], 4)
        self.assertEqual(self.match(["超级难做", "超级难做"], mode="word", min_word_length=5)["matched"], 0)
        self.assertEqual(self.match(["超级难做", "超级难做", "超级的难做"], min_count=3)["matched"], 3)
        self.assertEqual(self.match(["特别好看", "特别保暖"], mode="word")["matched"], 0)

    def test_negation_colors_products_parts_and_ambiguous_contexts(self):
        for texts in (["超级难做", "不是超级难做"], ["非常好看", "不是非常的好看"],
                      ["粉色这件超级难做", "白色这件超级难做"],
                      ["粉色这件，超级难做", "白色这件，超级难做"],
                      ["羽绒服超级难做", "毛衣超级难做"], ["袖子超级难做", "领口超级难做"],
                      ["粉色超级难做", "白色超级难做", "超级难做"]):
            with self.subTest(texts=texts):
                self.assertEqual(self.match(texts, mode="word")["matched"], 0)
        result = self.match(["粉色超级难做", "超级难做"], mode="word")
        self.assertEqual(result["matched"], 2)
        self.assertEqual(result["groups"][0]["context"], {"颜色": "粉色"})

    def test_global_sentences_include_first_and_preserve_negation(self):
        result = self.match(["这件面料柔软。", "这件，面料 柔软！", "这件面料柔软？",
                             "面料非常柔软。", "OK好看；ok好看"], mode="sentence")
        self.assertEqual(result["sentence_matched"], 3)
        self.assertTrue(result["rows"][0]["hits"][0]["is_first"])
        hit = result["rows"][1]["hits"][0]
        self.assertEqual((hit["count"], hit["reference"]["id"]), (3, 0))
        self.assertEqual(hit["text"], "这件，面料 柔软")
        self.assertFalse(result["rows"][3]["hits"])
        self.assertFalse(result["rows"][4]["hits"])
        self.assertEqual(self.match(["好看。", "好看。"], mode="sentence")["matched"], 0)
        self.assertEqual(self.match(["面料很柔软", "面料很柔软"], min_count=3)["matched"], 0)
        self.assertEqual(self.match(["超级的难做", "超级难做"], mode="sentence")["matched"], 2)
        self.assertEqual(self.match(["买到了粉色羽绒服", "没买到粉色羽绒服"])["matched"], 0)

    def test_chronology_all_rows_and_unchanged_input(self):
        rows = [{"id": "later", "text": "面料非常柔软", "start": 9, "usable": True},
                {"id": "removed", "text": "面料非常柔软", "start": 1, "usable": False, "reason": "duplicate"},
                {"id": "first-kept", "text": "面料非常柔软", "start": 3, "usable": True}]
        original = json.dumps(rows)
        result = match_rows(rows, trial_options({}))
        self.assertFalse(result["rows"][0]["hits"])
        self.assertEqual(result["rows"][2]["hits"][0]["reference"]["id"], "first-kept")
        self.assertEqual((result["total"], result["eligible"], result["excluded"],
                          result["matched"], result["newly_rejected"]), (3, 2, 1, 2, 1))
        self.assertEqual(json.dumps(rows), original)
        self.assertEqual(self.match(["", "…", "普通内容"])["matched"], 0)

    def test_rejected_question_cannot_seed_duplicate_in_any_mode(self):
        rows = [{"id": 2, "text": "可以水洗吗?", "start": 6.93, "end": 7.63,
                 "usable": False, "reason": "duration_gate"},
                {"id": 66, "text": "可以水洗吗", "start": 173.02, "end": 173.98, "usable": True}]
        for mode in ("all", "word", "sentence"):
            with self.subTest(mode=mode):
                result = match_rows(rows, trial_options({"mode": mode}))
                self.assertEqual(result["groups"], [])
                self.assertEqual((result["eligible"], result["excluded"], result["matched"]), (1, 1, 0))
                self.assertTrue(all(not r["hits"] for r in result["rows"]))
        rows[0]["usable"] = True
        self.assertEqual(match_rows(rows, trial_options({}))["matched"], 2)

    def test_rejected_phrases_do_not_affect_thresholds_or_object_context(self):
        rows = [{"id": 0, "text": "白色超级难做", "start": 0, "usable": False},
                {"id": 1, "text": "粉色超级的难做", "start": 1, "usable": True},
                {"id": 2, "text": "超级难做", "start": 2, "usable": True}]
        result = match_rows(rows, trial_options({"mode": "word"}))
        self.assertEqual(result["groups"][0]["context"], {"颜色": "粉色"})
        self.assertEqual([m["id"] for m in result["groups"][0]["occurrences"]], [1, 2])
        self.assertEqual(match_rows(rows, trial_options({"min_count": 3}))["matched"], 0)
        for row in rows:
            row["usable"] = False
        self.assertEqual(match_rows(rows, trial_options({}))["groups"], [])

    def test_invalid_options(self):
        for payload in ({"mode": "semantic"}, {"min_count": 1}, {"min_count": True},
                        {"min_word_length": "2"}, {"min_sentence_length": 201}, {"min_count": 2.5}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                trial_options(payload)

    def test_color_information_point_matches_three_different_expressions(self):
        result = self.match(["5分裤一共两个色，一个黑一个咖。",
                             "我们给大家两个颜色，我现在穿的黑色。", "裤子黑跟咖两个色。"], mode="fact")
        self.assertEqual((result["group_count"], result["fact_matched"]), (1, 3))
        group = result["groups"][0]
        self.assertEqual(group["phrase"], "可选颜色：2种")
        self.assertEqual(group["context"]["商品"], "裤子")
        self.assertEqual(group["occurrences"][1]["text"], "两个颜色")
        self.assertEqual(group["occurrences"][1]["fact"]["complete_colors"], [])
        self.assertEqual(result["newly_rejected"], 0)

    def test_actual_asr_variants_traditional_text_and_other_product(self):
        result = self.match(["五分裤两个色,一个黑,然后一个看。",
                             "我们给大家两个颜色我现在穿的是黑色", "褲子黑跟卡兩個色",
                             "我现在给到的这件皮衣两个色"], mode="fact")
        self.assertEqual((result["group_count"], result["fact_matched"]), (1, 3))
        members = result["groups"][0]["occurrences"]
        self.assertEqual([m["id"] for m in members], [0, 1, 2])
        self.assertEqual(members[0]["fact"]["observed_colors"], ["黑色"])
        self.assertEqual(members[0]["fact"]["complete_colors"], [])  # Never rewrite 看 into 咖.
        self.assertEqual(members[2]["text"], "兩個色")
        self.assertEqual(members[2]["fact"]["complete_colors"], ["咖色", "黑色"])
        self.assertFalse(result["rows"][3]["hits"])

    def test_color_fact_conflicts_questions_stock_and_product_switches(self):
        for texts in (["裤子两个色黑色咖色", "裤子三个颜色"],
                      ["裤子两个色黑色咖色", "裤子两个色白色黑色"],
                      ["裤子两个色", "毛衣两个色"], ["裤子两个色", "裤子只剩两个颜色"],
                      ["裤子两个色", "裤子不是两个颜色"], ["裤子两个色", "裤子有两个颜色吗？"],
                      ["裤子两个色", "下一款是毛衣", "我们给大家两个颜色"]):
            with self.subTest(texts=texts):
                self.assertEqual(self.match(texts, mode="fact")["group_count"], 0)
        result = self.match(["裤子两个色黑色咖色", "裤子两个色白色黑色", "两个颜色黑色"], mode="fact")
        self.assertEqual(result["group_count"], 0)  # Partial black mention cannot bridge two sets.

    def test_rejected_color_facts_do_not_change_scope_or_repetition_count(self):
        rows = [{"id": 0, "text": "裤子两个色", "start": 0, "usable": True},
                {"id": 1, "text": "毛衣两个颜色", "start": 1, "usable": False},
                {"id": 2, "text": "我们给大家两种颜色", "start": 2, "usable": True}]
        result = match_rows(rows, trial_options({"mode": "fact"}))
        self.assertEqual(result["groups"][0]["context"]["商品"], "裤子")
        self.assertEqual([m["id"] for m in result["groups"][0]["occurrences"]], [0, 2])
        self.assertFalse(result["rows"][1]["hits"])
        self.assertEqual(match_rows(rows, trial_options({"mode": "fact", "min_count": 3}))["group_count"], 0)

    def test_http_completed_jobs_legacy_smart_and_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = Application(root)
            source = root / "source.mp4"
            source.write_bytes(b"fixture")
            job = app.create_job({"source_path": str(source), "title": "重复试匹配"})
            file = Path(job["workspace"]) / "clauses.filtered.json"
            file.write_text(json.dumps({"clauses": [
                {"id": 0, "text": "这一件超级的难做", "start": 0, "end": 3, "usable": True},
                {"id": 19, "text": "超级难做", "start": 57, "end": 60, "usable": False}
            ]}), encoding="utf-8")
            before = file.read_bytes()
            with self.assertRaises(ValueError):
                app.test_repetition_rules({"job_id": job["id"]})
            app.store.update_job(job["id"], status="completed")
            smart = app.smart.store.create({"product_name": "测试"})
            smart["status"] = "completed"
            app.smart.store.save(smart)
            workspace = app.smart.workspace(smart["id"])
            workspace.mkdir()
            smart_file = workspace / "S2.json"
            smart_file.write_text(json.dumps({"kept": [{"id": "u1", "text": "面料非常柔软", "start": 3},
                                                       {"id": "u2", "text": "面料非常柔软", "start": 9}],
                                              "removed": [{"id": "u0", "text": "面料非常柔软", "start": 0,
                                                           "filter_reason": "duplicate"}]}), encoding="utf-8")
            smart_before = smart_file.read_bytes()
            server = Server(("127.0.0.1", 0), app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            def request(route, payload=None):
                body = json.dumps(payload).encode() if payload is not None else None
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(urllib.request.Request(
                    f"http://127.0.0.1:{server.server_port}/api/rules/repetition/{route}",
                    data=body, headers={"Content-Type": "application/json"}
                )) as response:
                    return json.loads(response.read())

            try:
                self.assertEqual(len(request("jobs")["jobs"]), 2)
                result = request("test", {"job_id": job["id"]})
                self.assertEqual((result["total"], result["eligible"], result["matched"]), (2, 1, 0))
                self.assertEqual(result["groups"], [])
                result = request("test", {"job_id": smart["id"], "kind": "smart", "mode": "sentence"})
                self.assertFalse(result["rows"][0]["hits"])
                self.assertEqual(result["rows"][2]["hits"][0]["reference"]["id"], "u1")
                self.assertEqual([m["id"] for m in result["groups"][0]["occurrences"]], ["u1", "u2"])
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    request("test", {"job_id": job["id"], "min_count": 0})
                self.assertEqual(caught.exception.code, 400)
                self.assertEqual(file.read_bytes(), before)
                self.assertEqual(smart_file.read_bytes(), smart_before)
                self.assertEqual(app.store.get_job(job["id"])["status"], "completed")
                self.assertFalse((app.data_dir / "repetition_rules.json").exists())
            finally:
                server.shutdown()
                thread.join()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
