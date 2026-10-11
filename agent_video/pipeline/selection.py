"""S4 selection contract: individual candidates and an explicit render snapshot."""
from __future__ import annotations

import math
from typing import Any

from agent_video.engine.scripts import badvocab
from .errors import AIReturnError

SELECTION_VERSION = 2


def selection_candidates(clauses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep original clause IDs; never absorb a rejected neighbour into a candidate.

    ``context`` identifies uninterrupted S3 sentence runs, without sending S3
    explanations or source metadata to the model. It is context, not a mandate
    to select the whole run.
    """
    result = []
    previous = None
    context = -1
    seen = set()
    for clause in clauses:
        if not clause.get("usable") or badvocab.BAD_RE.search(str(clause.get("text") or "")):
            previous = None
            continue
        cid = clause.get("id")
        if not isinstance(cid, int) or isinstance(cid, bool) or cid in seen:
            raise AIReturnError(f"S4 候选 id 非法或重复：{cid!r}")
        start, end = float(clause["start"]), float(clause["end"])
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise AIReturnError(f"S4 候选时间非法：{cid}")
        if (previous is None or cid != previous["id"] + 1
                or clause.get("unit", cid) != previous.get("unit", previous["id"])
                or start - float(previous["end"]) > 0.35
                or start < float(previous["end"]) - 1e-6):
            context += 1
        result.append({"id": cid, "start": start, "end": end,
                       "text": str(clause.get("text") or ""),
                       "usable": True, "context": context, "members": [cid]})
        seen.add(cid)
        previous = clause
    return result


def compile_selection(clauses: list[dict[str, Any]],
                      sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge only exact adjacent source clauses selected in the same sentence group.

    Non-contiguous selections stay separate, even when they share a group. An
    excluded clause, silence gap, different group or reverse order is a cut.
    """
    by_id = {c["id"]: c for c in selection_candidates(clauses)}
    positions = {c["id"]: i for i, c in enumerate(clauses)}
    clips: list[dict[str, Any]] = []
    seen = set()
    for group, section in enumerate(sections):
        for cid in section["ids"]:
            if cid not in by_id or cid in seen:
                raise AIReturnError(f"S4 选择了不可用或重复的子句：{cid!r}")
            seen.add(cid)
            clause = by_id[cid]
            previous = clips[-1] if clips else None
            if (previous is not None and previous["group"] == group
                    and positions[cid] == positions[previous["members"][-1]] + 1
                    and cid == previous["members"][-1] + 1
                    and abs(clause["start"] - previous["end"]) <= 1e-6):
                previous["members"].append(cid)
                previous["end"] = clause["end"]
                previous["text"] += clause["text"]
            else:
                clips.append({"id": cid, "start": clause["start"], "end": clause["end"],
                              "text": clause["text"], "members": [cid],
                              "group": group, "role": section["role"], "usable": True})
    if not clips:
        raise AIReturnError("S4 没有选出可渲染的内容")
    for clip in clips:
        if badvocab.BAD_RE.search(clip["text"]):
            raise AIReturnError(f"S4 所选句群命中硬禁词：{clip['members']}")
    return clips
