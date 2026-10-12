from __future__ import annotations

import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Any

from agent_video.pipeline_v3 import badvocab
from agent_video.pipeline_v3.errors import PipelineError

REVISION = "v2-content-1"


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("不是对象")
        return value
    except (OSError, ValueError) as exc:
        raise PipelineError(f"V2 数据读取失败：{path.name}：{exc}") from exc


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def emit(callback, stage, status, message):
    if callback:
        callback(stage, status, message)


class _Prepared(Exception):
    pass


def prepare(media, workspace, *, on_stage=None, **kwargs):
    """Run V1's exact S1/S2, stop at its public stage callback before any V1 AI.

    No edits/monkey patches to V1. Artifacts are local to this V2 job. A test
    verifies the filter completion boundary remains before V1 S3 or rendering.
    """
    from agent_video.pipeline_v3.run import run_pipeline

    def boundary(stage, status, message):
        emit(on_stage, stage, status, message)
        if stage == "filter" and status == "done":
            raise _Prepared()

    try:
        run_pipeline(media, str(workspace), on_stage=boundary, **kwargs)
    except _Prepared:
        return read(workspace / "clauses.filtered.json")
    raise PipelineError("V1 S1/S2 复用边界未触发，停止 V2")


# Structural noise checks, separate from the sole compliance vocabulary.
NOISE = re.compile(
    r"原版|再给大家穿.{0,12}搭配|你们就去穿.{0,10}吧|我给你们穿.{0,12}吧|"
    r"(先把|给你们上).{0,10}(上了|链接)|(给我|帮我).{0,8}(拿一下|踢单|踢掉)|"
    r"(卡码|卡马|卡嗎).{0,14}(拍大|拍小|玩小牌|牌小)|"
    r"(全部|已经).{0,8}卖完了|吓我一跳|没有.{0,3}[八8]分")
UNFINISHED = re.compile(r"(的话|的时候|就正常来说|然后就是|还有一个|这件衣服的)[，。！？、…\s]*$")
CONDITION = re.compile(r"(?:如果|要是|你).{0,22}(?:搭|穿|配).{0,16}(?:的话|的时候)")
GARMENTS = ("丝绒", "牛仔", "西装裤", "半身裙", "阔腿裤", "白裤", "白牛")
EFFECT = re.compile(r"显高|显瘦|显腿|修饰|更正式|更休闲|气质")


def normalize_text(text):
    return re.sub(r"[\W_]+", "", text)


def source_rows(data):
    rows = data.get("clauses")
    if not isinstance(rows, list) or not rows:
        raise PipelineError("V2 缺少 S2 子句")
    seen = set()
    for row in rows:
        cid = row.get("id")
        if isinstance(cid, bool) or not isinstance(cid, int) or cid < 0 or cid in seen:
            raise PipelineError("V2 原始子句 ID 无效或重复")
        seen.add(cid)
        start, end = float(row["start"]), float(row["end"])
        if not math.isfinite(start + end) or start < 0 or end <= start:
            raise PipelineError(f"V2 原始时间区间无效：{cid}")
    return rows


def selectable(row):
    text = str(row.get("text") or "")
    return bool(row.get("usable")) and not badvocab.BAD_RE.search(text) and not NOISE.search(text)


def source_fingerprint(data, product):
    return digest([REVISION, data.get("source"), data.get("virtual_timeline"), product,
                   [{k: r.get(k) for k in ("id", "start", "end", "text", "usable")}
                    for r in source_rows(data)], badvocab.BAD_RE.pattern, NOISE.pattern])


def origins(rows):
    result = {}
    for n, row in enumerate(rows):
        text = str(row.get("text") or "")
        if not EFFECT.search(text):
            continue
        for before in reversed(rows[max(0, n - 3):n]):
            context = str(before.get("text") or "")
            terms = [term for term in GARMENTS if term in context]
            if CONDITION.search(context) and terms and float(row["start"]) - float(before["end"]) <= 8:
                result[row["id"]] = {"id": before["id"], "text": context, "terms": terms}
                break
    return result


def make_block(spec, rows, *, allowed=None):
    by_id = {r["id"]: r for r in rows}
    ids = spec["ids"]
    if len(set(ids)) != len(ids) or any(cid not in by_id or
            (allowed is not None and cid not in allowed) or not selectable(by_id[cid]) for cid in ids):
        raise PipelineError("V2 完整表达引用了重复、越界或不可选子句")
    text = "".join(str(by_id[cid]["text"]) for cid in ids)
    if badvocab.BAD_RE.search(text):
        raise PipelineError("V2 完整表达拼接后命中禁词")
    positions = {r["id"]: n for n, r in enumerate(rows)}
    neighbors = set()
    for cid in ids:
        n = positions[cid]
        neighbors.update(r["id"] for r in rows[max(0, n - 2):n + 3] if r["id"] not in ids)
    context = [{"id": r["id"], "text": r["text"], "selectable": selectable(r)}
               for r in rows if r["id"] in neighbors]
    source_conditions = origins(rows)
    constraints = [dict(source_conditions[cid], effect_id=cid) for cid in ids if cid in source_conditions]
    return {"id": "b_" + digest(ids)[:16], "ids": ids, "text": text,
            "seconds": round(sum(float(by_id[cid]["end"]) - float(by_id[cid]["start"]) for cid in ids), 3),
            "topic": spec["topic"], "fact": spec["fact"], "context": context,
            "constraints": constraints}


def block_issues(block):
    issues = []
    text = block["text"]
    if NOISE.search(text):
        issues.append("chatter")
    if UNFINISHED.search(text):
        issues.append("incomplete")
    for constraint in block.get("constraints", []):
        if not all(term in text for term in constraint["terms"]):
            issues.append("changed_meaning")
    return list(dict.fromkeys(issues))


def plan_blocks(plan, blocks):
    by_id = {b["id"]: b for b in blocks}
    selected, reserve = plan["selected"], plan["reserve"]
    if len(set(selected + reserve)) != len(selected + reserve) or any(cid not in by_id for cid in selected + reserve):
        raise PipelineError("V2 编排引用了重复或不存在的表达")
    if not selected:
        raise PipelineError("V2 未选出成片内容")
    used = set()
    texts = set()
    for bid in selected:
        block = by_id[bid]
        if used.intersection(block["ids"]):
            raise PipelineError("V2 编排重复使用同一原始口播")
        normalized = normalize_text(block["text"])
        if normalized in texts:
            raise PipelineError("V2 编排含重复原文")
        if block_issues(block):
            raise PipelineError(f"V2 编排选择了结构不合格的表达：{bid}")
        used.update(block["ids"])
        texts.add(normalized)
    return [by_id[bid] for bid in selected]


def compile_clips(blocks, rows):
    """Keep a complete block together in segment export, including cross-source cuts."""
    by_id = {r["id"]: r for r in rows}
    clips = []
    for block in blocks:
        previous = None
        for cid in block["ids"]:
            row = by_id[cid]
            if previous is not None and abs(float(previous["end"]) - float(row["start"])) <= 1e-6:
                previous["end"] = row["end"]
                previous["text"] += row["text"]
                previous["members"].append(cid)
            else:
                previous = {"id": block["id"], "start": row["start"], "end": row["end"],
                            "text": row["text"], "members": [cid]}
                clips.append(previous)
    return clips
