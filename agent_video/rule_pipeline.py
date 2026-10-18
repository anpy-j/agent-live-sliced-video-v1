"""Independent, stage-fixed V1 rule switches. Trials deliberately ignore these switches."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
from pathlib import Path

from .repetition_rules import trial_options, match_rows, _characters, _canonical

_LOCK = threading.RLock()
DEFAULTS = {
    "breath": {"version": "v1", "stage": "S4", "enabled": False},
    "pinyin": {"version": "v1", "stage": "S2", "enabled": True},
    "repetition": {"version": "v1", "stage": "S2", "enabled": False,
                   "options": trial_options({})},
    "filler": {"version": "v1", "stage": "S4", "enabled": True},
}


def settings_path():
    return Path(os.environ.get("LIVECUT_RULE_PIPELINE", "data/rule_pipeline.json"))


def _snapshot(features):
    revision = hashlib.sha256(json.dumps(features, sort_keys=True).encode()).hexdigest()[:16]
    return {"revision": revision, "features": features}


def load_settings(path=None):
    file = Path(path) if path is not None else settings_path()
    features = copy.deepcopy(DEFAULTS)
    if file.is_file():
        data = json.loads(file.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("features"), dict):
            raise ValueError("规则流程开关文件损坏")
        for name, value in data["features"].items():
            if name not in features or not isinstance(value, dict) or not isinstance(value.get("enabled"), bool):
                raise ValueError("规则流程开关无效")
            features[name]["enabled"] = value["enabled"]
            if name == "repetition":
                options = value.get("options", {})
                if not isinstance(options, dict):
                    raise ValueError("重复检测条件必须是对象")
                features[name]["options"] = trial_options(options)
    return _snapshot(features)


def save_setting(path, name, payload):
    if name not in DEFAULTS:
        raise ValueError("未知规则类型")
    if not isinstance(payload.get("enabled"), bool):
        raise ValueError("流程开关必须是布尔值")
    if any(k in payload and payload[k] != DEFAULTS[name][k] for k in ("version", "stage")):
        raise ValueError("当前只支持 V1，规则阶段不可更改")
    with _LOCK:
        data = load_settings(path)
        feature = data["features"][name]
        feature["enabled"] = payload["enabled"]
        if name == "repetition" and "options" in payload:
            if not isinstance(payload["options"], dict):
                raise ValueError("重复检测条件必须是对象")
            feature["options"] = trial_options(payload["options"])
        data = _snapshot(data["features"])
        file = Path(path)
        file.parent.mkdir(parents=True, exist_ok=True)
        temporary = file.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, file)
        return data


def apply_repetition(clauses, options):
    """Only remove a whole duplicate clause; partial descriptions remain advisory."""
    report = match_rows(clauses, options)
    by_id = {r["id"]: r for r in clauses}
    removed = []
    for group in report["groups"]:
        if group["type"] != "sentence":
            continue
        whole = [m for m in group["occurrences"]
                 if _canonical(_characters(m["full_text"])[0]) == group["phrase"]]
        # A partial match in an earlier clause must never become the reference for deletion.
        if len({m["id"] for m in whole}) < options["min_count"]:
            continue
        reference = whole[0]["id"]
        for member in whole[1:]:
            row = by_id[member["id"]]
            if row.get("usable") and member["id"] != reference:
                row.update(usable=False, reason="repetition_rule",
                           repetition_reference=reference)
                removed.append(member["id"])
    report["removed_ids"] = removed
    return report
