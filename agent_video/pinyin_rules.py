"""Shared, version-independent S2 contiguous pinyin rules."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from functools import lru_cache
from pathlib import Path

from pypinyin import Style, lazy_pinyin

_LOCK = threading.RLock()


def rules_path() -> Path:
    return Path(os.environ.get("LIVECUT_PINYIN_RULES", "data/pinyin_rules.json"))


@lru_cache(maxsize=1)
def _valid_syllables():
    from pypinyin.constants import PINYIN_DICT
    from pypinyin.contrib.tone_convert import to_normal
    return {to_normal(p).replace("ü", "v") for readings in PINYIN_DICT.values()
            for p in readings.split(",")}


def normalize(value):
    text = str(value or "").strip().lower().replace("ü", "v").replace("u:", "v")
    if not re.fullmatch(r"[a-z]+(?:\s+[a-z]+)*", text):
        raise ValueError("拼音请用空格分隔的无声调音节，不支持间隔、数字或通配符")
    syllables = text.split()
    # Validate against the library's Mandarin syllable inventory.
    valid = _valid_syllables()
    if any(s not in valid for s in syllables):
        raise ValueError("包含无效拼音音节，请检查拼写")
    if len(syllables) > 32:
        raise ValueError("单条规则最多32个音节")
    return " ".join(syllables)


def load_rules(path=None):
    file = Path(path) if path is not None else rules_path()
    if not file.exists():
        return {"revision": "empty", "rules": []}
    data = json.loads(file.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise ValueError("拼音规则文件损坏")
    return data


def save_rule(path, payload, rule_id=None):
    with _LOCK:
        data = load_rules(path)
        phrase = normalize(payload.get("pinyin"))
        if any(r["pinyin"] == phrase and r["id"] != rule_id for r in data["rules"]):
            raise ValueError("该拼音规则已存在")
        enabled = payload.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("启用状态必须是布尔值")
        rule = {"id": rule_id or uuid.uuid4().hex, "pinyin": phrase,
                "name": str(payload.get("name") or phrase).strip()[:120],
                "note": str(payload.get("note") or "").strip()[:1000], "enabled": enabled}
        if rule_id:
            index = next((i for i, r in enumerate(data["rules"]) if r["id"] == rule_id), None)
            if index is None:
                raise ValueError("规则不存在")
            data["rules"][index] = rule
        else:
            if len(data["rules"]) >= 1000:
                raise ValueError("最多保存1000条拼音规则")
            data["rules"].append(rule)
        return _write(path, data)


def delete_rule(path, rule_id):
    with _LOCK:
        data = load_rules(path)
        rows = [r for r in data["rules"] if r["id"] != rule_id]
        if len(rows) == len(data["rules"]):
            raise ValueError("规则不存在")
        data["rules"] = rows
        return _write(path, data)


def _write(path, data):
    data["revision"] = hashlib.sha256(json.dumps(data["rules"], ensure_ascii=False,
                                                sort_keys=True).encode()).hexdigest()[:16]
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    temp = file.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, file)
    return data


def match_text(text, snapshot):
    # Phrase-aware pronunciation, aligned one syllable per Han character.
    # Punctuation/whitespace can separate speech; Latin text/numbers break a match.
    tokens, positions = [], []
    for part in re.finditer(r"[\u3400-\u9fff]+|[^\u3400-\u9fff]+", text):
        value = part.group()
        if re.fullmatch(r"[\u3400-\u9fff]+", value):
            sounds = lazy_pinyin(value, style=Style.NORMAL, strict=False)
            for offset, sound in enumerate(sounds):
                tokens.append(sound.replace("ü", "v"))
                positions.append(part.start() + offset)
        elif re.search(r"[\w]", value):
            tokens.append("|")
            positions.append(part.start())
    hits = []
    for rule in snapshot["rules"]:
        if not rule["enabled"]:
            continue
        syllables = rule["pinyin"].split()
        for i in range(len(tokens) - len(syllables) + 1):
            if tokens[i:i + len(syllables)] == syllables:
                start, end = positions[i], positions[i + len(syllables) - 1] + 1
                hits.append({"rule_id": rule["id"], "name": rule["name"],
                             "pinyin": rule["pinyin"], "text": text[start:end],
                             "start": start, "end": end})
    return {"pinyin": " ".join(tokens), "hits": hits}


def apply_rules(rows, snapshot):
    for row in rows:
        row.pop("pinyin_hits", None)
    if not any(r["enabled"] for r in snapshot["rules"]):
        return rows
    for row in rows:
        result = match_text(str(row.get("text") or ""), snapshot)
        if result["hits"]:
            row["usable"] = False
            row["reason"] = "pinyin_rule"
            row["pinyin_hits"] = result["hits"]
    return rows
