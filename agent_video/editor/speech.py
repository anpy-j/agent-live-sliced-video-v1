import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from .model import ident


class SpeechQueue:
    def __init__(self, service):
        self.service = service
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.process = None
        self.lock = threading.RLock()
        with service.connect() as con:
            con.execute("CREATE TABLE IF NOT EXISTS speech(id TEXT PRIMARY KEY,data TEXT NOT NULL,updated REAL NOT NULL)")
            for row in con.execute("SELECT id,data FROM speech").fetchall():
                job = json.loads(row[1])
                if job["status"] in {"queued", "running"}:
                    job.update(status="failed", error="识别被中断，请重新识别")
                    con.execute("UPDATE speech SET data=? WHERE id=?", (json.dumps(job), row[0]))
        self.thread = threading.Thread(target=self.run, daemon=True, name="editor-speech")
        self.thread.start()

    def put(self, job):
        with self.service.connect() as con:
            con.execute("INSERT OR REPLACE INTO speech VALUES(?,?,?)", (job["id"], json.dumps(job), time.time()))

    def get(self, job_id):
        with self.service.connect() as con:
            row = con.execute("SELECT data FROM speech WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise KeyError("字幕识别任务不存在")
        return json.loads(row[0])

    def start(self, project_id, payload):
        project = self.service.get(project_id)
        if project["revision"] != payload.get("revision"):
            from .service import Conflict
            raise Conflict("请先保存项目再识别")
        timeline = next((t for t in project["timelines"] if t["id"] == payload.get("timeline_id")), None)
        clip = next((c for t in (timeline or {}).get("tracks", []) if t["kind"] in {"video", "audio"}
                     for c in t["clips"] if c["id"] == payload.get("clip_id")), None)
        if not clip:
            raise ValueError("请选择有声音的片段")
        asset = self.service.asset(project_id, clip["asset_id"])
        if not asset["has_audio"]:
            raise ValueError("素材没有音轨")
        job_id = ident()
        folder = self.service.root / "speech"
        folder.mkdir(exist_ok=True)
        request = folder / (job_id + ".json")
        request.write_text(json.dumps({**clip, "path": asset["path"], "model": self.service.settings()["asr_model"]}), encoding="utf-8")
        job = {"id": job_id, "project_id": project_id, "timeline_id": timeline["id"],
               "clip_id": clip["id"], "status": "queued", "request": str(request), "error": "", "segments": []}
        self.put(job)
        self.wake.set()
        return job

    def cancel(self, job_id):
        with self.lock:
            job = self.get(job_id)
            if job["status"] in {"queued", "running"}:
                job.update(status="cancelled")
                self.put(job)
                if self.process and self.process[0] == job_id and self.process[1].poll() is None:
                    self.process[1].terminate()
        return job

    def close(self):
        self.stop.set()
        self.wake.set()
        with self.lock:
            if self.process and self.process[1].poll() is None:
                self.process[1].terminate()
        self.thread.join(timeout=5)

    def run(self):
        while not self.stop.is_set():
            self.wake.wait(1)
            self.wake.clear()
            with self.lock, self.service.connect() as con:
                jobs = [json.loads(r[0]) for r in con.execute("SELECT data FROM speech ORDER BY updated").fetchall()]
                job = next((j for j in jobs if j["status"] == "queued"), None)
                if not job:
                    continue
                job["status"] = "running"
                self.put(job)
            request = Path(job["request"])
            cmd = [sys.executable] + (["--editor-transcribe"] if getattr(sys, "frozen", False) else ["-m", "agent_video.editor.transcribe"])
            try:
                with request.with_suffix(".log").open("w", encoding="utf-8") as log:
                    with self.lock:
                        if self.get(job["id"])["status"] == "cancelled" or self.stop.is_set():
                            continue
                        process = subprocess.Popen(cmd + ["--request", str(request)], stdout=log, stderr=log,
                                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                        self.process = (job["id"], process)
                    code = process.wait()
                if code:
                    raise RuntimeError(request.with_suffix(".log").read_text(encoding="utf-8", errors="replace")[-1500:])
                job.update(status="completed", segments=json.loads(request.with_suffix(".result.json").read_text(encoding="utf-8")))
            except Exception as exc:
                job.update(status="failed", error=str(exc))
            finally:
                with self.lock:
                    if self.get(job["id"])["status"] != "cancelled":
                        self.put(job)
                    self.process = None
                self.wake.set()
