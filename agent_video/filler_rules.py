"""Manually configured V1 S4 pinyin filler removal at clip start or any position."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import threading
import uuid
from pathlib import Path

from agent_video.engine.scripts.prep import simp
from agent_video import pinyin_rules
from pypinyin import lazy_pinyin

_LOCK = threading.RLock()
_PUNCT = re.compile(r"[\s，,。.!！?？；;：:、…\"'“”‘’]+")
# "all" removes every hit; "leading" only removes hits at the start of the clip text.
SCOPES = {"all": "全部", "leading": "句首"}


def _normalize_scope(value):
    scope = str(value or "all").strip().lower()
    if scope not in SCOPES:
        raise ValueError("清理范围只能是句首或全部")
    return scope


def rules_path():
    return Path(os.environ.get("LIVECUT_FILLER_RULES", "data/filler_rules.json"))


def load_rules(path=None):
    file = Path(path) if path is not None else rules_path()
    if not file.exists():
        return {"revision": "empty", "rules": []}
    data = json.loads(file.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise ValueError("口头词规则文件损坏")
    # Read legacy Chinese rules without mutating them; the next save writes pinyin format.
    for rule in data["rules"]:
        if "pinyin" not in rule:
            rule["pinyin"] = pinyin_rules.normalize(" ".join(lazy_pinyin(rule.get("phrase", ""))))
        rule.setdefault("name", rule.get("phrase") or rule["pinyin"])
        rule.setdefault("scope", "all")
    return data


def _write(path, data):
    data["revision"] = hashlib.sha256(json.dumps(data["rules"], ensure_ascii=False,
                                                sort_keys=True).encode()).hexdigest()[:16]
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, file)
    return data


def save_rule(path, payload, rule_id=None):
    value = payload.get("pinyin")
    # Compatibility with previously saved Chinese rules and legacy API callers.
    if value is None and payload.get("phrase"):
        if not re.fullmatch(r"[\u4e00-\u9fff]{1,32}", str(payload["phrase"])):
            raise ValueError("请输入无声调拼音，音节用空格分隔")
        value = " ".join(lazy_pinyin(str(payload["phrase"])))
    phrase = pinyin_rules.normalize(value)
    enabled = payload.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("启用状态必须是布尔值")
    scope = _normalize_scope(payload.get("scope"))
    with _LOCK:
        data = load_rules(path)
        if any(r["pinyin"] == phrase and r["id"] != rule_id for r in data["rules"]):
            raise ValueError("该口头词规则已存在")
        rule = {"id": rule_id or uuid.uuid4().hex, "pinyin": phrase, "name": str(payload.get("name") or phrase).strip()[:120],
                "note": str(payload.get("note") or "").strip()[:1000], "enabled": enabled, "scope": scope}
        if rule_id:
            index = next((i for i, r in enumerate(data["rules"]) if r["id"] == rule_id), None)
            if index is None:
                raise ValueError("规则不存在")
            data["rules"][index] = rule
        else:
            if len(data["rules"]) >= 1000:
                raise ValueError("最多保存1000条口头词规则")
            data["rules"].append(rule)
        return _write(path, data)


def delete_rule(path, rule_id):
    with _LOCK:
        data = load_rules(path)
        remaining = [r for r in data["rules"] if r["id"] != rule_id]
        if len(remaining) == len(data["rules"]):
            raise ValueError("规则不存在")
        data["rules"] = remaining
        return _write(path, data)


def _merge_spans(spans):
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _text_parts(text, spans):
    parts, cursor = [], 0
    for start, end in spans:
        if start > cursor:
            parts.append({"char_start": cursor, "char_end": start, "text": text[cursor:start]})
        cursor = end
    if cursor < len(text):
        parts.append({"char_start": cursor, "char_end": len(text), "text": text[cursor:]})
    return [p for p in parts if _PUNCT.sub("", p["text"])]


def match_text(text, snapshot):
    """Same continuous pinyin matcher as S2, retaining original character positions.

    "leading" rules keep only hits at the start of the clip text; every other
    position stays in the text. "all" rules keep every hit.
    """
    text = str(text or "")
    result = pinyin_rules.match_text(text, snapshot)
    scopes = {rule["id"]: _normalize_scope(rule.get("scope")) for rule in snapshot["rules"]}
    hits, leading_skipped = [], []
    for hit in result["hits"]:
        if (scopes.get(hit["rule_id"], "all") == "leading"
                and _PUNCT.sub("", text[:hit["start"]])):
            leading_skipped.append(hit)
        else:
            hits.append(hit)
    hits.sort(key=lambda h: (h["start"], -h["end"]))
    spans = []
    for hit in hits:
        start, end = hit["start"], hit["end"]
        following = _PUNCT.match(text, end)
        if following:
            end = following.end()
        if not _PUNCT.sub("", text[:start]):
            start = 0
        # A trailing filler includes its preceding dangling separator.
        if not _PUNCT.sub("", text[end:]):
            while start > 0 and _PUNCT.fullmatch(text[start - 1]):
                start -= 1
        spans.append((start, end))
    spans = _merge_spans(spans)
    return {"text": text, "pinyin": result["pinyin"], "hits": hits, "leading_skipped": leading_skipped,
            "spans": spans, "preview_text": "".join(p["text"] for p in _text_parts(text, spans))}


def load_words(workdir, clauses):
    # S1 snapshots carry words across workspace cache copies. Older jobs may have words.json.
    file = Path(workdir) / "clauses.json"
    if file.is_file():
        data = json.loads(file.read_text(encoding="utf-8"))
        if isinstance(data.get("words"), list):
            return data["words"]
    file = Path(workdir) / "words.json"
    return json.loads(file.read_text(encoding="utf-8")) if file.is_file() else []


def clean_candidate(candidate, words, snapshot):
    """Detect in-scope positions; remove each alignable hit, preserving unaligned and out-of-scope hits."""
    clip = copy.deepcopy(candidate)
    text = str(clip.get("text", ""))
    trial = {"id": clip["id"], "start": clip["start"], "end": clip["end"],
             **match_text(text, snapshot), "status": "unchanged",
             "reason": "未命中口头词拼音", "cleaned_text": text}
    if not trial["hits"]:
        # A "leading" rule leaves in-sentence hits untouched; explain the keep.
        if trial["leading_skipped"]:
            trial["reason"] = "命中不在句首，按要求仅清理句首"
        return clip, trial
    trial.update(status="skipped", reason="缺少可对齐的词级时间戳")
    if not trial["preview_text"]:
        trial["reason"] = "清理后没有正文，保留原片段"
        return clip, trial
    tokens = []
    for word in words:
        try:
            start, end = float(word["s"]), float(word["e"])
            if not (math.isfinite(start) and math.isfinite(end) and end > start):
                continue
            if start >= float(clip["start"]) - .001 and end <= float(clip["end"]) + .001:
                tokens.append({"s": start, "e": end, "w": simp(str(word["w"]))})
        except (KeyError, ValueError, TypeError):
            continue
    tokens.sort(key=lambda t: (t["s"], t["e"]))
    normalize = lambda value: _PUNCT.sub("", value)
    if not tokens or normalize("".join(t["w"] for t in tokens)) != normalize(text):
        return clip, trial
    # Character offsets in normalized text map to exact ASR token boundaries.
    cursor = 0
    substantive = []
    for token in tokens:
        length = len(normalize(token["w"]))
        if length:
            substantive.append({**token, "a": cursor, "b": cursor + length})
            cursor += length
    deletions = []
    for start, end in trial["spans"]:
        a, b = len(normalize(text[:start])), len(normalize(text[:end]))
        first = next((i for i, t in enumerate(substantive) if t["a"] == a), None)
        last = next((i for i, t in enumerate(substantive) if t["b"] == b), None)
        if first is None or last is None or last < first:
            trial["reason"] = "口头词与正文处于同一词块，无法精确剪切"
            continue
        previous = substantive[first - 1] if first else None
        following = substantive[last + 1] if last + 1 < len(substantive) else None
        chosen = substantive[first:last + 1]
        if (any(right["s"] < left["e"] - .001 for left, right in zip(chosen, chosen[1:]))
                or previous and chosen[0]["s"] < previous["e"] - .001
                or following and following["s"] < chosen[-1]["e"] - .001):
            trial["reason"] = "词级边界重叠，保留该命中"
            continue
        cut_start = chosen[0]["s"] if previous else float(clip["start"])
        cut_end = following["s"] if following else float(clip["end"])
        deletions.append({"char_start": start, "char_end": end,
                          "start": round(cut_start, 3), "end": round(cut_end, 3)})
    if not deletions:
        return clip, trial
    parts = []
    char_cursor, time_cursor = 0, float(clip["start"])
    for deletion in deletions:
        if deletion["start"] > time_cursor and normalize(text[char_cursor:deletion["char_start"]]):
            parts.append({"start": time_cursor, "end": deletion["start"],
                          "text": text[char_cursor:deletion["char_start"]]})
        char_cursor, time_cursor = deletion["char_end"], deletion["end"]
    if time_cursor < float(clip["end"]) and normalize(text[char_cursor:]):
        parts.append({"start": time_cursor, "end": float(clip["end"]), "text": text[char_cursor:]})
    if not parts:
        trial["reason"] = "清理后没有正文，保留原片段"
        return clip, trial
    clip.update(start=parts[0]["start"], end=parts[-1]["end"], text="".join(p["text"] for p in parts))
    if len(parts) > 1:
        clip["parts"] = parts
    trial.update(status="cleaned", reason=("已按词级时间戳清理" if len(deletions) == len(trial["spans"])
                                          else "已清理可对齐命中；其余命中无法精确剪切，保留原文"),
                 cleaned_text=clip["text"], cleaned_start=clip["start"], retained_parts=parts,
                 removed_spans=deletions,
                 removed_seconds=round(float(candidate["end"]) - float(candidate["start"])
                                       - sum(p["end"] - p["start"] for p in parts), 3))
    return clip, trial


def clips_digest(clips):
    return hashlib.sha256(json.dumps(clips, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def clean_selection(candidates, words, snapshot):
    pairs = [clean_candidate(c, words, snapshot) for c in candidates]
    clips = []
    for clip, trial in pairs:
        parts = clip.pop("parts", None)
        if parts:
            clips.extend({**clip, **part} for part in parts)
        else:
            clips.append(clip)
    return {"version": 2, "rules": snapshot, "source_candidates": copy.deepcopy(candidates),
            "cleaned_candidates": clips, "cleaned_digest": clips_digest(clips),
            "rows": [pair[1] for pair in pairs],
            "total_seconds": round(sum(float(c["end"]) - float(c["start"]) for c in clips), 3)}
