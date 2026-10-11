"""ACE-Step asynchronous API adapter, isolated from the FFmpeg export worker."""
import json
import os
import threading
import time
import urllib.parse
import urllib.request

from ..editor.model import ident, number


def validate_url(value):
    value = str(value).strip().rstrip("/")
    url = urllib.parse.urlsplit(value)
    if url.scheme not in {"http", "https"} or not url.netloc or url.username or url.password or url.query or url.fragment:
        raise ValueError("ACE-Step 服务地址需要是 http/https 地址")
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("音乐服务返回重定向，请检查服务地址")


def request(base, route, payload=None, *, binary=False):
    url = urllib.parse.urljoin(base+"/", route)
    if urllib.parse.urlsplit(url).netloc != urllib.parse.urlsplit(base).netloc or urllib.parse.urlsplit(url).scheme != urllib.parse.urlsplit(base).scheme:
        raise ValueError("音乐下载地址与配置的服务不一致")
    headers = {"Content-Type": "application/json"}
    if os.environ.get("ACESTEP_API_KEY"):
        headers["Authorization"] = "Bearer " + os.environ["ACESTEP_API_KEY"]
    req = urllib.request.Request(url, data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
    with urllib.request.build_opener(NoRedirect()).open(req, timeout=60) as response:
        data = response.read(200*1024*1024+1 if binary else 2*1024*1024)
    if binary:
        if len(data)>200*1024*1024:
            raise ValueError("生成音频超过 200 MB")
        return data
    result = json.loads(data)
    if result.get("error") or result.get("code", 200) != 200:
        raise ValueError(str(result.get("error") or "音乐服务请求失败"))
    return result.get("data", result)


class MusicQueue:
    def __init__(self, service):
        self.service = service
        self.stop = threading.Event()
        self.lock = threading.RLock()
        self.threads = []
        with service.connect() as con:
            con.execute("CREATE TABLE IF NOT EXISTS music(id TEXT PRIMARY KEY,data TEXT NOT NULL)")
            for row in con.execute("SELECT data FROM music").fetchall():
                job = json.loads(row[0])
                if job["status"] == "running":
                    job.update(status="failed", error="上次生成跟踪被中断")
                    con.execute("UPDATE music SET data=? WHERE id=?", (json.dumps(job), job["id"]))

    def close(self):
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=1)

    def jobs(self):
        with self.service.connect() as con:
            return {"jobs": [json.loads(row[0]) for row in con.execute("SELECT data FROM music ORDER BY rowid DESC LIMIT 50")]}

    def update(self, job):
        with self.service.connect() as con:
            con.execute("INSERT OR REPLACE INTO music VALUES (?,?)", (job["id"], json.dumps(job)))

    def start(self, payload):
        prompt = str(payload.get("prompt") or "").strip()[:2000]
        if not prompt:
            raise ValueError("请填写配乐风格描述")
        params = {"prompt": prompt+", instrumental background music, no vocals, continuous musical arrangement throughout the track, no long silent gaps", "lyrics": "[Instrumental]",
                  "audio_duration": number(payload.get("duration", 30), 10, 180, "音乐时长"),
                  "bpm": int(number(payload.get("bpm", 100), 30, 300, "BPM")), "audio_format": "wav",
                  "thinking": False, "use_cot_caption": False, "use_cot_language": False,
                  "batch_size": 1, "inference_steps": 8}
        base = validate_url(self.service.settings()["acestep_url"])
        with self.lock:
            if any(j["status"] == "running" for j in self.jobs()["jobs"]):
                raise ValueError("已有音乐正在生成，请先等待完成")
            job = {"id": ident(), "status": "running", "prompt": prompt, "params": params,
                   "created": time.time(), "error": "", "service": base}
            self.update(job)
            thread = threading.Thread(target=self._run, args=(job,), daemon=True, name="postprocess-music")
            self.threads.append(thread)
            thread.start()
        return job

    def _run(self, job):
        try:
            result = request(job["service"], "/release_task", job["params"])
            job["task_id"] = result["task_id"]
            self.update(job)
            deadline = time.monotonic()+3600
            while not self.stop.is_set() and time.monotonic()<deadline:
                rows = request(job["service"], "/query_result", {"task_id_list": [job["task_id"]]})
                row = next((r for r in rows if r.get("task_id")==job["task_id"]), None)
                if row and row.get("status") == 2:
                    raise ValueError(str(row.get("result") or "音乐生成失败")[:1000])
                if row and row.get("status") == 1:
                    items = json.loads(row["result"]) if isinstance(row["result"], str) else row["result"]
                    data = request(job["service"], items[0]["file"], binary=True)
                    path = self.service.root / "resources" / f"AI配乐-{job['id'][:8]}.wav"
                    path.write_bytes(data)
                    job.update(result=items[0], generated_path=str(path))
                    from .audio_quality import analyze_generated_audio
                    job["quality"] = analyze_generated_audio(path, job["params"]["audio_duration"])
                    if job["quality"]["error"]:
                        raise ValueError(job["quality"]["error"])
                    a = self.service.import_asset({"path": str(path)})
                    if a["kind"] != "audio":
                        raise ValueError("音乐服务返回的文件不是音频")
                    job.update(status="completed", asset_id=a["id"], result=items[0])
                    self.update(job)
                    return
                self.stop.wait(5)
            raise ValueError("音乐生成跟踪已停止或超过一小时；服务端任务可能继续")
        except Exception as exc:
            job.update(status="failed", error=str(exc)[-1000:])
            self.update(job)
