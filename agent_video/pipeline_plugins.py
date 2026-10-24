"""Optional V1 extensions. The base stage definitions remain unchanged."""
from __future__ import annotations

PLUGIN_STAGES = [("claim_extract", "信息提取", 66), ("claim_group", "语义归组", 72)]
PLUGIN_IDS = {sid for sid, _, _ in PLUGIN_STAGES}


def extend_stages(stages, enabled=False, version="v1", job_type="direct"):
    if not enabled or version != "v1" or job_type == "remix":
        return stages
    return sorted([*stages, *PLUGIN_STAGES], key=lambda s: s[2])


def configuration(path=None):
    from .rule_pipeline import load_settings
    return dict(load_settings(path)["features"]["semantic_groups"])
