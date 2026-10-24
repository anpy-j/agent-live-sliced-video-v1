"""Independent, stage-fixed V1 rule switches. Trials deliberately ignore these switches."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
from pathlib import Path


_LOCK = threading.RLock()
DEFAULTS = {
    "semantic_groups": {"version": "v1", "stage": "S3–S4", "enabled": False},
    "breath": {"version": "v1", "stage": "S4", "enabled": False},
    "pinyin": {"version": "v1", "stage": "S2", "enabled": True},
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
            # Retired S2 extension: tolerate historical settings, never execute them.
            if name == "repetition":
                continue
            if name not in features or not isinstance(value, dict) or not isinstance(value.get("enabled"), bool):
                raise ValueError("规则流程开关无效")
            features[name]["enabled"] = value["enabled"]
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
        data = _snapshot(data["features"])
        file = Path(path)
        file.parent.mkdir(parents=True, exist_ok=True)
        temporary = file.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, file)
        return data
