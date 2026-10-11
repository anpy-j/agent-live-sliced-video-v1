"""Persistent folder intake; use every content-based overlay pair once per round."""
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
        self.pool_hashes = {"video": set(), "image": set()}
        with service.connect() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS watch_config(id INTEGER PRIMARY KEY,data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS watch_records(id TEXT PRIMARY KEY,source_hash TEXT UNIQUE,data TEXT NOT NULL,created REAL);
                CREATE TABLE IF NOT EXISTS watch_used(kind TEXT,hash TEXT,record_id TEXT,path TEXT,PRIMARY KEY(kind,hash));
                CREATE TABLE IF NOT EXISTS watch_pairs(round INTEGER,video_hash TEXT,image_hash TEXT,record_id TEXT,
                    PRIMARY KEY(round,video_hash,image_hash));
                CREATE TABLE IF NOT EXISTS watch_round(id INTEGER PRIMARY KEY,round INTEGER NOT NULL);
            """)
            # Preserve selections made by the former single-use allocator as round one.
            if not con.execute("SELECT round FROM watch_round WHERE id=1").fetchone():
                con.execute("INSERT INTO watch_round VALUES (1,1)")
                for row in con.execute("SELECT data FROM watch_records").fetchall():
                    record = json.loads(row[0])
                    if record.get("video") and record.get("image"):
                        record["round"] = 1
                        con.execute("INSERT OR IGNORE INTO watch_pairs VALUES (1,?,?,?)",
                                    (record["video"]["hash"], record["image"]["hash"], record["id"]))
                        con.execute("UPDATE watch_records SET data=? WHERE id=?", (json.dumps(record), record["id"]))
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
                used = {kind: con.execute(f"SELECT COUNT(DISTINCT {kind}_hash) FROM watch_pairs").fetchone()[0]
                        for kind in ("video", "image")}
                current_round = con.execute("SELECT round FROM watch_round WHERE id=1").fetchone()[0]
                pairs = con.execute("SELECT video_hash,image_hash FROM watch_pairs WHERE round=?", (current_round,)).fetchall()
                count = sum(v in self.pool_hashes["video"] and i in self.pool_hashes["image"] for v, i in pairs)
                total = self.available["video"] * self.available["image"]
                cycle = {"round": current_round, "total": total, "used": count, "remaining": total-count,
                         "allocated": con.execute("SELECT COUNT(*) FROM watch_pairs").fetchone()[0]}
            return {"config": self.config, "state": self.state, "error": self.error,
                    "available": self.available, "used": used, "cycle": cycle, "records": records[:100]}

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
            pool[sha] = {"hash": sha, "asset_id": asset["id"], "path": asset["path"]}
        self.available[kind] = len(pool)
        self.pool_hashes[kind] = set(pool)
        return list(pool.values())

    def _reserve_pair(self, record, candidates):
        # Select and commit the source, pair and round together. Never reset history.
        with self.service.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            current_round = con.execute("SELECT round FROM watch_round WHERE id=1").fetchone()[0]
            used = set(con.execute("SELECT video_hash,image_hash FROM watch_pairs WHERE round=?", (current_round,)).fetchall())
            # Reservoir sampling avoids storing N*M combinations (up to 500*500).
            selected, count = None, 0
            rng = random.SystemRandom()
            for video in candidates["video"]:
                for image in candidates["image"]:
                    if (video["hash"], image["hash"]) not in used:
                        count += 1
                        if rng.randrange(count) == 0:
                            selected = (video, image)
            if selected is None:
                current_round += 1
                con.execute("UPDATE watch_round SET round=? WHERE id=1", (current_round,))
                selected = (rng.choice(candidates["video"]), rng.choice(candidates["image"]))
            record.update(video=selected[0], image=selected[1], round=current_round)
            con.execute("INSERT INTO watch_records VALUES (?,?,?,?)", (record["id"], record["source_hash"], json.dumps(record), record["created"]))
            con.execute("INSERT INTO watch_pairs VALUES (?,?,?,?)", (current_round, record["video"]["hash"], record["image"]["hash"], record["id"]))

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
                    self.state, self.error = "exhausted", "画中画视频或图片文件夹没有可用素材，请补充素材"
                    return
                record = {"id": ident(), "source": str(path), "source_hash": sha, "created": time.time(),
                          "status": "preparing", "error": "", "export_id": "", "output": "",
                          "options": self.config["options"], "output_dir": self.config["output_dir"]}
                self._reserve_pair(record, candidates)
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
