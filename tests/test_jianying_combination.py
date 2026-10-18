"""剪映组合片段（compound clip）解析：组合片段外壳没有素材路径，必须展开内嵌 draft。"""
import importlib
import json

import pytest

MODULES = ["agent_video.timeline", "agent_video.smart_v3.draft_timeline"]


def _combination_draft(tmp_path):
    """构造一个外层只有组合片段、内层有两段真实素材的草稿。"""
    inner_placeholder = tmp_path / "subdraft" / "sub-1" / "materials" / "video" / "inner.mp4"
    inner_placeholder.parent.mkdir(parents=True)
    inner_placeholder.touch()
    inner_local = tmp_path / "materials" / "video" / "local.mp4"
    inner_local.parent.mkdir(parents=True)
    inner_local.touch()

    inner = {
        "materials": {
            "videos": [
                {
                    "id": "inner-1",
                    "path": "##_draftpath_placeholder_project_##/subdraft/sub-1/materials/video/inner.mp4",
                    "material_name": "inner.mp4",
                },
                {"id": "inner-2", "path": str(inner_local), "material_name": "local.mp4"},
            ],
            "speeds": [],
        },
        "tracks": [{
            "type": "video",
            "segments": [
                {
                    "material_id": "inner-1",
                    "source_timerange": {"start": 100_000_000, "duration": 8_000_000},
                    "target_timerange": {"start": 0, "duration": 8_000_000},
                },
                {
                    "material_id": "inner-2",
                    "source_timerange": {"start": 200_000_000, "duration": 12_000_000},
                    "target_timerange": {"start": 8_000_000, "duration": 12_000_000},
                },
            ],
        }],
    }
    data = {
        "materials": {
            "videos": [{"id": "combo-video", "path": "", "material_name": "白衬衫组合片段"}],
            "speeds": [],
            "drafts": [{"id": "combo-mat", "type": "combination",
                        "combination_id": "combo-1", "draft": inner}],
        },
        "tracks": [{
            "type": "video",
            "segments": [{
                "material_id": "combo-video",
                "source_timerange": {"start": 0, "duration": 20_000_000},
                # 外层 2 倍速（源 20s 压到时间线 10s），起点偏移 5s
                "target_timerange": {"start": 5_000_000, "duration": 10_000_000},
                "extra_material_refs": ["combo-mat"],
            }],
        }],
    }
    draft_file = tmp_path / "draft_content.json"
    draft_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return draft_file, inner_placeholder.resolve(), inner_local.resolve()


@pytest.mark.parametrize("module", MODULES)
def test_combination_segment_expands_to_real_materials(tmp_path, module):
    draft_file, placeholder_resolved, local = _combination_draft(tmp_path)
    timeline = importlib.import_module(module).load_virtual_timeline(draft_file)

    assert len(timeline.segments) == 2
    first, second = timeline.segments
    assert first.segment_id == "片段1"
    assert first.source_path == str(placeholder_resolved)
    assert (first.timeline_start, first.timeline_end) == (5.0, 9.0)
    assert (first.source_start, first.source_end) == (100.0, 108.0)
    assert first.speed == pytest.approx(2.0)

    assert second.segment_id == "片段2"
    assert second.source_path == str(local)
    assert (second.timeline_start, second.timeline_end) == (9.0, 15.0)
    assert (second.source_start, second.source_end) == (200.0, 212.0)
    assert second.speed == pytest.approx(2.0)


@pytest.mark.parametrize("module", MODULES)
def test_combination_segment_is_clipped_by_outer_timerange(tmp_path, module):
    draft_file, placeholder_resolved, _ = _combination_draft(tmp_path)
    data = json.loads(draft_file.read_text(encoding="utf-8"))
    # 外层只引用组合片段的第 6–16 秒，且起点落在时间线 0
    segment = data["tracks"][0]["segments"][0]
    segment["source_timerange"] = {"start": 6_000_000, "duration": 10_000_000}
    segment["target_timerange"] = {"start": 0, "duration": 10_000_000}
    draft_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    timeline = importlib.import_module(module).load_virtual_timeline(draft_file)
    first, second = timeline.segments
    # 内层第一段 0–8s 被外层裁掉，只保留 8–10s 的尾巴
    assert first.source_path == str(placeholder_resolved)
    assert (first.timeline_start, first.timeline_end) == (0.0, 2.0)
    assert (first.source_start, first.source_end) == (106.0, 108.0)
    assert second.source_path.endswith("local.mp4")
    assert (second.timeline_start, second.timeline_end) == (2.0, 10.0)
    assert (second.source_start, second.source_end) == (200.0, 208.0)


@pytest.mark.parametrize("module", MODULES)
def test_missing_material_path_reports_readable_error(tmp_path, module):
    data = {
        "materials": {"videos": [{"id": "cloud", "path": "", "material_name": "云端素材"}]},
        "tracks": [{
            "type": "video",
            "segments": [{
                "material_id": "cloud",
                "source_timerange": {"start": 0, "duration": 1_000_000},
                "target_timerange": {"start": 0, "duration": 1_000_000},
            }],
        }],
    }
    draft_file = tmp_path / "draft_content.json"
    draft_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    module_obj = importlib.import_module(module)
    with pytest.raises(ValueError) as excinfo:
        module_obj.load_virtual_timeline(draft_file)
    message = str(excinfo.value)
    assert "片段1" in message
    assert str(tmp_path) not in message


@pytest.mark.parametrize("module", MODULES)
def test_empty_source_path_stays_empty_when_restoring_segments(tmp_path, module):
    module_obj = importlib.import_module(module)
    segment = module_obj.TimelineSegment.from_dict({
        "segment_id": "片段1",
        "timeline_start": 0.0,
        "timeline_end": 1.0,
        "source_path": "",
        "source_start": 0.0,
        "source_end": 1.0,
        "speed": 1.0,
    })
    assert segment.source_path == ""
