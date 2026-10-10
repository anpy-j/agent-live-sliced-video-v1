from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .model import SUBTITLE_STYLES, ident, new_project, validate


class Conflict(ValueError):
    pass


class EditorService:
    """Own database, assets, snapshots and serial export queue; no V1 state mutations."""

    def __init__(self, root: Path):
        self.root = Path(root) / "data" / "editor"
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "editor.db"
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.stop_event = threading.Event()
        self.processes: dict[str, subprocess.Popen] = {}
        with self.connect() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, data TEXT NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS exports(id TEXT PRIMARY KEY, project_id TEXT, data TEXT NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY, data TEXT NOT NULL);
            """)
            for row in con.execute("SELECT id,data FROM exports").fetchall():
                job = json.loads(row[1])
                if job["status"] == "running":
                    job.update(status="failed", error="上次导出被中断，可重新导出")
                    con.execute("UPDATE exports SET data=? WHERE id=?", (json.dumps(job), row[0]))
        self.worker = threading.Thread(target=self._work, name="editor-export", daemon=True)
        self.worker.start()
        self.wake.set()

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.db, timeout=30)
        try:
            con.execute("PRAGMA journal_mode=WAL")
            with con:
                yield con
        finally:
            con.close()

    def close(self):
        self.stop_event.set()
        self.wake.set()
        with self.lock:
            for p in self.processes.values():
                if p.poll() is None:
                    p.terminate()
        self.worker.join(timeout=10)

    def list_projects(self):
        with self.connect() as con:
            rows = con.execute("SELECT data,updated FROM projects ORDER BY updated DESC").fetchall()
        return {"projects": [{"id": p["id"], "title": p["title"], "revision": p["revision"],
                              "updated": updated, "timeline_count": len(p["timelines"])}
                             for data, updated in rows for p in [json.loads(data)]]}

    def create(self, payload: dict):
        p = new_project(str(payload.get("title") or "未命名剪辑"))
        p["source_job_id"] = str(payload.get("source_job_id") or "")
        with self.connect() as con:
            con.execute("INSERT INTO projects VALUES (?,?,?)", (p["id"], json.dumps(p), time.time()))
        return p

    def get(self, project_id: str):
        with self.connect() as con:
            row = con.execute("SELECT data FROM projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            raise KeyError("剪辑项目不存在")
        return json.loads(row[0])

    def save(self, project_id: str, payload: dict):
        with self.lock, self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT data FROM projects WHERE id=?", (project_id,)).fetchone()
            if not row:
                raise KeyError("剪辑项目不存在")
            old = json.loads(row[0])
            if payload.get("revision") != old["revision"]:
                raise Conflict("项目已在其他窗口更新，请重新打开项目")
            p = validate(payload, {a["id"]: a for a in old["assets"]})
            p.update(id=project_id, revision=old["revision"] + 1,
                     source_job_id=old.get("source_job_id", ""))
            con.execute("UPDATE projects SET data=?,updated=? WHERE id=?", (json.dumps(p), time.time(), project_id))
        return p

    def register(self, project_id: str, path: str):
        media = Path(path).expanduser().resolve()
        if not media.is_file():
            raise ValueError("素材文件不存在")
        allowed = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".png", ".jpg", ".jpeg", ".webp", ".gif"}
        if media.suffix.lower() not in allowed:
            raise ValueError("不支持的素材格式")
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(media)],
                               capture_output=True, encoding="utf-8", errors="replace", timeout=30,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if probe.returncode:
            raise ValueError("无法读取素材：" + probe.stderr[-500:])
        data = json.loads(probe.stdout)
        streams = data.get("streams", [])
        video = next((x for x in streams if x["codec_type"] == "video"), None)
        audio = next((x for x in streams if x["codec_type"] == "audio"), None)
        kind = "image" if media.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"} else "video" if video else "audio"
        duration = float(data.get("format", {}).get("duration") or (video or audio or {}).get("duration") or 0)
        if kind != "image" and duration <= 0:
            raise ValueError("素材没有有效时长")
        stat = media.stat()
        asset = {"id": ident(), "path": str(media), "name": media.name, "kind": kind,
                 "duration": duration, "width": (video or {}).get("width", 0),
                 "height": (video or {}).get("height", 0), "has_audio": bool(audio),
                 "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        with self.lock, self.connect() as con:
            p = self.get(project_id)
            old = next((a for a in p["assets"] if a["path"] == str(media) and a["mtime_ns"] == stat.st_mtime_ns and a["size"] == stat.st_size), None)
            if old:
                return {"project": p, "asset": old}
            p["assets"].append(asset)
            p["revision"] += 1
            con.execute("UPDATE projects SET data=?,updated=? WHERE id=?", (json.dumps(p), time.time(), project_id))
        return {"project": p, "asset": asset}

    def asset(self, project_id: str, asset_id: str):
        asset = next((a for a in self.get(project_id)["assets"] if a["id"] == asset_id), None)
        if not asset:
            raise KeyError("素材未导入")
        path = Path(asset["path"])
        if not path.is_file():
            raise ValueError("素材已移动，请重新导入")
        stat = path.stat()
        if stat.st_size != asset["size"] or stat.st_mtime_ns != asset["mtime_ns"]:
            raise ValueError("素材已被修改，请重新导入")
        return asset

    def cache(self, project_id: str, asset_id: str, kind: str):
        a = self.asset(project_id, asset_id)
        folder = self.root / "cache"
        folder.mkdir(exist_ok=True)
        stem = hashlib.sha256(f"{a['path']}:{a['size']}:{a['mtime_ns']}".encode()).hexdigest()[:24]
        path = folder / f"{stem}.{ 'jpg' if kind == 'thumbnail' else 'json'}"
        if path.exists():
            return path
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        if kind == "thumbnail":
            if a["kind"] == "audio":
                raise ValueError("音频素材没有画面")
            args = ["ffmpeg", "-v", "error", "-y", "-i", a["path"], "-frames:v", "1", "-vf", "scale=160:-1", str(path)]
            subprocess.run(args, check=True, capture_output=True, timeout=30, creationflags=flags)
        else:
            if not a["has_audio"]:
                path.write_text("[]", encoding="utf-8")
            else:
                result = subprocess.run(["ffmpeg", "-v", "error", "-i", a["path"], "-vn", "-ac", "1", "-ar", "100", "-f", "f32le", "-"],
                                        capture_output=True, check=True, timeout=90, creationflags=flags)
                import array
                values = array.array("f")
                values.frombytes(result.stdout)
                step = max(1, len(values) // 600)
                peaks = [round(max(abs(x) for x in values[i:i + step]), 4) for i in range(0, len(values), step)]
                path.write_text(json.dumps(peaks), encoding="utf-8")
        return path

    def settings(self, payload=None):
        with self.connect() as con:
            row = con.execute("SELECT data FROM settings WHERE id=1").fetchone()
            data = json.loads(row[0]) if row else {"output_dir": str(Path.home() / "Movies" / "LiveCut"), "audio_bitrate": "192k"}
            if payload is not None:
                directory = str(payload.get("output_dir") or "").strip()
                if not directory or not Path(directory).expanduser().is_absolute():
                    raise ValueError("输出目录必须是绝对路径")
                bitrate = str(payload.get("audio_bitrate", "192k"))
                if bitrate not in {"128k", "192k", "256k", "320k"}:
                    raise ValueError("不支持的音频码率")
                data.update(output_dir=str(Path(directory).expanduser()), audio_bitrate=bitrate)
                con.execute("INSERT OR REPLACE INTO settings VALUES (1,?)", (json.dumps(data),))
        return {**data, "subtitle_styles": SUBTITLE_STYLES,
                "ffmpeg_available": bool(shutil.which("ffmpeg")), "ffprobe_available": bool(shutil.which("ffprobe"))}

    def exports(self):
        with self.connect() as con:
            rows = con.execute("SELECT data FROM exports ORDER BY updated DESC LIMIT 100").fetchall()
        return {"exports": [json.loads(r[0]) for r in rows]}

    def export(self, project_id: str, payload: dict):
        p = self.get(project_id)
        if payload.get("revision") != p["revision"]:
            raise Conflict("请先保存最新编辑再导出")
        timeline = next((t for t in p["timelines"] if t["id"] == payload.get("timeline_id")), None)
        if not timeline or not any(t["clips"] for t in timeline["tracks"]):
            raise ValueError("请选择非空时间线")
        for t in timeline["tracks"]:
            for c in t["clips"]:
                if c.get("asset_id"):
                    self.asset(project_id, c["asset_id"])
        settings = self.settings()
        job_id = ident()
        name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', f"{p['title']}-{timeline['name']}")[:150]
        output = Path(settings["output_dir"]) / f"{name}-{job_id[:8]}.mp4"
        snapshot = copy.deepcopy(p)
        snapshot["timelines"] = [timeline]
        snapshot["audio_bitrate"] = settings["audio_bitrate"]
        folder = self.root / "exports" / job_id
        folder.mkdir(parents=True)
        (folder / "snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
        job = {"id": job_id, "project_id": project_id, "title": name, "status": "queued", "progress": 0,
               "output": str(output), "error": "", "snapshot": str(folder / "snapshot.json")}
        with self.connect() as con:
            con.execute("INSERT INTO exports VALUES (?,?,?,?)", (job_id, project_id, json.dumps(job), time.time()))
        self.wake.set()
        return job

    def _update_export(self, job):
        with self.connect() as con:
            con.execute("UPDATE exports SET data=?,updated=? WHERE id=?", (json.dumps(job), time.time(), job["id"]))

    def cancel(self, job_id: str):
        with self.lock:
            job = next((j for j in self.exports()["exports"] if j["id"] == job_id), None)
            if not job:
                raise KeyError("导出任务不存在")
            if job["status"] in {"queued", "running"}:
                job.update(status="cancelled", error="已取消")
                self._update_export(job)
                process = self.processes.get(job_id)
                if process and process.poll() is None:
                    process.terminate()
        return job

    def _work(self):
        from .render import render
        while not self.stop_event.is_set():
            self.wake.wait(1)
            self.wake.clear()
            with self.lock:
                jobs = [j for j in reversed(self.exports()["exports"]) if j["status"] == "queued"]
                if not jobs:
                    continue
                job = jobs[0]
                job["status"] = "running"
                self._update_export(job)
            def progress(value):
                with self.lock:
                    current = next(j for j in self.exports()["exports"] if j["id"] == job["id"])
                    if current["status"] == "cancelled" or self.stop_event.is_set():
                        raise RuntimeError("已取消")
                    job["progress"] = value
                    self._update_export(job)
            def process_started(process):
                with self.lock:
                    self.processes[job["id"]] = process
                    current = next(j for j in self.exports()["exports"] if j["id"] == job["id"])
                    if current["status"] == "cancelled" or self.stop_event.is_set():
                        process.terminate()
            try:
                snapshot = json.loads(Path(job["snapshot"]).read_text(encoding="utf-8"))
                render(snapshot, Path(job["output"]), progress, process_started)
                with self.lock:
                    current = next(j for j in self.exports()["exports"] if j["id"] == job["id"])
                    if current["status"] != "cancelled":
                        job.update(status="completed", progress=100)
                        self._update_export(job)
            except Exception as exc:
                with self.lock:
                    current = next(j for j in self.exports()["exports"] if j["id"] == job["id"])
                    if current["status"] != "cancelled":
                        job.update(status="failed", error=str(exc)[-2000:])
                        self._update_export(job)
            finally:
                with self.lock:
                    self.processes.pop(job["id"], None)
            self.wake.set()
