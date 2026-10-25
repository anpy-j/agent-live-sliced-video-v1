import copy
import json
from unittest.mock import patch

import pytest

from agent_video.pipeline import run as pipeline
from agent_video.pipeline.errors import AIReturnError


def candidates():
    return [dict(id=10, start=0, end=3), dict(id=30, start=3, end=6)]


def order(hook=None, body=None, ordered=None):
    return {"main_product": "羽绒服", "sections": [
        {"role": "hook", "ids": hook if hook is not None else [10]},
        {"role": "selling_point", "ids": body if body is not None else [620, 30]},
    ], "ordered_ids": ordered if ordered is not None else [10, 620, 30]}


def request(tmp_path, call, grouping=None):
    with patch.object(pipeline, "ai_call", side_effect=call) as ai:
        result = pipeline._request_order("auto", "prompt", candidates(), (5, 7), 1,
                                         6, grouping, str(tmp_path), None, 60)
    return result, ai.call_count


def test_invalid_ids_removed_from_both_lists_without_mutating_response(tmp_path):
    response = order(body=[620, True, "30", {}, 30], ordered=[10, 620, 30, None])
    original = copy.deepcopy(response)
    (normalized, validated, calls), count = request(tmp_path, [response])
    assert validated[1:3] == ([10, 30], 6)
    assert normalized["sections"][1]["ids"] == [30]
    assert normalized["ordered_ids"] == [10, 30]
    assert count == calls == 1
    assert response == original
    report = json.loads((tmp_path / "order.recovery.json").read_text("utf-8"))
    assert 620 in report["attempts"][0]["removed_ids"]


def test_empty_invalid_body_section_is_removed():
    result = pipeline._validate_order(order(body=[620], ordered=[10, 620]), candidates(), (5, 7), 1)
    assert result[1] == [10]
    assert result[3] == [{"role": "hook", "ids": [10]}]


def test_removed_hook_retries_ai_instead_of_relabelling_body(tmp_path):
    bad = order(hook=[620], body=[30], ordered=[620, 30])
    good = order(body=[30], ordered=[10, 30])
    (normalized, validated, calls), count = request(tmp_path, [bad, good])
    assert validated[1] == [10, 30]
    assert count == calls == 2


def test_all_invalid_has_bounded_retries_and_cannot_render_rejected_clips(tmp_path):
    bad = order(hook=[620], body=[621], ordered=[620, 621])
    with patch.object(pipeline, "ai_call", return_value=bad) as ai:
        with pytest.raises(AIReturnError):
            pipeline._request_order("auto", "prompt", candidates(), (5, 7), 1,
                                    6, None, str(tmp_path), None, 60)
    assert ai.call_count == 2


def test_semantic_constraint_checked_after_id_cleanup(tmp_path):
    report = {"groups": [{"name": "same", "ids": [10, 620]}]}
    (normalized, _, _), count = request(tmp_path, [order()], report)
    assert normalized["ordered_ids"] == [10, 30]
    assert count == 1
