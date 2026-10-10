from __future__ import annotations

import copy
import math
import uuid
from typing import Any


KINDS = {"video", "audio", "image", "subtitle"}
EFFECTS = {"none", "warm", "cool", "mono", "blur"}
TRANSITIONS = {"none", "fade", "slide", "wipe"}
SUBTITLE_STYLES = {
    "classic": {"name": "白字黑边", "color": "FFFFFF", "outline": 3},
    "yellow": {"name": "黄色强调", "color": "00FFFF", "outline": 3},
    "bar": {"name": "半透明底条", "color": "FFFFFF", "outline": 2, "box": True},
    "clean": {"name": "简洁细字", "color": "FFFFFF", "outline": 1},
    "title": {"name": "粗体标题", "color": "FFFFFF", "outline": 4, "bold": True},
    "highlight": {"name": "逐字高亮", "color": "00FFFF", "outline": 3},
}


def ident() -> str:
    return uuid.uuid4().hex


def number(value: Any, low: float, high: float, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} 必须为数字")
    try:
        n = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name} 必须为数字") from exc
    if not math.isfinite(n) or not low <= n <= high:
        raise ValueError(f"{name} 必须在 {low}–{high} 之间")
    return n


def new_project(title: str = "未命名剪辑") -> dict[str, Any]:
    return {
        "id": ident(), "title": title[:120], "revision": 0, "assets": [],
        "width": 1080, "height": 1920, "fps": 30,
        "timelines": [{"id": ident(), "name": "时间线01", "tracks": [
            {"id": ident(), "name": "主视频", "kind": "video", "hidden": False,
             "muted": False, "locked": False, "clips": []},
            {"id": ident(), "name": "字幕", "kind": "subtitle", "clips": []},
            {"id": ident(), "name": "音乐", "kind": "audio", "clips": []},
        ]}],
        "postprocess": {"progress": False, "pip_asset": "", "pip_opacity": 0.01,
                        "sticker_asset": ""},
    }


def validate(project: dict[str, Any], assets: dict[str, dict]) -> dict[str, Any]:
    """Normalize editing data; paths and media metadata always come from the registry."""
    p = copy.deepcopy(project)
    p["title"] = str(p.get("title") or "未命名剪辑")[:120]
    p["width"] = int(number(p.get("width", 1080), 64, 3840, "宽度")) // 2 * 2
    p["height"] = int(number(p.get("height", 1920), 64, 3840, "高度")) // 2 * 2
    p["fps"] = number(p.get("fps", 30), 12, 60, "帧率")
    timelines = p.get("timelines")
    if not isinstance(timelines, list) or not 1 <= len(timelines) <= 100:
        raise ValueError("项目需要 1–100 条时间线")
    seen: set[str] = set()

    def unique(item: dict) -> None:
        key = str(item.get("id", ""))
        if not key or key in seen or len(key) > 80:
            raise ValueError("时间线、轨道和片段需要唯一 ID")
        seen.add(key)

    for timeline in timelines:
        unique(timeline)
        timeline["name"] = str(timeline.get("name") or "时间线")[:120]
        tracks = timeline.get("tracks")
        if not isinstance(tracks, list) or not 1 <= len(tracks) <= 32:
            raise ValueError("每条时间线需要 1–32 个轨道")
        for track in tracks:
            unique(track)
            if track.get("kind") not in KINDS:
                raise ValueError("不支持的轨道类型")
            if not isinstance(track.get("clips"), list) or len(track["clips"]) > 2000:
                raise ValueError("轨道片段格式错误或数量过多")
            for clip in track["clips"]:
                unique(clip)
                kind = track["kind"]
                clip["start"] = number(clip.get("start", 0), 0, 86400, "片段开始")
                clip["duration"] = number(clip.get("duration"), 1 / p["fps"], 86400, "片段时长")
                clip["in"] = number(clip.get("in", 0), 0, 86400, "素材入点")
                clip["speed"] = number(clip.get("speed", 1), 0.25, 4, "倍速")
                for key, default, low, high in [
                    ("volume", 1, 0, 4), ("opacity", 1, 0, 1),
                    ("x", 0, -2, 2), ("y", 0, -2, 2), ("scale", 1, 0.01, 3),
                    ("rotation", 0, -360, 360), ("fade_in", 0, 0, 10),
                    ("fade_out", 0, 0, 10), ("transition_duration", 0.3, 0, 5),
                    ("font_size", 56, 12, 240),
                ]:
                    clip[key] = number(clip.get(key, default), low, high, key)
                if clip.get("effect", "none") not in EFFECTS:
                    raise ValueError("不支持的特效")
                if clip.get("transition", "none") not in TRANSITIONS:
                    raise ValueError("不支持的转场")
                if kind == "subtitle":
                    clip["text"] = str(clip.get("text") or "")[:2000]
                    if clip.get("style", "classic") not in SUBTITLE_STYLES:
                        raise ValueError("不支持的字幕模板")
                else:
                    asset = assets.get(str(clip.get("asset_id")))
                    if not asset:
                        raise ValueError("片段引用了未导入的素材")
                    compatible = {"video": {"video", "image"}, "image": {"image"},
                                  "audio": {"audio", "video"}}[kind]
                    if asset["kind"] not in compatible:
                        raise ValueError("素材与轨道类型不匹配")
                    if asset["kind"] != "image" and clip["in"] + clip["duration"] * clip["speed"] > asset["duration"] + 0.05:
                        raise ValueError("片段超出素材时长")
    p["assets"] = list(assets.values())
    post = p.setdefault("postprocess", {})
    post["pip_opacity"] = number(post.get("pip_opacity", .01), 0, 1, "画中画不透明度")
    for key in ("pip_asset", "sticker_asset"):
        if post.get(key) and post[key] not in assets:
            raise ValueError("后处理素材未导入")
    if post.get("pip_asset") and assets[post["pip_asset"]]["kind"] == "audio":
        raise ValueError("画中画需要视频或图片")
    if post.get("sticker_asset") and assets[post["sticker_asset"]]["kind"] != "image":
        raise ValueError("角落贴纸需要图片")
    return p


def timeline_duration(timeline: dict[str, Any]) -> float:
    return max((float(c["start"]) + float(c["duration"])
                for t in timeline["tracks"] if not t.get("hidden")
                for c in t["clips"]), default=0)
