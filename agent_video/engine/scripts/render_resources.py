"""Bound FFmpeg threads and adapt its CPU affinity to system headroom.

Codec thread counts are fixed at startup. On Windows/Linux, changing the
process affinity limits how many CPUs those threads can run on during a render.
Memory estimates are admission heuristics, not hard limits on FFmpeg's RSS.
"""
from dataclasses import asdict, dataclass
import json
import math
import os
import subprocess
import sys
import time

try:
    import psutil
except ImportError:
    psutil = None

GIB = 1024 ** 3
MIB = 1024 ** 2
SAMPLE_SECONDS = 5.0


@dataclass(frozen=True)
class Snapshot:
    cpu_percent: float
    available: int
    total: int

    @property
    def memory_percent(self):
        return 100 * (1 - self.available / self.total)


@dataclass(frozen=True)
class Budget:
    encoder_threads: int
    filter_threads: int
    decoder_threads: int = 1
    reserve_bytes: int = 0
    target_cpu: float = 80.0
    monitored: bool = True


def snapshot(interval=0.0):
    if psutil is None:
        return None
    cpu = psutil.cpu_percent(interval=interval)
    memory = psutil.virtual_memory()
    return Snapshot(cpu, memory.available, memory.total)


def config():
    try:
        target = float(os.environ.get("RENDER_TARGET_CPU_PERCENT", "80"))
        cap = int(os.environ.get("RENDER_MAX_THREADS", "8"))
    except ValueError as exc:
        raise RuntimeError("渲染资源配置必须是数字") from exc
    if not math.isfinite(target) or not 20 <= target <= 90 or cap < 1:
        raise RuntimeError("RENDER_TARGET_CPU_PERCENT 必须为 20~90；RENDER_MAX_THREADS 必须 >=1")
    return target, cap


def choose_budget(state, logical_cpus, decoder_bytes=0, *, target=80.0, cap=8):
    if state is None:
        return Budget(1, 1, monitored=False, target_cpu=target)
    reserve = max(2 * GIB, int(state.total * 0.15))
    # Leave space for each independently opened decoder and the encoder.
    remaining = state.available - reserve - decoder_bytes - 512 * MIB
    if remaining < 256 * MIB:
        raise RuntimeError(
            f"渲染内存不足：可用 {state.available / GIB:.1f} GiB，"
            f"保留 {reserve / GIB:.1f} GiB，解码估算 {decoder_bytes / GIB:.1f} GiB")
    cpu_threads = max(1, math.floor(logical_cpus * (target - state.cpu_percent) / 100))
    threads = max(1, min(cap, max(1, logical_cpus - 2), cpu_threads,
                         int(remaining // (256 * MIB))))
    return Budget(threads, min(2, threads), reserve_bytes=reserve, target_cpu=target)


def admit_render(decoder_bytes=0):
    target, cap = config()
    state = snapshot(0.3)
    logical = os.cpu_count() or 1
    if psutil is not None:
        # Honour inherited cpusets/affinity, rather than assuming every CPU is available.
        try:
            logical = len(psutil.Process().cpu_affinity())
        except (AttributeError, psutil.Error, OSError):
            pass
    deadline = time.monotonic() + 30
    while True:
        try:
            return choose_budget(state, logical, decoder_bytes, target=target, cap=cap), state
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise
            print("[render-resources] 等待可用内存释放", file=sys.stderr, flush=True)
            time.sleep(2)
            state = snapshot(0.3)


class Governor:
    """Decrease quickly; increase only after three healthy samples (hysteresis)."""

    def __init__(self, budget, active_cpus):
        self.budget = budget
        self.active = active_cpus
        self.maximum = active_cpus
        self.healthy = 0
        self.critical = 0

    def update(self, state):
        critical = state.available < max(512 * MIB, int(state.total * 0.03))
        self.critical = self.critical + 1 if critical else 0
        if self.critical >= 3:
            raise RuntimeError("渲染期间系统可用内存持续过低，已停止渲染；请释放内存后重试")
        pressured = (state.cpu_percent > self.budget.target_cpu + 5 or
                     state.available < self.budget.reserve_bytes)
        healthy = (state.cpu_percent < self.budget.target_cpu - 15 and
                   state.available > self.budget.reserve_bytes + 512 * MIB)
        self.healthy = self.healthy + 1 if healthy else 0
        if pressured:
            self.active = max(1, self.active - 1)
        elif self.healthy >= 3:
            self.active = min(self.maximum, self.active + 1)
            self.healthy = 0
        return self.active


def run_render(command, budget, initial_state, output):
    """Drain pipes while sampling, and never leave FFmpeg running after an error."""
    journal_path = output + ".resources.jsonl"
    with open(journal_path, "w", encoding="utf-8") as journal:
        def record(event, state=None, **extra):
            entry = {"event": event, "time": time.time(), **extra}
            if state is not None:
                entry.update(asdict(state))
                entry["memory_percent"] = round(state.memory_percent, 1)
            journal.write(json.dumps(entry, ensure_ascii=False) + "\n")
            journal.flush()

        record("budget", initial_state, **asdict(budget))
        print(f"[render-resources] 编码={budget.encoder_threads}，"
              f"每路解码={budget.decoder_threads}，滤镜={budget.filter_threads}；"
              f"监控日志：{journal_path}", file=sys.stderr, flush=True)
        if not budget.monitored:
            print("[render-resources] 缺少 psutil，保守使用单线程；请安装项目依赖以启用监控",
                  file=sys.stderr, flush=True)
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8", errors="replace")
        controlled = None
        cpus = []
        if psutil is not None:
            try:
                controlled = psutil.Process(process.pid)
                cpus = controlled.cpu_affinity()
                cpus = cpus[:budget.encoder_threads]
                controlled.cpu_affinity(cpus)
            except (AttributeError, psutil.Error, OSError) as exc:
                record("affinity_unavailable", reason=str(exc))
                controlled = None
        governor = Governor(budget, len(cpus) or budget.encoder_threads)
        try:
            while True:
                try:
                    stdout, stderr = process.communicate(timeout=SAMPLE_SECONDS)
                    break
                except subprocess.TimeoutExpired:
                    state = snapshot()
                    if state is None:
                        continue
                    previous = governor.active
                    active = governor.update(state)
                    if controlled is not None and active != previous:
                        try:
                            controlled.cpu_affinity(cpus[:active])
                            print(f"[render-resources] 可运行 CPU 核心数 {previous} → {active}",
                                  file=sys.stderr, flush=True)
                        except (psutil.Error, OSError) as exc:
                            record("affinity_unavailable", reason=str(exc))
                            controlled = None
                    record("sample", state, active_cpus=active if controlled else None)
            record("finished", returncode=process.returncode)
            if process.returncode:
                raise RuntimeError(stderr[-4000:])
            return stdout
        except BaseException as exc:
            if process.poll() is None:
                process.terminate()
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
            record("aborted", reason=str(exc))
            raise
