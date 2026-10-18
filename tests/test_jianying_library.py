import json
import tempfile
import unittest
from pathlib import Path

from agent_video.jianying_library import entry_for, load_library, update_draft


class JianyingLibraryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "jianying_library.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file_returns_empty_library(self):
        self.assertEqual(load_library(self.path), {"folders": [], "drafts": {}})
        self.assertEqual(entry_for(self.path, "a"), {"folder": "", "note": ""})

    def test_update_persists_folder_and_note(self):
        result = update_draft(self.path, "draft-a", folder="服装", note="已发布")

        self.assertEqual(result["folder"], "服装")
        self.assertEqual(result["note"], "已发布")
        self.assertEqual(result["folders"], ["服装"])
        self.assertEqual(entry_for(self.path, "draft-a"), {"folder": "服装", "note": "已发布"})

    def test_folders_are_deduplicated_case_insensitively(self):
        update_draft(self.path, "a", folder="服装")
        update_draft(self.path, "b", folder="服装")

        self.assertEqual(load_library(self.path)["folders"], ["服装"])

    def test_only_provided_fields_change(self):
        update_draft(self.path, "a", folder="美妆", note="钩子")

        update_draft(self.path, "a", folder="护肤")

        self.assertEqual(entry_for(self.path, "a"), {"folder": "护肤", "note": "钩子"})
        self.assertIn("美妆", load_library(self.path)["folders"])
        self.assertIn("护肤", load_library(self.path)["folders"])

    def test_clearing_all_fields_removes_entry(self):
        update_draft(self.path, "a", folder="美妆", note="钩子")

        update_draft(self.path, "a", folder="", note="")

        self.assertEqual(load_library(self.path)["drafts"], {})
        self.assertEqual(entry_for(self.path, "a"), {"folder": "", "note": ""})

    def test_note_is_trimmed_and_folder_name_normalized(self):
        result = update_draft(self.path, "a", folder="  多个   空格  ", note="  前后留白  ")

        self.assertEqual(result["folder"], "多个 空格")
        self.assertEqual(result["note"], "前后留白")

    def test_blank_draft_id_is_rejected(self):
        with self.assertRaises(ValueError):
            update_draft(self.path, "  ", folder="美妆")

    def test_corrupt_file_is_ignored(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{ not json", encoding="utf-8")

        self.assertEqual(load_library(self.path), {"folders": [], "drafts": {}})

        update_draft(self.path, "a", folder="美妆")
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["folders"], ["美妆"])


if __name__ == "__main__":
    unittest.main()
