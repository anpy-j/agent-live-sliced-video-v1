import importlib
import json

import pytest

from agent_video.jianying_paths import resolve_draft_media_placeholder


@pytest.mark.parametrize("module", ["agent_video.timeline", "agent_video.smart_v3.draft_timeline"])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("separator", ["/", "\\"])
def test_placeholder_import_uses_project_root(tmp_path, module, nested, separator):
    media = tmp_path / "Resources" / "local" / "video.mp4"
    media.parent.mkdir(parents=True)
    media.touch()
    normal = tmp_path / "original.mp4"
    normal.touch()
    draft_dir = tmp_path / "Timelines" / "timeline-id" if nested else tmp_path
    draft_dir.mkdir(parents=True, exist_ok=True)
    placeholder = separator.join(["##_draftpath_placeholder_project-id_##", "Resources", "local", "video.mp4"])
    data = {
        "materials": {"videos": [{"id": "placeholder", "path": placeholder}, {"id": "normal", "path": str(normal)}]},
        "tracks": [{"type": "video", "segments": [
            {"material_id": material_id,
             "source_timerange": {"start": 0, "duration": 1000000},
             "target_timerange": {"start": index * 1000000, "duration": 1000000}}
            for index, material_id in enumerate(["placeholder", "normal"])
        ]}],
    }
    draft = draft_dir / "draft_content.json"
    draft.write_text(json.dumps(data), encoding="utf-8")
    timeline = importlib.import_module(module).load_virtual_timeline(draft)
    assert timeline.segments[0].source_path == str(media.resolve())
    assert timeline.segments[1].source_path == str(normal.resolve())


def test_placeholder_requires_draft_context():
    with pytest.raises(ValueError, match="草稿文件路径"):
        resolve_draft_media_placeholder("##_draftpath_placeholder_id_##/Resources/video.mp4", None)


def test_ordinary_paths_are_unchanged(tmp_path):
    for path in [str(tmp_path / "video.mp4"), r"E:\切片\原片.mp4", "relative.mp4", ""]:
        assert resolve_draft_media_placeholder(path, tmp_path) == path
