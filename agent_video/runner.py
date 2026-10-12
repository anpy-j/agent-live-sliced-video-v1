# -*- coding: utf-8 -*-
"""任务执行器：按任务固定版本分派 V1、V2、V3 剪辑流程。

``JobRunner`` 只做三件事：把任务放进串行队列、执行精简管线、把每个阶段的进度、
错误与产物写回 ``Store``。编排、规则筛、AI 判定/排序、渲染原语全在
各版本的 pipeline 模块内；这里不添加 AI 降级链、局部重编或兜底。
"""
from __future__ import annotations

import os
import multiprocessing
import json
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .db import Store, utc_now, STAGE_DEFINITIONS
from .pipeline_versions import stages_for
from .pipeline import (PipelineError, next_available_dir, next_available_output,
                       group_segments_for_export, render_segment, run_pipeline,
                       run_pipeline_stage)
from .pipeline.remix import run_remix_pipeline

HEARTBEAT_SECONDS = 5.0

# 精简管线通过环境变量选择 AI 引擎/提供方/模型；这些键与 DB 设置一一对应。
_AI_ENV_KEYS = {
    "ai_engine": "PIPELINE_AI_ENGINE",
    "ai_provider": "PIPELINE_AI_PROVIDER",
    "ai_model": "PIPELINE_AI_MODEL",
    "api_base_url": "LIVE_CUT_API_BASE_URL",
    "judge_concurrency": "PIPELINE_JUDGE_CONCURRENCY",
    "judge_retries": "PIPELINE_JUDGE_RETRIES",
    "jev_api_key": "TYPESAFE_API_KEY",
    "jev_base_url": "TYPESAFE_BASE_URL",
}

# 生产默认值：S3 批次 4 并发 + 单批失败重试 1 次（依据 LARI-18 对比实验：4 并发约 2.9×
# 提速，8 并发边际收益小且内存翻倍）。管线代码自身默认仍为串行(1)，未设 DB 设置时
# 由这里注入生产默认，仍可用 DB 设置或环境变量回退成 1。
_AI_ENV_DEFAULTS = {
    "PIPELINE_JUDGE_CONCURRENCY": "4",
    "PIPELINE_JUDGE_RETRIES": "1",
}


class JobPaused(RuntimeError):
    def __init__(self, stage):
        self.stage = stage


class JobCancelled(RuntimeError):
    """用户显式取消，不应被记成失败。"""


def refresh_timeline_media_paths(meta: dict[str, Any], cache_path: Path) -> dict[str, Any]:
    """Refresh relocated media without changing a job's frozen edit decisions."""
    segments = meta.get("segments") or []
    if not segments or all(Path(s["source_path"]).is_file() for s in segments):
        return meta
    selected = meta.get("draft_path")
    if not selected:
        # Older jobs did not retain their selected draft file. The discovery
        # cache can locate that exact timeline; never use the active timeline.
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return meta
        paths = {t["path"] for entry in cache.values() for t in entry.get("timelines", [])
                 if t.get("timeline_id") == meta.get("timeline_id") and t.get("path")}
        if len(paths) != 1:
            return meta
        selected = paths.pop()
    from .timeline import load_virtual_timeline
    current = load_virtual_timeline(selected)
    if current.timeline_id != meta.get("timeline_id"):
        raise ValueError("所选草稿时间线已变化，请重新创建任务")
    fields = ("segment_id", "timeline_start", "timeline_end", "source_start", "source_end", "speed")
    fresh = current.to_dict()["segments"]
    if len(fresh) != len(segments) or any(
            any(old.get(key, 1.0 if key == "speed" else None) != new[key] for key in fields)
            for old, new in zip(segments, fresh)):
        raise ValueError("草稿剪辑范围或倍速已变化，不能仅更新素材路径，请重新创建任务")
    updated = json.loads(json.dumps(meta))
    for old, new in zip(updated["segments"], fresh):
        if not Path(old["source_path"]).is_file() and Path(new["source_path"]).is_file():
            old["source_path"] = new["source_path"]
    updated["draft_path"] = str(selected)
    return updated


def _windows_kill_on_close_job() -> Any:
    """把当前任务进程及未来后代放入随句柄关闭而终止的 Windows Job。"""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        return None
    info = ExtendedLimitInformation()
    info.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(
            handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
        kernel32.CloseHandle(handle)
        return None
    if not kernel32.AssignProcessToJobObject(handle, kernel32.GetCurrentProcess()):
        kernel32.CloseHandle(handle)
        return None
    return handle


def _run_pipeline_child(result_queue: Any, source: str, workspace: str,
                        target_seconds: tuple[float, float],
                        only_stage: str | None = None,
                        output_stem: str | None = None,
                        product_name: str | None = None, pause_event: Any = None,
                        pipeline_version: str = "v1") -> None:
    """在可强制终止的独立进程中执行重计算管线。"""
    job_handle = _windows_kill_on_close_job()
    if os.name != "nt":
        os.setsid()

    s1_key = None

    def on_stage(stage: str, status: str, message: str) -> None:
        if status == "start" and pause_event is not None and pause_event.is_set():
            raise JobPaused(stage)
        if stage == "asr" and status == "done" and s1_key is not None:
            from .s1_cache import save
            save(Path(workspace), s1_key)
        result_queue.put(("stage", stage, status, message))

    try:
        vt = None
        vt_file = Path(workspace) / "virtual_timeline.json"
        if vt_file.is_file():
            from .timeline import load_virtual_timeline
            vt = load_virtual_timeline(vt_file)

        remix_config_file = Path(workspace) / "remix_config.json"
        if vt is not None and only_stage is None and not remix_config_file.is_file():
            from .s1_cache import prepare
            on_stage("asr", "start", "检查时间线和素材文件，查找可复用的 S1")
            s1_key, _ = prepare(Path(workspace), vt, pipeline_version)
        if pipeline_version != "v1" and remix_config_file.is_file():
            raise PipelineError("成片重组仅支持 V1 流程")
        if remix_config_file.is_file():
            if vt is None:
                raise PipelineError("成片重组任务缺少虚拟时间线")
            try:
                remix_config = json.loads(remix_config_file.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise PipelineError(f"成片重组配置无法读取：{exc}") from exc
            manifest = run_remix_pipeline(
                source, workspace, virtual_timeline=vt, output_stem=output_stem,
                dedupe_strength=str(remix_config.get("dedupe_strength") or "standard"),
                on_stage=on_stage)
        elif pipeline_version == "v2":
            from .pipeline_v2.run import run_pipeline as run_v2
            manifest = run_v2(source, workspace, only_stage=only_stage,
                              target_seconds=target_seconds, product_name=product_name,
                              output_stem=output_stem, on_stage=on_stage, virtual_timeline=vt)
        elif pipeline_version == "v3":
            from .pipeline_v3.run import run_pipeline as run_v3, run_pipeline_stage as stage_v3
            kwargs = dict(target_seconds=target_seconds, product_name=product_name,
                          output_stem=output_stem, on_stage=on_stage, virtual_timeline=vt)
            manifest = (stage_v3(source, workspace, only_stage, **kwargs) if only_stage
                        else run_v3(source, workspace, **kwargs))
            manifest["pipeline_version"] = "v3"
        elif pipeline_version != "v1":
            raise PipelineError(f"未知剪辑版本：{pipeline_version}")
        elif only_stage:
            manifest = run_pipeline_stage(
                source, workspace, only_stage, target_seconds=target_seconds,
                product_name=product_name, output_stem=output_stem, on_stage=on_stage,
                virtual_timeline=vt)
        else:
            manifest = run_pipeline(source, workspace, target_seconds=target_seconds,
                                    output_stem=output_stem, product_name=product_name,
                                    on_stage=on_stage, virtual_timeline=vt)
    except JobPaused as exc:
        result_queue.put(("paused", exc.stage))
    except PipelineError as exc:
        result_queue.put(("pipeline_error", str(exc)))
    except BaseException as exc:  # noqa: BLE001 - 跨进程回传未预期错误
        result_queue.put(("error", f"{type(exc).__name__}: {exc}"))
    else:
        result_queue.put(("success", manifest))
    finally:
        # 变量必须活到管线结束；任务被强制终止时 OS 会关闭句柄并清理所有后代。
        _ = job_handle


class JobRunner:
    """串行执行精简管线的本地工作进程。"""

    def __init__(self, store: Store, project_root: Path, *, vault=None):
        self.store = store
        self.vault = vault
        self.project_root = Path(project_root)
        self._queue: list[str] = []
        self._queued: set[str] = set()
        self._cancelled: set[str] = set()
        self._pause_requested: set[str] = set()
        self._pause_event = None
        self.control_lock = threading.RLock()
        self._active_stage: dict[str, str] = {}
        self._current: str | None = None
        self._condition = threading.Condition()
        self._thread: threading.Thread | None = None
        self._process: multiprocessing.Process | None = None
        self._running = False

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        for job in self.store.list_jobs(limit=10000):
            if job['status'] == 'pausing':
                self.store.update_job(job['id'], status='paused', run_stage=job.get('current_stage'))
        for job in self.store.list_recoverable_jobs():
            if job.get("run_stage"):
                self.store.prepare_stage_rerun(job["id"], job["run_stage"])
            else:
                self.store.reset_job(job["id"])
            self._enqueue(job["id"], event=False)
        self._thread = threading.Thread(target=self._loop, name="slice-agent-worker",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._condition:
            self._running = False
            self._condition.notify_all()
        self._terminate_active_process()

    def enqueue(self, job_id: str) -> None:
        self._enqueue(job_id, event=True)

    def _enqueue(self, job_id: str, *, event: bool, prepend: bool = False) -> None:
        with self._condition:
            if prepend:
                if job_id in self._queue:
                    self._queue.remove(job_id)
                self._queue.insert(0, job_id)
                self._queued.add(job_id)
            else:
                if job_id in self._queued:
                    return
                self._queued.add(job_id)
                self._queue.append(job_id)
            self.store.update_job(job_id, status="queued", error=None, finished_at=None)
            self._condition.notify_all()
        if event:
            self.store.add_event(job_id, None, "info", "queued", "任务已加入执行队列")

    def pause(self, job_id: str) -> None:
        with self._condition:
            job = self.store.get_job(job_id)
            if not job or job['status'] not in {'queued', 'running', 'pausing', 'paused'}:
                raise ValueError('仅排队或运行中的任务可以暂停')
            if job['status'] in {'paused', 'pausing'}:
                return
            self._pause_requested.add(job_id)
            if job_id in self._queued:
                self._queued.discard(job_id)
                self._queue.remove(job_id)
                self.store.update_job(job_id, status='paused')
            else:
                self.store.update_job(job_id, status='pausing')
                if self._current == job_id and self._pause_event is not None:
                    self._pause_event.set()
            self.store.add_event(job_id, None, 'info', 'pause_requested', '请求暂停：当前步骤完成后停止')

    def resume(self, job_id: str) -> None:
        job = self.store.get_job(job_id)
        if not job or job['status'] != 'paused':
            raise ValueError('仅已暂停任务可以继续')
        with self._condition:
            if self._current == job_id:
                self._condition.wait_for(lambda: self._current != job_id, timeout=5)
                if self._current == job_id:
                    raise ValueError('任务仍在保存暂停状态，请稍后继续')
        self._pause_requested.discard(job_id)
        stage = job.get('run_stage')
        resume_stages = {'filter', 'judge', 'order', 'render'}
        if job.get('pipeline_version') == 'v2':
            resume_stages |= {'asr', 'review'}
        if stage in resume_stages and job.get('job_type') != 'remix':
            self.store.prepare_stage_rerun(job_id, stage)
        else:
            self.store.update_job(job_id, run_stage=None)
        self.enqueue(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self.store.get_job(job_id)
        if not job:
            return False
        if job["status"] not in {"queued", "running", "pausing", "paused"}:
            return False
        self._pause_requested.discard(job_id)
        self._cancelled.add(job_id)
        with self._condition:
            if job_id in self._queued:
                self._queued.discard(job_id)
                if job_id in self._queue:
                    self._queue.remove(job_id)
                self._condition.notify_all()
        if self._current == job_id:
            self._terminate_active_process(job_id)
        stage_id = job.get("current_stage")
        if stage_id:
            self.store.update_stage(job_id, stage_id, status="cancelled", progress=0,
                                    message="任务已取消", finished_at=utc_now(), error=None)
        self.store.update_job(job_id, status="cancelled", finished_at=utc_now())
        self.store.add_event(job_id, stage_id, "warning", "cancelled", "任务已取消")
        return True

    def _refresh_ai_configuration(self, job_id: str, reason: str) -> dict[str, str]:
        """Start a new manual attempt with current settings, preserving its audit trail."""
        job = self.store.get_job(job_id)
        if not job:
            raise KeyError("任务不存在")
        defaults = {"ai_engine": "llm", "ai_provider": "auto", "ai_model": "auto"}
        configuration = {key: str(self.store.get_setting(key) or default).strip() or default
                         for key, default in defaults.items()}
        try:
            saved = json.loads(job.get("remote_model_json") or "{}")
            previous = {key: saved.get(key) for key in defaults} if isinstance(saved, dict) else {}
        except (TypeError, ValueError):
            previous = {}
        self.store.update_job(job_id, remote_model_json=json.dumps(configuration))
        if previous != configuration:
            self.store.add_event(
                job_id, None, "info", "ai_configuration_updated",
                f"{reason}：采用 {configuration['ai_provider']} / {configuration['ai_model']}",
                {"previous": previous, "current": configuration})
        return configuration

    def restart(self, job_id: str, clean: bool = True) -> None:
        job = self.store.get_job(job_id)
        if not job:
            raise KeyError("任务不存在")
        timeline_meta = job.get("timeline_meta")
        if isinstance(timeline_meta, dict):
            refreshed = refresh_timeline_media_paths(timeline_meta, self.store.path.parent / "jianying_draft_cache.json")
            if refreshed != timeline_meta:
                if refreshed.get("segments") != timeline_meta.get("segments"):
                    clean = True  # ASR clauses and render mappings still contain the old paths.
                self.store.update_job(job_id, timeline_meta_json=json.dumps(refreshed, ensure_ascii=False))
                job["timeline_meta"] = refreshed
                self.store.add_event(job_id, None, "info", "media_paths_refreshed", "已根据所选剪映时间线更新素材路径")
        if job["status"] in {"queued", "running"}:
            self.cancel(job_id)
        self._cancelled.discard(job_id)
        workspace = Path(job.get("workspace") or "")
        if workspace and workspace.exists():
            if clean:
                shutil.rmtree(workspace, ignore_errors=True)
                workspace.mkdir(parents=True, exist_ok=True)
            else:
                preserved = {"clauses.json", "timeline.json", "s1-cache.json", "audio16k.wav",
                             "audio16k.wav.manifest.json", "deliverables"}
                if job.get("pipeline_version") == "v2":
                    preserved |= {"v2", "clauses.filtered.json"}
                for item in workspace.iterdir():
                    if item.name not in preserved:
                        if item.is_dir():
                            shutil.rmtree(item, ignore_errors=True)
                        else:
                            item.unlink(missing_ok=True)
        elif workspace:
            workspace.mkdir(parents=True, exist_ok=True)
        timeline_meta = job.get("timeline_meta")
        if workspace and isinstance(timeline_meta, dict) and timeline_meta.get("segments"):
            (workspace / "virtual_timeline.json").write_text(
                json.dumps(timeline_meta, ensure_ascii=False, indent=2), encoding="utf-8")
            if job.get("job_type") == "remix":
                remix_meta = timeline_meta.get("remix") or {}
                (workspace / "remix_config.json").write_text(json.dumps({
                    "version": 1,
                    "dedupe_strength": remix_meta.get("dedupe_strength") or "standard",
                    "text_only": True,
                    "check_product": False,
                    "check_compliance": False,
                    "dedupe_visual": False,
                }, ensure_ascii=False, indent=2), encoding="utf-8")
        self.store.reset_job(job_id)
        self._refresh_ai_configuration(job_id, "手动重试或重启任务")
        self.enqueue(job_id)

    def retry(self, job_id: str) -> None:
        """重新开始：保留已有 ASR/子句切分等确定性产物，从后续失败步骤重试。"""
        job = self.store.get_job(job_id)
        if job and job.get("pipeline_version") == "v2" and job.get("current_stage") in {"filter", "judge", "order", "review", "render"}:
            self.rerun_stage(job_id, job["current_stage"])
            return
        self.restart(job_id, clean=False)

    def rerun_stage(self, job_id: str, stage_id: str) -> None:
        """重跑 S2/S3/S4/S6；保留上游输入并清空会失效的下游节点数据。

        重跑选中节点后会**自动继续执行其下游节点直到出片**（见 ``_succeed_stage``），
        所以外层只需提交一次。
        """
        job = self.store.get_job(job_id)
        if not job:
            raise KeyError("任务不存在")
        is_v2 = job.get("pipeline_version") == "v2"
        if stage_id not in ({"filter", "judge", "order", "review", "render"} if is_v2 else
                            {"filter", "judge", "order", "render"}):
            raise ValueError("仅支持重新执行 S2、S3、S4 或渲染")
        if job["status"] in {"queued", "running"}:
            self.cancel(job_id)
            with self._condition:
                self._condition.wait_for(lambda: self._current != job_id, timeout=5.0)
        self._cancelled.discard(job_id)
        workspace = Path(job.get("workspace") or "")
        if is_v2:
            from .pipeline_v2.run import invalidate_stage, required_for_stage
            required_path = required_for_stage(workspace, stage_id)
            if not required_path.is_file():
                raise ValueError("V2 上游数据不存在，不能重跑该节点")
            self._refresh_ai_configuration(job_id, "手动重新执行节点")
            invalidate_stage(workspace, stage_id)
            self.store.prepare_stage_rerun(job_id, stage_id)
            self._enqueue(job_id, event=False)
            return
        required = {
            "filter": ("clauses.json", "S1 语音转写与切分"),
            "judge": ("clauses.filtered.json", "S2 规则粗筛"),
            "order": ("clauses.judged.json", "S3 AI 可用性判定"),
            "render": ("order.json", "S4 AI 排序编排"),
        }
        filename, label = required[stage_id]
        if not (workspace / filename).is_file():
            raise ValueError(f"{label}数据不存在，不能单独重跑该节点")
        self._refresh_ai_configuration(job_id, "手动重新执行节点")
        for name in {
            "filter": ("clauses.filtered.json", "clauses.judged.json", "judge.progress.json", "order.json"),
            "judge": ("clauses.judged.json", "judge.progress.json", "order.json"),
            "order": ("order.json",),
            "render": (),
        }[stage_id]:
            (workspace / name).unlink(missing_ok=True)
        if stage_id in {"judge", "order"}:
            timeline_path = workspace / "timeline.json"
            if timeline_path.is_file():
                try:
                    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
                    for clause in timeline.get("clauses") or []:
                        clause.pop("order", None)
                    timeline_path.write_text(
                        json.dumps(timeline, ensure_ascii=False, indent=1), encoding="utf-8")
                except (OSError, ValueError, AttributeError):
                    pass
        self.store.prepare_stage_rerun(job_id, stage_id)
        self._enqueue(job_id, event=False)

    def delete(self, job_id: str) -> bool:
        job = self.store.get_job(job_id)
        if not job:
            return False
        if job["status"] in {"queued", "running"}:
            self.cancel(job_id)
        with self._condition:
            self._queued.discard(job_id)
            if job_id in self._queue:
                self._queue.remove(job_id)
        self._cancelled.discard(job_id)
        workspace = Path(job.get("workspace") or "")
        if workspace:
            shutil.rmtree(workspace, ignore_errors=True)
        return self.store.delete_job(job_id)

    def runtime(self, job_id: str) -> dict[str, Any]:
        active = self._current == job_id
        return {
            "worker_alive": bool(self._thread and self._thread.is_alive()),
            "process_active": active,
            "process_id": self._process.pid if active and self._process else None,
            "queued": job_id in self._queued,
            "stage": self._active_stage.get(job_id),
        }

    # ------------------------------------------------------------------ worker
    def _loop(self) -> None:
        while True:
            with self._condition:
                while self._running and not self._queue:
                    self._condition.wait(timeout=1.0)
                if not self._running:
                    return
                job_id = self._queue.pop(0)
                self._queued.discard(job_id)
            job = self.store.get_job(job_id)
            if not job:
                continue
            if job_id in self._cancelled:
                self._cancelled.discard(job_id)
                continue
            self._current = job_id
            if self.store.get_job(job_id)["status"] == "paused":
                self._current = None
                continue
            try:
                self._run(job)
            finally:
                self._current = None
                self._active_stage.pop(job_id, None)
                with self._condition:
                    self._condition.notify_all()

    @staticmethod
    def _parse_target_seconds(value: Any) -> tuple[float, float]:
        parts = [p.strip() for p in re.split(r"[-–—~,:]", str(value or "").strip())]
        if len(parts) == 2:
            try:
                low, high = float(parts[0]), float(parts[1])
                if low > 0 and high >= low:
                    return low, high
            except (ValueError, TypeError):
                pass
        return (70.0, 90.0)

    def _run(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        if not job.get("remote_model_json"):
            configuration = self._refresh_ai_configuration(job_id, "首次执行")
            job["remote_model_json"] = json.dumps(configuration)
        source = Path(job["source_path"])
        workspace = Path(job["workspace"])
        workspace.mkdir(parents=True, exist_ok=True)
        heartbeat = self._start_heartbeat(job_id)
        target_seconds = self._parse_target_seconds(job.get("target_seconds"))
        # 成片重组的语义去重、分类和顺序是一个整体决策；局部重跑会让后续结果失配。
        # 即使历史 UI 留下 run_stage，也按完整重组流程执行一次。
        only_stage = None if job.get("job_type") == "remix" else job.get("run_stage")

        def on_stage(stage: str, status: str, message: str) -> None:
            if job_id in self._cancelled:
                raise JobCancelled()
            self._active_stage[job_id] = stage
            if status == "start":
                self.store.stage_start(job_id, stage, message)
                if stage == "render":
                    self.store.update_stage(job_id, stage, progress=0, result=None)
                if job_id in self._pause_requested:
                    self.store.update_job(job_id, status="pausing")
            elif stage == "render":
                # Rendering the preview is followed by split exports and delivery.
                # Keep the node running until those outputs have also finished.
                self._sync_render_progress(job_id, workspace)
                self.store.update_stage(job_id, stage, status="running", progress=0.99,
                                        message="编码结束，正在封装和交付成片", finished_at=None)
            else:
                self.store.stage_done(job_id, stage, message)

        output_stem = str(job.get("title") or "").strip() or None
        product_name = str(job.get("product_name") or "").strip() or None
        try:
            with self._ai_environment(job):
                manifest = self._execute_pipeline(
                    job_id, str(source), str(workspace), target_seconds, on_stage,
                    only_stage=only_stage, output_stem=output_stem,
                    product_name=product_name)
        except JobPaused as exc:
            self.store.update_job(job_id, status="paused", run_stage=exc.stage)
            self.store.add_event(job_id, exc.stage, "info", "paused", "已暂停，当前步骤结果已保存")
        except JobCancelled:
            if job_id in self._pause_requested and not self._running:
                self.store.update_job(job_id, status="paused", run_stage=self._active_stage.get(job_id))
            elif self.store.get_job(job_id):
                self._finish(job_id, "cancelled", "任务已取消")
        except PipelineError as exc:
            stage = self._active_stage.get(job_id, "asr")
            self._fail(job_id, stage, str(exc))
        except Exception as exc:  # noqa: BLE001 - 未预期错误也按失败停止，不兜底
            stage = self._active_stage.get(job_id, "asr")
            self._fail(job_id, stage, f"未预期错误：{exc}")
        else:
            if only_stage:
                self._succeed_stage(job_id, workspace, only_stage, manifest, job)
            else:
                self._succeed(job_id, workspace, manifest, export_dir=job.get("export_dir"),
                              export_mode=str(job.get("export_mode") or "merge"),
                              output_stem=output_stem, media=str(source))
        finally:
            heartbeat.set()

    def _execute_pipeline(self, job_id: str, source: str, workspace: str,
                          target_seconds: tuple[float, float],
                          on_stage: Any, *, only_stage: str | None = None,
                          output_stem: str | None = None,
                          product_name: str | None = None) -> dict[str, Any]:
        """执行子进程并把阶段事件同步回主服务；取消时终止整棵进程树。"""
        context = multiprocessing.get_context("spawn")
        result_queue = context.Queue()
        self._pause_event = context.Event()
        if job_id in self._pause_requested:
            self._pause_event.set()
        process = context.Process(
            target=_run_pipeline_child,
            args=(result_queue, source, workspace, target_seconds, only_stage,
                  output_stem, product_name, self._pause_event,
                  (self.store.get_job(job_id) or {}).get("pipeline_version", "v1")),
            name=f"pipeline-{job_id}",
        )
        for filename in ("render.progress.json", "render.progress.json.ffmpeg"):
            (Path(workspace) / filename).unlink(missing_ok=True)
        process.start()
        self._process = process
        terminal: tuple[Any, ...] | None = None
        try:
            while terminal is None:
                if job_id in self._cancelled or not self._running:
                    self._terminate_process_tree(process)
                    raise JobCancelled()
                try:
                    message = result_queue.get(timeout=0.2)
                except queue.Empty:
                    if self._active_stage.get(job_id) == "render":
                        self._sync_render_progress(job_id, Path(workspace))
                    if not process.is_alive():
                        # 给 Queue 的后台发送线程一个短暂的收尾机会。
                        try:
                            message = result_queue.get(timeout=0.5)
                        except queue.Empty:
                            raise RuntimeError(
                                f"管线子进程异常退出（exitcode={process.exitcode}）")
                    else:
                        continue
                if message[0] == "stage":
                    _, stage, status, text = message
                    on_stage(stage, status, text)
                else:
                    terminal = message
            process.join(timeout=2)
            if terminal[0] == "success":
                return terminal[1]
            if terminal[0] == "paused":
                raise JobPaused(terminal[1])
            if terminal[0] == "pipeline_error":
                raise PipelineError(terminal[1])
            raise RuntimeError(terminal[1])
        finally:
            if process.is_alive():
                self._terminate_process_tree(process)
            process.join(timeout=2)
            result_queue.close()
            self._process = None
            self._pause_event = None

    def _terminate_active_process(self, job_id: str | None = None) -> None:
        process = self._process
        if process and process.is_alive() and (job_id is None or self._current == job_id):
            self._terminate_process_tree(process)

    @staticmethod
    def _terminate_process_tree(process: multiprocessing.Process) -> None:
        """终止任务进程及其 ffmpeg/AI CLI 等后代，不留下幽灵计算。"""
        if not process.pid or not process.is_alive():
            return
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
                check=False,
            )
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
        process.join(timeout=3)
        if process.is_alive():
            process.kill()

    @contextmanager
    def _ai_environment(self, job: dict[str, Any] | None = None) -> Iterator[None]:
        """把 DB 中的 AI 设置注入管线所需的环境变量，跑完恢复原值。

        未配置 DB 设置时，用 ``_AI_ENV_DEFAULTS`` 注入生产默认（S3 并发/重试）；DB
        设置或调用方已有的环境变量优先，仍可回退成串行。
        """
        managed = set(_AI_ENV_KEYS.values()) | set(_AI_ENV_DEFAULTS) | {"LIVE_CUT_API_KEY"}
        previous = {key: os.environ.get(key) for key in managed}
        override = json.loads((job or {}).get("remote_model_json") or "{}")
        for setting_key, env_key in _AI_ENV_KEYS.items():
            value = override.get(setting_key, self.store.get_setting(setting_key))
            if value:
                os.environ[env_key] = str(value)
        if self.vault and os.environ.get('PIPELINE_AI_PROVIDER') == 'api':
            os.environ['LIVE_CUT_API_KEY'] = self.vault.get('model-api-key')
        for env_key, default in _AI_ENV_DEFAULTS.items():
            if not os.environ.get(env_key):
                os.environ[env_key] = default
        try:
            yield
        finally:
            for env_key, old in previous.items():
                if old is None:
                    os.environ.pop(env_key, None)
                else:
                    os.environ[env_key] = old

    def _start_heartbeat(self, job_id: str) -> threading.Event:
        stop = threading.Event()

        def beat() -> None:
            while not stop.wait(HEARTBEAT_SECONDS):
                self.store.touch_job(job_id)

        threading.Thread(target=beat, name=f"heartbeat-{job_id}", daemon=True).start()
        return stop

    @staticmethod
    def _export_video(source: Path, export_dir: str, stem: str | None = None) -> Path:
        folder = Path(export_dir).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        target = Path(next_available_output(str(folder), stem or source.stem))
        shutil.copy2(source, target)
        return target

    def _set_render_progress(self, job_id: str, completed: int, total: int,
                             phase: str = "编码片段") -> None:
        if total <= 0:
            return
        completed = max(0, min(completed, total))
        percent = min(99, completed * 100 // total)
        message = f"{phase} {completed}/{total} · {percent}%"
        job = self.store.get_job(job_id)
        if not job:
            return
        stage = next((s for s in job["stages"] if s["stage_id"] == "render"), {})
        result = {"completed": completed, "total": total, "phase": phase}
        if stage.get("result") == result and stage.get("status") == "running":
            return
        self.store.update_stage(job_id, "render", status="running", progress=percent / 100,
                                message=message, result=result, finished_at=None)
        definitions = stages_for(job.get("pipeline_version", "v1"), STAGE_DEFINITIONS)
        before_render = max(position for sid, _, position in definitions if sid != "render")
        self.store.update_job(job_id, current_stage="render",
                              progress=before_render + (100 - before_render) * percent / 100)

    def _sync_render_progress(self, job_id: str, workspace: Path) -> None:
        try:
            snapshot = json.loads((workspace / "render.progress.json").read_text(encoding="utf-8"))
            with (workspace / "render.progress.json.ffmpeg").open("rb") as handle:
                handle.seek(0, 2)
                handle.seek(max(0, handle.tell() - 16384))
                values = re.findall(r"(?:^|\n)frame=(\d+)\r?\n", handle.read().decode("utf-8", errors="replace"))
            frames = int(values[-1]) if values else 0
            completed = sum(frames >= boundary for boundary in snapshot["frame_ends"])
            self._set_render_progress(job_id, completed, snapshot["total"])
        except (OSError, ValueError, KeyError, TypeError):
            # A missing/incomplete progress write is not a completed render.
            return

    def _export_outputs(self, workspace: Path, manifest: dict[str, Any], export_dir: str,
                        export_mode: str, output_stem: str | None,
                        media: str | None, *, job_id: str | None = None) -> list[Path]:
        folder = Path(export_dir).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        if export_mode == "segments":
            segments = manifest.get("segments") or []
            if not segments:
                raise RuntimeError("没有可导出的分段")
            sub = Path(next_available_dir(str(folder), output_stem or "segments"))
            sub.mkdir(parents=True, exist_ok=True)
            source = media or str(manifest.get("source") or "")
            exported: list[Path] = []
            group_fn, render_fn = group_segments_for_export, render_segment
            if manifest.get("pipeline_version") in {"v2", "v3"}:
                from .pipeline_v3.render import group_segments_for_export as group_fn, render_segment as render_fn
            segment_groups = group_fn(segments)
            if job_id:
                self._set_render_progress(job_id, 0, len(segment_groups), "导出片段")
            for index, segment_group in enumerate(segment_groups, start=1):
                target = Path(next_available_output(str(sub), f"{index:02d}"))
                render_fn(source, segment_group, str(target), str(workspace), index=index)
                exported.append(target)
                if job_id:
                    self._set_render_progress(job_id, index, len(segment_groups), "导出片段")
            return exported
        rendered = workspace / str(manifest.get("output") or "")
        if not rendered.is_file():
            raise RuntimeError(f"成片不存在：{rendered}")
        return [self._export_video(rendered, export_dir, stem=output_stem)]

    def _succeed(self, job_id: str, workspace: Path, manifest: dict[str, Any],
                 export_dir: str | None = None, export_mode: str = "merge",
                 output_stem: str | None = None, media: str | None = None) -> None:
        if manifest.get("workflow") == "remix":
            artifacts = [
                ("asr", "json", "集合片段文字", workspace / "timeline.json", "application/json"),
                ("filter", "json", "文字字面去重", workspace / "clauses.filtered.json", "application/json"),
                ("judge", "json", "语义去重结果", workspace / "clauses.judged.json", "application/json"),
                ("order", "json", "成片重组方案", workspace / "remix_plan.json", "application/json"),
                ("render", "json", "渲染清单", workspace / "manifest.json", "application/json"),
            ]
        else:
            artifacts = [
                ("asr", "json", "子句时间线", workspace / "timeline.json", "application/json"),
                ("filter", "json", "规则筛结果", workspace / "clauses.filtered.json", "application/json"),
                ("judge", "json", "AI 判定结果", workspace / "clauses.judged.json", "application/json"),
                ("render", "json", "渲染清单", workspace / "manifest.json", "application/json"),
            ]
        for stage_id, kind, title, path, mime in artifacts:
            if path.is_file():
                self.store.add_artifact(job_id, stage_id, kind, title, path, mime)
        if manifest.get("pipeline_version") == "v2":
            for sid, filename, title in (
                ("judge", "candidates.json", "V2 完整表达候选"),
                ("order", "order.json", "V2 成片编排"),
                ("review", "review.json", "V2 成片验收"),
                ("review", "成片文本.txt", "V2 成片文本"),
                ("review", "usage.json", "V2 调用用量"),
            ):
                path = workspace / "v2" / filename
                if path.is_file():
                    self.store.add_artifact(job_id, sid, "text" if filename.endswith(".txt") else "json",
                                            title, path, "text/plain" if filename.endswith(".txt") else "application/json")
        videos = sorted((workspace / "deliverables").glob("*.mp4"),
                        key=lambda path: path.stat().st_mtime_ns, reverse=True)
        for video in videos:
            self.store.add_artifact(job_id, "render", "video", f"成片 · {video.name}",
                                    video, "video/mp4")
        if export_dir:
            try:
                exported = self._export_outputs(workspace, manifest, export_dir,
                                                export_mode, output_stem, media, job_id=job_id)
            except Exception as exc:  # noqa: BLE001 - 未完成交付不能标为完成
                self._fail(job_id, "render", f"导出到自定义位置失败：{exc}")
                return
            else:
                for path in exported:
                    self.store.add_artifact(job_id, "render", "video",
                                            f"成片（导出）· {path.name}", path, "video/mp4")
                self.store.add_event(job_id, "render", "success", "exported",
                                     f"已导出 {len(exported)} 个文件到 {export_dir}")
        render_stage = next((s for s in (self.store.get_job(job_id) or {}).get("stages", [])
                             if s["stage_id"] == "render"), {})
        render_result = render_stage.get("result") or {}
        total = render_result.get("total")
        if total:
            render_result = {"completed": total, "total": total, "phase": "完成"}
        self.store.stage_done(job_id, "render", f"成片生成完成{f' · {total}/{total} · 100%' if total else ''}",
                              result=render_result or None)
        self._pause_requested.discard(job_id)
        self.store.bump_edit_count(job_id)
        self.store.update_job(job_id, status="completed", progress=100, run_stage=None,
                              current_stage="render", finished_at=utc_now(), error=None)
        self.store.add_event(job_id, "render", "success", "job_completed",
                             f"成片已交付：{manifest.get('output')}", manifest)

    def mark_delivered(self, job_id: str) -> bool:
        if not self.store.get_job(job_id):
            raise KeyError("任务不存在")
        return self.store.mark_delivered(job_id)

    def _succeed_stage(self, job_id: str, workspace: Path, stage_id: str,
                       result: dict[str, Any], job: dict[str, Any]) -> None:
        # 渲染是最后一个节点：直接按出片收尾（登记产物、导出、交付事件）。
        if stage_id == "render":
            self._succeed(job_id, workspace, result,
                          export_dir=job.get("export_dir"),
                          export_mode=str(job.get("export_mode") or "merge"),
                          output_stem=str(job.get("title") or "").strip() or None,
                          media=str(job.get("source_path") or ""))
            return
        if job.get("pipeline_version") == "v2":
            outputs = {"asr": "clauses.json", "filter": "clauses.filtered.json",
                       "judge": "v2/candidates.json", "order": "v2/order.json",
                       "review": "v2/review.json"}
            path = workspace / outputs[stage_id]
            if path.is_file():
                self.store.add_artifact(job_id, stage_id, "json", f"V2 {stage_id} 结果", path, "application/json")
            if stage_id == "review":
                for filename, mime in (("成片文本.txt", "text/plain"), ("usage.json", "application/json")):
                    path = workspace / "v2" / filename
                    if path.is_file():
                        self.store.add_artifact(job_id, stage_id, "text", filename, path, mime)
            next_stage = {"asr": "filter", "filter": "judge", "judge": "order",
                          "order": "review", "review": "render"}[stage_id]
            self.store.prepare_stage_rerun(job_id, next_stage)
            if job_id in self._pause_requested:
                self.store.update_job(job_id, status="paused")
            else:
                self._enqueue(job_id, event=False, prepend=True)
            return
        outputs = {
            "filter": ("规则筛结果", workspace / "clauses.filtered.json"),
            "judge": ("AI 判定结果", workspace / "clauses.judged.json"),
            "order": ("AI 编排结果", workspace / "order.json"),
        }
        title, path = outputs[stage_id]
        if path.is_file():
            self.store.add_artifact(job_id, stage_id, "json", title, path, "application/json")
        self.store.add_event(job_id, stage_id, "success", "single_stage_completed",
                             "单节点重新执行完成", result)
        # 单节点重跑不再停在 waiting_input：自动优先推进当前计划的下游节点直到出片，
        # 批量恢复时优先完成当前计划，再执行下一个计划。
        next_stage = {"filter": "judge", "judge": "order", "order": "render"}[stage_id]
        self.store.prepare_stage_rerun(job_id, next_stage)
        if job_id in self._pause_requested:
            self.store.update_job(job_id, status="paused")
        else:
            self._enqueue(job_id, event=False, prepend=True)

    def _fail(self, job_id: str, stage: str, message: str) -> None:
        job = self.store.get_job(job_id)
        if job and job.get("pipeline_version") == "v2":
            for filename in ("candidates.json", "order.json", "review.json", "成片文本.txt", "usage.json"):
                path = Path(job["workspace"]) / "v2" / filename
                if path.is_file():
                    self.store.add_artifact(job_id, stage, "text" if filename.endswith("txt") else "json",
                                            f"V2 {filename}", path,
                                            "text/plain" if filename.endswith("txt") else "application/json")
        failed_progress = 0
        if stage == "render" and job:
            failed_progress = next((s["progress"] for s in job["stages"] if s["stage_id"] == stage), 0)
        self.store.update_stage(job_id, stage, status="failed", progress=failed_progress, message=message,
                               error=message, finished_at=utc_now())
        self.store.update_job(job_id, status="failed", current_stage=stage, run_stage=None,
                              error=f"[{stage}] {message}", finished_at=utc_now())
        self.store.add_event(job_id, stage, "error", "job_failed", message)

    def _finish(self, job_id: str, status: str, message: str) -> None:
        self.store.update_job(job_id, status=status, finished_at=utc_now())
        self.store.add_event(job_id, None, "warning", status, message)
