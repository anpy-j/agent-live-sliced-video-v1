"""Launch only the installed, isolated local ACE-Step CPU environment."""
import os
import subprocess
import threading
import urllib.parse
from pathlib import Path



class CpuMusicRuntime:
    def __init__(self, service):
        self.service = service
        self.process = None
        self.lock = threading.Lock()
        source = Path(os.environ.get("LIVECUT_ASSET_ROOT", Path(__file__).resolve().parents[2]))
        self.checkout = Path(os.environ.get("LIVECUT_ACESTEP_ROOT", source / ".runtime" / "ace-step"))
        self.python = Path(os.environ.get("LIVECUT_ACESTEP_PYTHON", source / ".runtime" / "ace-cpu" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")))
        self.log = service.root / "ace-cpu.log"

    def status(self):
        base = self.service.settings()["acestep_url"]
        running = self.process is not None and self.process.poll() is None
        # Avoid blocking the UI while a cold server is loading dependencies/models.
        import urllib.request
        try:
            with urllib.request.urlopen(base+"/health", timeout=1) as response:
                import json
                health = json.load(response).get("data", {})
                ready = response.status == 200 and health.get("service") == "ACE-Step API"
        except Exception:
            ready = False
            health = {}
        detail = ""
        if self.log.exists():
            with self.log.open("rb") as stream:
                stream.seek(max(0, self.log.stat().st_size-2500))
                detail = stream.read().decode("utf-8", errors="replace")
        return {"installed": self.python.is_file() and (self.checkout / "acestep" / "api_server.py").is_file(),
                "running": running, "ready": ready, "managed": self.process is not None,
                "models_initialized": bool(health.get("models_initialized")),
                "log": detail, "log_path": str(self.log)}

    def start(self):
        with self.lock:
            state = self.status()
            if state["ready"] or state["running"]:
                return state
            if not state["installed"]:
                raise ValueError("未安装独立 ACE-Step CPU 环境，请按文档安装后再启动")
            url = urllib.parse.urlsplit(self.service.settings()["acestep_url"])
            if url.scheme != "http" or url.hostname not in {"localhost", "127.0.0.1"} or url.path not in {"", "/"}:
                raise ValueError("启动本机 CPU 服务需要 http://127.0.0.1:端口 地址")
            env = {**os.environ, "ACESTEP_DEVICE": "cpu", "ACESTEP_INIT_LLM": "false", "ACESTEP_NO_INIT": "true",
                   "ACESTEP_USE_FLASH_ATTENTION": "false", "ACESTEP_COMPILE_MODEL": "false", "ACESTEP_LM_BACKEND": "pt",
                   "HF_HOME": str(self.checkout / "hf-cache"), "HF_HUB_DISABLE_XET": "1",
                   "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1", "OMP_NUM_THREADS": "8", "MKL_NUM_THREADS": "8"}
            with self.log.open("w", encoding="utf-8") as log:
                self.process = subprocess.Popen([str(self.python.resolve()), "-m", "acestep.api_server", "--host", "127.0.0.1", "--port", str(url.port or 80)],
                    cwd=self.checkout, env=env, stdout=log, stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0, start_new_session=os.name != "nt")
            return {**state, "running": True, "managed": True}

    def close(self):
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"], capture_output=True,
                                   creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
                else:
                    import signal
                    os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
