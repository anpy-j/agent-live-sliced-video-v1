import json
from unittest.mock import patch

import pytest

from agent_video.jianying import _discovery_cache, list_jianying_drafts
from agent_video.jianying_crypto import JianyingDecryptError, decrypt_jianying_file
from agent_video.timeline import discover_virtual_timelines


def test_decrypt_retries_other_installed_version(tmp_path, monkeypatch):
    apps = tmp_path / "JianyingPro" / "Apps"
    versions = [apps / "11.7", apps / "11.6"]
    for version in versions:
        version.mkdir(parents=True)
        (version / "videoeditor.dll").touch()
    source = tmp_path / "draft_content.json"
    source.write_bytes(b"encrypted")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    with patch("agent_video.jianying_crypto.sys.platform", "win32"), patch(
        "agent_video.jianying_crypto._decrypt_with_install",
        side_effect=[JianyingDecryptError("empty result"), {"tracks": []}],
    ) as read:
        assert decrypt_jianying_file(source) == {"tracks": []}
        assert [call.args[1] for call in read.call_args_list] == versions
    assert source.read_bytes() == b"encrypted"


def test_failure_cache_retries_without_draft_changes(tmp_path):
    draft = tmp_path / "draft"
    draft.mkdir()
    (tmp_path / "root_meta_info.json").write_text(json.dumps({
        "all_draft_store": [{"draft_fold_path": str(draft)}],
    }), encoding="utf-8")
    _discovery_cache.clear()
    try:
        with patch("agent_video.timeline.discover_virtual_timelines", side_effect=[
            ValueError("temporary decrypt failure"),
            {"timelines": [{"timeline_id": "one", "timeline_duration": 10}]},
        ]) as discover:
            assert list_jianying_drafts(tmp_path)[0].get("timeline_error")
            assert list_jianying_drafts(tmp_path)[0]["recommended_timeline"]
            list_jianying_drafts(tmp_path)
            assert discover.call_count == 2
    finally:
        _discovery_cache.clear()


def test_empty_child_does_not_hide_populated_timeline(tmp_path):
    for name, data in [
        ("empty", {"materials": {}}),
        ("populated", {"segments": [{"timeline_start": 0, "timeline_end": 30,
                                     "source_start": 0, "source_end": 30,
                                     "source_path": "video.mp4"}]}),
    ]:
        child = tmp_path / "Timelines" / name
        child.mkdir(parents=True)
        (child / "draft_content.json").write_text(json.dumps(data), encoding="utf-8")
    timelines = discover_virtual_timelines(tmp_path)["timelines"]
    assert [item["timeline_id"] for item in timelines] == ["populated"]
    assert timelines[0]["timeline_duration"] == 30


def test_unreadable_child_is_not_silently_skipped(tmp_path):
    child = tmp_path / "Timelines" / "broken"
    child.mkdir(parents=True)
    (child / "draft_content.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="文件格式不符合"):
        discover_virtual_timelines(tmp_path)
