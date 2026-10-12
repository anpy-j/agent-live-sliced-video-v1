import json

import pytest

from agent_video.ai import WorkBuddyCli


def dsml(params, tool="StructuredOutput"):
    fields = "\n".join(
        f'<｜｜DSML｜｜ parameter name="{name}" string="{kind}">{value}</｜｜DSML｜｜ parameter>'
        for name, kind, value in params)
    return f'<｜｜DSML｜｜ calls>\n<｜｜DSML｜｜ invoke name="{tool}">\n{fields}\n</｜｜DSML｜｜ invoke>\n</｜｜DSML｜｜ calls>'


def test_decodes_actual_workbuddy_deepseek_terminal_format():
    decisions = [{"id": 4, "usable": True, "reason": "面料卖点"}]
    text = dsml([("decisions", "false", json.dumps(decisions))])
    history = [{"type": "message", "role": "user", "content": '{"decisions":[]}'},
               {"type": "reasoning", "text": '{"decisions":[]}'},
               {"type": "result", "result": text}]
    assert WorkBuddyCli._find_object(history, ("decisions",)) == {"decisions": decisions}


def test_decodes_order_string_and_array_parameters():
    text = dsml([("main_product", "true", "羊毛衫"),
                 ("sections", "false", '[{"role":"hook","ids":[4]}]'),
                 ("ordered_ids", "false", "[4]")])
    assert WorkBuddyCli._find_object(text, ("main_product", "sections", "ordered_ids")) == {
        "main_product": "羊毛衫", "sections": [{"role": "hook", "ids": [4]}], "ordered_ids": [4]}


@pytest.mark.parametrize("text", [
    dsml([("decisions", "false", "[broken]")]),
    dsml([("decisions", "false", "[]"), ("decisions", "false", "[]")]),
    dsml([("decisions", "false", "[]")], tool="Shell"),
    dsml([("decisions", "false", "[]")]).replace('</｜｜DSML｜｜ invoke>', ''),
    '```json\n[{"id":4,"usable":true,"reason":"test"}]\n```',
])
def test_rejects_invalid_calls_and_unwrapped_arrays(text):
    assert WorkBuddyCli._find_object(text, ("decisions",)) is None


def test_does_not_accept_request_or_reasoning_as_answer():
    text = dsml([("decisions", "false", "[]")])
    for item in [{"role": "user", "content": text}, {"type": "reasoning", "text": text}]:
        assert WorkBuddyCli._find_object(item, ("decisions",)) is None
