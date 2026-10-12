# -*- coding: utf-8 -*-
"""S2.5 句单元聚合（确定性）。

S1 按停顿/句末标点切出的子句，很多只是「一句话的一半」：单独看是残句，和相邻
子句连起来才是完整卖点。S3 若逐子句独立判定，就会把这些碎片判死。这里在 S2 与
S3 之间把时间相邻、语义承接的子句聚成一个「句单元」，作为 S3 理解的上下文单位。

- 单元**不改变逐子句结论**：S3 仍然对每个 id 给出 usable，坏句不会被连坐放行。
- S4 接收逐子句候选及上下文标记，自行精选并绑定完整句群；不预先强制合并。
  ``order_candidates`` 仅用于读取旧版 S4 计划，读取时必须保留淘汰句作为断点。
- 聚合上限是一个区间 ``[merge_min, merge_max]``：单元先长到 ``merge_min``；
  此后只有下一条与当前明显承接（结尾是连接词，或以承接词开头）才继续并入，
  直到 ``merge_max``。这些范围用于 S3 上下文聚合，不限制 S4 句群的段数。
"""
from __future__ import annotations

from typing import Any

from agent_video.engine.scripts.textnorm import (context_dependent_start,
                                                 incomplete_ending)

FINAL_PUNCT = "。！？!?…"
DEFAULT_MERGE_MIN = 4.0
DEFAULT_MERGE_MAX = 8.0
DEFAULT_SILENCE_GAP = 0.30


def _duration(unit: dict[str, Any]) -> float:
    return float(unit["end"]) - float(unit["start"])


def _ends_sentence(clause: dict[str, Any]) -> bool:
    text = str(clause.get("text") or "")
    return bool(text) and text[-1] in FINAL_PUNCT


def _continues(previous_text: str, next_text: str) -> bool:
    """下一条是否明显承接上一条（确定性、保守）。"""
    return (incomplete_ending(previous_text) is not None
            or context_dependent_start(next_text) is not None)


def _new_unit(clause: dict[str, Any]) -> dict[str, Any]:
    return {"id": clause["id"], "start": float(clause["start"]),
            "end": float(clause["end"]), "text": str(clause.get("text") or ""),
            "members": [clause]}


def build_units(clauses: list[dict[str, Any]], *,
                merge_min: float = DEFAULT_MERGE_MIN,
                merge_max: float = DEFAULT_MERGE_MAX,
                silence_gap: float = DEFAULT_SILENCE_GAP) -> list[dict[str, Any]]:
    """把时间序子句聚成句单元，就地写 ``clause['unit']`` 并返回单元列表。"""
    units: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for clause in clauses:
        if current is None:
            current = _new_unit(clause)
            continue
        gap = float(clause["start"]) - current["end"]
        contiguous = gap < silence_gap - 1e-9
        fits = float(clause["end"]) - current["start"] <= merge_max + 1e-9
        grow = (_duration(current) < merge_min - 1e-9
                or _continues(current["text"], str(clause.get("text") or "")))
        if contiguous and not _ends_sentence(current["members"][-1]) and fits and grow:
            current["members"].append(clause)
            current["end"] = float(clause["end"])
            current["text"] += str(clause.get("text") or "")
        else:
            units.append(current)
            current = _new_unit(clause)
    if current is not None:
        units.append(current)
    for index, unit in enumerate(units):
        for member in unit["members"]:
            member["unit"] = index
    return units


def _merge_run(run: list[dict[str, Any]]) -> dict[str, Any]:
    first, last = run[0], run[-1]
    return {"id": first["id"],
            "text": "".join(str(clause.get("text") or "") for clause in run),
            "start": float(first["start"]), "end": float(last["end"]),
            "usable": True, "members": [clause["id"] for clause in run]}


def order_candidates(clauses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按句单元把已判定子句收敛成 S4 候选：同一单元内连续的可用子句并为一段。"""
    candidates: list[dict[str, Any]] = []
    run: list[dict[str, Any]] = []
    previous_unit: Any = None
    for clause in clauses:
        unit = clause.get("unit")
        if run and unit != previous_unit:
            candidates.append(_merge_run(run))
            run = []
        previous_unit = unit
        if clause.get("usable"):
            run.append(clause)
        elif run:
            candidates.append(_merge_run(run))
            run = []
    if run:
        candidates.append(_merge_run(run))
    return candidates
