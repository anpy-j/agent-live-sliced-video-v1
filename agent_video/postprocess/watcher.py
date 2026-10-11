"""Persistent folder intake and content-based, single-use overlay reservations."""
import hashlib
import json
import random
import threading
import time
from pathlib import Path

from ..editor.model import ident

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
DEFAULT_OUTPUT = r"D:\切片\袁艺灵\完成版"


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class FolderWatcher:
    def __init__(self, service, *, interval=3, stable_seconds=10):
        self.service = service
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.interval, self.stable_seconds = interval, stable_seconds
        self.observed, self.hashes = {}, {}
        self.state, self.error = "stopped", ""
        self.available = {"video": 0, "image": 0}
        with service.connect() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS watch_config(id INTEGER PRIMARY KEY,data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS watch_records(id TEXT PRIMARY KEY,source_hash TEXT UNIQUE,data TEXT NOT NULL,created REAL);
                CREATE TABLE IF NOT EXISTS watch_used(kind TEXT,hash TEXT,record_id TEXT,path TEXT,PRIMARY KEY(kind,hash));
            """)
            row = con.execute("SELECT data FROM watch_config WHERE id=1").fetchone()
            self.config = json.loads(row[0]) if row else {"enabled": False, "input_dir": "", "output_dir": DEFAULT_OUTPUT,
                "video_dir": "", "image_dir": "", "options": {}}
            # Recover the narrow interruption window between export insertion and linking.
            for row in con.execute("SELECT data FROM watch_records").fetchall():
                record = json.loads(row[0])
                if record["status"] == "preparing":
                    export = con.execute("SELECT data FROM exports WHERE project_id=? ORDER BY updated DESC LIMIT 1",
                                         (record.get("project_id", ""),)).fetchone()
                    if export:
                        job = json.loads(export[0])
                        record.update(export_id=job["id"], output=job["output"], status=job["status"])
                    else:
                        record.update(status="failed", error="上次提交被中断，可重试；预留素材保留")
                    con.execute("UPDATE watch_records SET data=? WHERE id=?", (json.dumps(record), record["id"]))
        self.thread = threading.Thread(target=self._run, daemon=True, name="postprocess-watch")
        self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=35)

    def _save(self, record):
        with self.service.connect() as con:
            con.execute("UPDATE watch_records SET data=? WHERE id=?", (json.dumps(record), record["id"]))

    def _records(self):
        with self.service.connect() as con:
            return [json.loads(r[0]) for r in con.execute("SELECT data FROM watch_records ORDER BY created DESC")]

    def status(self):
        with self.lock:
            records = self._records()
            with self.service.connect() as con:
                used = {kind: con.execute("SELECT COUNT(*) FROM watch_used WHERE kind=?", (kind,)).fetchone()[0]
                        for kind in ("video", "image")}
            return {"config": self.config, "state": self.state, "error": self.error,
                    "available": self.available, "used": used, "records": records[:100]}

    def configure(self, payload):
        config = {key: str(payload.get(key) or "").strip() for key in ("input_dir", "output_dir", "video_dir", "image_dir")}
        for key in config:
            path = Path(config[key]).expanduser()
            if not config[key] or not path.is_absolute() or (key != "output_dir" and not path.is_dir()):
                raise ValueError("请选择有效的监控、输出、视频素材与图片素材目录")
            config[key] = str(path.resolve())
        source, output = Path(config["input_dir"]), Path(config["output_dir"])
        if source == output or output.is_relative_to(source) or source.is_relative_to(output):
            raise ValueError("监控目录与输出目录不能相同或互相包含")
        for key in ("video_dir", "image_dir"):
            material = Path(config[key])
            if material == source or material.is_relative_to(source) or source.is_relative_to(material):
                raise ValueError("画中画素材目录不能与监控目录重叠")
            if material == output or material.is_relative_to(output) or output.is_relative_to(material):
                raise ValueError("画中画素材目录不能与输出目录重叠")
        options = dict(payload.get("options") or {})
        library = {a["id"]: a for a in self.service.library()["assets"]}
        for key in ("progress_asset", "sticker_asset"):
            if options.get(key) not in library or library[options[key]]["kind"] != "image":
                raise ValueError("请先选择动态进度条与贴纸素材")
            self.service.asset(self.service.library_id, options[key])
        options.update(pip_enabled=True, progress=True, pip_opacity=.01, pip_rotation_end=999)
        config.update(enabled=True, options=options)
        with self.lock:
            self.config = config
            self.observed.clear()
            self.state, self.error = "watching", ""
            with self.service.connect() as con:
                con.execute("INSERT OR REPLACE INTO watch_config VALUES (1,?)", (json.dumps(config),))
        return self.status()

    def pause(self):
        with self.lock:
            self.config = {**self.config, "enabled": False}
            self.state, self.error = "stopped", ""
            with self.service.connect() as con:
                con.execute("INSERT OR REPLACE INTO watch_config VALUES (1,?)", (json.dumps(self.config),))
        return self.status()

    def _hash(self, path):
        path = Path(path)
        stat = path.stat()
        stamp = (stat.st_size, stat.st_mtime_ns)
        key = str(path.resolve())
        cached = self.hashes.get(key)
        if cached and cached[0] == stamp:
            return cached[1]
        result = digest(path)
        end = path.stat()
        if stamp != (end.st_size, end.st_mtime_ns):
            raise OSError("文件还在写入，等待完成")
        self.hashes[key] = (stamp, result)
        return result

    def _pool(self, kind):
        folder = Path(self.config[kind + "_dir"])
        if not folder.is_dir():
            raise ValueError("画中画素材目录不可用")
        extensions = VIDEO_EXTENSIONS if kind == "video" else {".png", ".jpg", ".jpeg", ".webp", ".gif"}
        paths = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in extensions)
        if len(paths) > 500:
            raise ValueError("单个素材文件夹最多支持 500 个文件")
        library = {a["path"]: a for a in self.service.library()["assets"]}
        with self.service.connect() as con:
            used = {r[0] for r in con.execute("SELECT hash FROM watch_used WHERE kind=?", (kind,))}
        pool = {}
        for path in paths:
            if self.stop.is_set():
                return []
            stat = path.stat()
            asset = library.get(str(path.resolve()))
            if not asset or (asset["size"], asset["mtime_ns"]) != (stat.st_size, stat.st_mtime_ns):
                try:
                    asset = self.service.import_asset({"path": str(path)})
                except ValueError:
                    continue
            if asset["kind"] != kind:
                continue
            sha = self._hash(asset["path"])
            if sha not in used:
                pool[sha] = {"hash": sha, "asset_id": asset["id"], "path": asset["path"]}
        self.available[kind] = len(pool)
        return list(pool.values())

    def _enqueue(self, record):
        try:
            if self._hash(record["source"]) != record["source_hash"]:
                raise ValueError("原视频已改变，不能按原任务重试")
            options = dict(record["options"])
            source_asset = self.service.import_asset({"path": record["source"]})
            options["progress_height"] = min(options.get("progress_height", 40), max(2, source_asset["height"] // 4))
            for kind in ("video", "image"):
                chosen = record[kind]
                if self._hash(chosen["path"]) != chosen["hash"]:
                    raise ValueError("已预留的画中画素材被修改")
                asset = self.service.import_asset({"path": chosen["path"]})
                options["pip_" + kind + "_pool"] = [asset["id"]]
            p = self.service.prepare(record["source"], options)
            if self._hash(record["source"]) != record["source_hash"]:
                raise ValueError("原视频还在写入，请完成导出后重试")
            record["project_id"] = p["id"]
            self._save(record)
            job = self.service.export(p["id"], {"revision": p["revision"], "timeline_id": p["timelines"][0]["id"],
                                                "output_dir": record["output_dir"]})
            record.update(export_id=job["id"], output=job["output"], status=job["status"], error="")
        except (ValueError, OSError, KeyError) as exc:
            record.update(status="failed", error=str(exc))
        self._save(record)

    def retry(self, record_id):
        with self.lock:
            record = next((r for r in self._records() if r["id"] == record_id), None)
            if not record:
                raise KeyError("自动任务不存在")
            if record["status"] not in {"failed", "cancelled"}:
                raise ValueError("只有失败或取消的任务可重试")
            record.update(status="preparing", error="", export_id="")
            self._save(record)
            self._enqueue(record)
        return self.status()

    def tick(self):
        with self.lock:
            records = self._records()
            for record in records:
                if record.get("export_id") and record["status"] in {"queued", "running"}:
                    job = self.service.get_export(record["export_id"])
                    record.update(status=job["status"], progress=job["progress"], error=job["error"])
                    self._save(record)
            if not self.config["enabled"] or self.stop.is_set():
                return
            self.state, self.error = "watching", ""
            folder = Path(self.config["input_dir"])
            if not folder.is_dir():
                raise ValueError("监控目录不可用，请检查路径")
            known = {r["source_hash"] for r in records}
            # Bound automatic submissions; the shared renderer is serial.
            if any(r["status"] in {"queued", "running", "preparing"} for r in records):
                self.state = "processing"
                return
            for path in sorted(folder.iterdir()):
                if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
                    continue
                stat = path.stat()
                stamp = (stat.st_size, stat.st_mtime_ns)
                key = str(path.resolve())
                previous = self.observed.get(key)
                now = time.monotonic()
                if not previous or previous[0] != stamp:
                    self.observed[key] = (stamp, now)
                    continue
                if not stamp[0] or now - previous[1] < self.stable_seconds:
                    continue
                sha = self._hash(path)
                if sha in known:
                    continue
                # Validate the input before consuming overlay reservations.
                try:
                    asset = self.service.import_asset({"path": str(path)})
                    if asset["kind"] != "video":
                        raise ValueError("文件中没有视频画面")
                except (OSError, ValueError) as exc:
                    record = {"id": ident(), "source": str(path), "source_hash": sha, "status": "failed",
                              "error": str(exc), "created": time.time(), "export_id": "", "options": {}, "output": ""}
                    with self.service.connect() as con:
                        con.execute("INSERT INTO watch_records VALUES (?,?,?,?)", (record["id"], sha, json.dumps(record), record["created"]))
                    continue
                candidates = {kind: self._pool(kind) for kind in ("video", "image")}
                if self.stop.is_set():
                    return
                if any(not values for values in candidates.values()):
                    self.state, self.error = "exhausted", "未使用的画中画视频或图片已用完，请向素材文件夹补充新素材"
                    return
                record = {"id": ident(), "source": str(path), "source_hash": sha, "created": time.time(),
                          "status": "preparing", "error": "", "export_id": "", "output": "",
                          "options": self.config["options"], "output_dir": self.config["output_dir"]}
                for kind, values in candidates.items():
                    record[kind] = random.SystemRandom().choice(values)
                # The source and both single-use reservations are committed together.
                with self.service.connect() as con:
                    con.execute("INSERT INTO watch_records VALUES (?,?,?,?)", (record["id"], sha, json.dumps(record), record["created"]))
                    for kind in candidates:
                        chosen = record[kind]
                        con.execute("INSERT INTO watch_used VALUES (?,?,?,?)", (kind, chosen["hash"], record["id"], chosen["path"]))
                        self.available[kind] -= 1
                self._enqueue(record)
                self.state = "processing"
                return

    def _run(self):
        while not self.stop.wait(self.interval):
            try:
                self.tick()
            except Exception as exc:
                with self.lock:
                    self.state, self.error = "error", str(exc)[-1200:]
