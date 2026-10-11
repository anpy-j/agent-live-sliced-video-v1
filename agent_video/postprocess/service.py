from __future__ import annotations

import json
import random
from pathlib import Path

from ..editor.model import ident, number
from ..editor.service import EditorService
from .resources import animated_sticker, animated_progress, texture


class PostprocessService(EditorService):
    """Independent resource library and serial processing queue, using the shared renderer."""
    def __init__(self, root):
        super().__init__(root, namespace="postprocess", speech=False)
        with self.connect() as con:
            con.execute("CREATE TABLE IF NOT EXISTS post_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
            row = con.execute("SELECT value FROM post_meta WHERE key='library'").fetchone()
        self.library_id = row[0] if row else self.create({"title": "后处理素材库"})["id"]
        with self.connect() as con:
            con.execute("INSERT OR REPLACE INTO post_meta VALUES ('library',?)", (self.library_id,))
        folder = self.root / "resources"
        folder.mkdir(exist_ok=True)
        if not self.get(self.library_id)["assets"]:
            for style in ("stars", "rings"):
                self.register(self.library_id, str(animated_sticker(folder, style)))
            self.register(self.library_id, str(texture(folder, 20261011)))
        # Migrate existing libraries without replacing user resources.
        self.progress_ids = []
        self.progress_modes = {}
        known = {a["path"]: a for a in self.get(self.library_id)["assets"]}
        names = {"hearts": "跳动爱心", "floating": "漂浮爱心", "sparkles": "闪耀星光", "travelling_star": "游走小星星"}
        for style in names:
            path = folder / f"进度条-{names[style]}.gif"
            if not path.exists():
                path = animated_progress(folder, style)
            asset = known.get(str(path.resolve())) or self.register(self.library_id, str(path))["asset"]
            self.progress_ids.append(asset["id"])
            self.progress_modes[asset["id"]] = "moving_star" if style == "travelling_star" else "animated"
        from .music import MusicQueue
        self.music = MusicQueue(self)
        from .runtime import CpuMusicRuntime
        self.runtime = CpuMusicRuntime(self)

    def close(self):
        self.music.close()
        self.runtime.close()
        super().close()

    def settings(self, payload=None):
        data = super().settings(payload)
        data.setdefault("acestep_url", "http://127.0.0.1:8001")
        if payload is not None:
            from .music import validate_url
            data["acestep_url"] = validate_url(payload.get("acestep_url", data["acestep_url"]))
            if "postprocess_recipe" in payload:
                if not isinstance(payload["postprocess_recipe"], dict):
                    raise ValueError("后处理方案格式无效")
                data["postprocess_recipe"] = payload["postprocess_recipe"]
            # API keys are read from the environment, never returned to the browser.
            with self.connect() as con:
                con.execute("INSERT OR REPLACE INTO settings VALUES (1,?)", (json.dumps(data),))
        return data

    def library(self):
        return {"id": self.library_id, "assets": self.get(self.library_id)["assets"], "progress_ids": self.progress_ids,
                "progress_modes": self.progress_modes}

    def scan_folder(self, payload):
        folder = Path(str(payload.get("path") or "")).expanduser()
        kind = payload.get("kind")
        extensions = {"video": {".mp4", ".mov", ".mkv", ".webm", ".avi"},
                      "image": {".png", ".jpg", ".jpeg", ".webp", ".gif"}}
        if kind not in extensions or not str(payload.get("path") or "").strip() or not folder.is_dir():
            raise ValueError("请选择有效的视频或图片文件夹")
        files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in extensions[kind])
        if len(files)>500:
            raise ValueError("单个素材文件夹最多支持 500 个文件")
        assets, errors = [], []
        for path in files:
            try:
                asset = self.import_asset({"path": str(path.resolve())})
                if asset["kind"] == kind:
                    assets.append(asset)
            except (ValueError, OSError) as exc:
                errors.append({"path": str(path), "error": str(exc)})
        return {"assets": assets, "errors": errors, "path": str(folder.resolve())}

    def import_asset(self, payload):
        return self.register(self.library_id, str(payload.get("path") or ""))["asset"]

    def generate_texture(self):
        path = texture(self.root / "resources", random.SystemRandom().randrange(1, 2**31))
        return self.import_asset({"path": str(path)})

    def prepare(self, source, options):
        library = {a["id"]: a for a in self.get(self.library_id)["assets"]}
        paths = {}
        options = dict(options)
        if options.get("pip_enabled"):
            for key, kind in (("pip_video_asset", "video"), ("pip_image_asset", "image")):
                pool = options.get(key.replace("_asset", "_pool")) or []
                if not isinstance(pool, list) or not pool or any(x not in library or library[x]["kind"] != kind for x in pool):
                    raise ValueError("画中画需要扫描一个视频文件夹和一个图片文件夹")
                options[key] = random.SystemRandom().choice(pool)
        for key, kinds in (("pip_asset", {"video", "image"}), ("pip_video_asset", {"video"}),
                           ("pip_image_asset", {"image"}), ("progress_asset", {"image"}),
                           ("sticker_asset", {"image"}), ("music_asset", {"audio"})):
            selected = options.get(key)
            if selected:
                asset = library.get(selected)
                if not asset or asset["kind"] not in kinds:
                    raise ValueError("所选后处理素材无效")
                self.asset(self.library_id, selected)
                paths[key] = asset["path"]
        p = self.create({"title": Path(source).stem})
        imported = self.register(p["id"], source)
        a = imported["asset"]
        if a["kind"] != "video":
            raise ValueError("请导入有画面的视频文件")
        p = imported["project"]
        p.update(width=a["width"], height=a["height"], fps=options.get("fps", 30))
        duration = a["duration"]
        timeline = p["timelines"][0]
        timeline["name"] = "后处理"
        timeline["tracks"][0]["clips"] = [{"id": ident(), "asset_id": a["id"], "start": 0,
            "duration": duration, "volume": number(options.get("original_volume", 1), 0, 2, "原声音量")}]
        post = {"progress": bool(options.get("progress", True)), "pip_opacity": options.get("pip_opacity", .01),
                "pip_rotation": options.get("pip_rotation", 15), "sticker_visible": options.get("sticker_visible", .02),
                "progress_style": options.get("progress_style", "solid"), "progress_height": options.get("progress_height", 6),
                "progress_color": options.get("progress_color", "58d5b0")}
        post["pip_rotation_end"] = options.get("pip_rotation_end", 999)
        for key, path in paths.items():
            imported = self.register(p["id"], path)
            p["revision"] = imported["project"]["revision"]
            music = imported["asset"]
            if key != "music_asset":
                post[key] = music["id"]
                continue
            volume = number(options.get("music_volume", .1), 0, 1, "配乐音量")
            loop = bool(options.get("music_loop", True))
            length = duration if loop else min(duration, music["duration"])
            timeline["tracks"][2]["clips"].append({"id": ident(), "asset_id": music["id"], "start": 0,
                "duration": length, "volume": volume, "loop": loop,
                "fade_in": min(.4, length), "fade_out": min(.6, length)})
        p["postprocess"] = post
        return self.save(p["id"], p)

    def submit(self, payload):
        paths = payload.get("paths")
        if not isinstance(paths, list) or not 1 <= len(paths) <= 100:
            raise ValueError("请选择 1–100 个视频")
        options = payload.get("options") or {}
        prepared, errors = [], []
        # Prepare every item first; each immutable snapshot keeps the selected options.
        for source in paths:
            try:
                p = self.prepare(str(source), options)
                prepared.append(p)
            except (ValueError, OSError) as exc:
                errors.append({"path": str(source), "error": str(exc)})
        jobs = [self.export(p["id"], {"revision": p["revision"], "timeline_id": p["timelines"][0]["id"]}) for p in prepared]
        return {"jobs": jobs, "errors": errors}
