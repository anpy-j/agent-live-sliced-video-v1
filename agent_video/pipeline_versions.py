"""Job-local routing. Never change the V1 stage sequence or its defaults."""
from __future__ import annotations

V2_STAGES = [
    ("asr", "语音转写与切分", 20),
    ("filter", "规则粗筛", 40),
    ("judge", "V2 完整表达整理", 60),
    ("order", "V2 成片编排", 75),
    ("review", "V2 成片验收", 90),
    ("render", "渲染成片", 100),
]


def normalize_version(value: object = None) -> str:
    version = "v1" if value is None else str(value).strip().lower()
    if version not in {"v1", "v2"}:
        raise ValueError("剪辑版本必须是 V1 或 V2")
    return version


def stages_for(version: object, legacy: list) -> list:
    return V2_STAGES if normalize_version(version) == "v2" else legacy
