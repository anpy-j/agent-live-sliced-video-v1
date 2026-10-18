"""V2 orchestrator. Shared S1/S2 and renderer; independent S3/S4/S5 artifacts."""
from __future__ import annotations

import os
from pathlib import Path

from agent_video.pipeline_v3.ai import DEFAULT_TIMEOUT as AI_DEFAULT_TIMEOUT
from agent_video.pipeline_v3.errors import PipelineError
from agent_video.pipeline_v3.filter import activate_account_vocab
from agent_video.pipeline_v3.render import build_segments, build_virtual_segments, render_video
from agent_video.pipeline_v3.run import _next_render_output, run_pipeline_stage
from .ai import Calls
from .common import (compile_clips, digest, emit, plan_blocks, prepare, read, save,
                     source_fingerprint, source_rows)
from .content import arrange, deterministic_issues, extract, review


def required_for_stage(workspace, stage):
    return Path(workspace) / {"asr": "clauses.json", "filter": "clauses.json",
                              "judge": "clauses.filtered.json", "order": "v2/candidates.json",
                              "review": "v2/order.json", "render": "v2/review.json"}[stage]


def invalidate_stage(workspace, stage):
    """Explicit rerun invalidates only V2 downstream caches; retains usage history."""
    directory = Path(workspace) / "v2"
    downstream = {
        "filter": ("judge", "order", "review"), "judge": ("judge", "order", "review"),
        "order": ("order", "review"), "review": ("review",), "render": (),
    }[stage]
    artifacts = {"judge": ("candidates.json", "clauses.judged.json", "judge.progress.json"),
                 "order": ("order.json",), "review": ("review.json", "成片文本.txt")}
    for sid in downstream:
        for name in artifacts[sid]:
            (directory / name).unlink(missing_ok=True)
        cache = directory / "calls" / sid
        # Keep audit records; move them so an explicit retry can launch a new call.
        if cache.exists():
            archive = directory / "history"
            archive.mkdir(parents=True, exist_ok=True)
            import time
            cache.rename(archive / f"{sid}-{time.time_ns()}")


def check_upstream(value, source_fp, name):
    if value.get("pipeline_version") != "v2" or value.get("source_fingerprint") != source_fp:
        raise PipelineError(f"V2 {name} 与当前上游不一致，请从 S3 重新执行")


def run_pipeline(media, workdir, *, target_seconds=(70.0, 90.0), product_name=None,
                 output_stem=None, on_stage=None, virtual_timeline=None, only_stage=None,
                 ai_model=None, ai_timeout=AI_DEFAULT_TIMEOUT, ai_fn=None, render_fn=None,
                 **shared_kwargs):
    workspace = Path(workdir).resolve()
    directory = workspace / "v2"
    directory.mkdir(parents=True, exist_ok=True)
    if only_stage not in {None, "asr", "filter", "judge", "order", "review", "render"}:
        raise PipelineError(f"V2 不支持此节点：{only_stage}")
    if len(target_seconds) != 2 or not 0 < target_seconds[0] <= target_seconds[1]:
        raise PipelineError("V2 目标时长不合法")
    if only_stage in {None, "asr"}:
        prepare(media, workspace, on_stage=on_stage, virtual_timeline=virtual_timeline,
                product_name=product_name, target_seconds=target_seconds, **shared_kwargs)
        if only_stage == "asr":
            return {"pipeline_version": "v2", "stage": "asr"}
    elif only_stage == "filter":
        return run_pipeline_stage(media, str(workspace), "filter", on_stage=on_stage,
                                  virtual_timeline=virtual_timeline)
    activate_account_vocab()
    data = read(workspace / "clauses.filtered.json")
    if virtual_timeline is not None:
        data = dict(data, virtual_timeline=virtual_timeline.to_dict())
    rows = source_rows(data)
    source_fp = source_fingerprint(data, product_name)
    calls = Calls(directory, ai_model or os.environ.get("PIPELINE_AI_MODEL") or "auto",
                  ai_timeout, ai_fn)
    if only_stage in {None, "judge"}:
        emit(on_stage, "judge", "start", "V2 整理完整表达，保留有用待承接片段")
        candidates = extract(calls, rows, product_name, directory, source_fp)
        emit(on_stage, "judge", "done", f"V2 整理 {len(candidates['blocks'])} 个完整表达，"
             f"{len(candidates['unresolved_fragments'])} 个待承接片段")
        if only_stage:
            return {"pipeline_version": "v2", "stage": only_stage}
    else:
        candidates = read(directory / "candidates.json")
        check_upstream(candidates, source_fp, "S3候选")
    if only_stage in {None, "order"}:
        emit(on_stage, "order", "start", "V2 从完整表达中精选、去重并编排")
        order = arrange(calls, candidates, target_seconds, directory)
        emit(on_stage, "order", "done", f"V2 初选 {len(order['plan']['selected'])} 个表达、{order['seconds']:.2f}秒；待S5验收")
        if only_stage:
            return {"pipeline_version": "v2", "stage": only_stage}
    else:
        order = read(directory / "order.json")
        check_upstream(order, source_fp, "S4编排")
        if order.get("candidates_fingerprint") != digest(candidates) or order.get("target") != list(target_seconds):
            raise PipelineError("V2 S4 候选或目标已改变，请重新编排")
    if only_stage in {None, "review"}:
        emit(on_stage, "review", "start", "V2 验收实际成片文本，最多一次定点修复")
        approved = review(calls, candidates, order, target_seconds, directory)
        emit(on_stage, "review", "done", f"V2 文本验收通过，{approved['seconds']:.2f}秒；未进行画面审核")
        if only_stage:
            return {"pipeline_version": "v2", "stage": only_stage}
    else:
        approved = read(directory / "review.json")
    check_upstream(approved, source_fp, "S5验收")
    if (approved.get("passed") is not True or approved.get("issues") or
            approved.get("candidates_fingerprint") != digest(candidates) or
            approved.get("order_fingerprint") != digest(order) or
            approved.get("target") != list(target_seconds)):
        raise PipelineError("V2 S5 未通过或验收已失效，禁止渲染")
    selected = plan_blocks(approved["plan"], candidates["blocks"])
    # Reconstruct every expression, verifying no stale/edited text or duration.
    from .common import make_block
    for block in selected:
        actual = make_block(block, rows)
        if digest(actual) != digest(block):
            raise PipelineError("V2 已验收表达与原始口播不符，禁止渲染")
    if deterministic_issues(selected, target_seconds):
        raise PipelineError("V2 已验收计划未通过确定性复核，禁止渲染")
    clips = compile_clips(selected, rows)
    segments = build_virtual_segments(clips, virtual_timeline) if virtual_timeline is not None else build_segments(clips)
    emit(on_stage, "render", "start", "V2 验收通过，复用渲染器输出完整表达片段")
    output = _next_render_output(str(workspace), output_stem)
    renderer = render_fn or render_video
    renderer(media, segments, output, str(workspace), virtual_timeline=virtual_timeline)
    manifest = {"pipeline_version": "v2", "workflow": "content_v2", "source": data.get("source", media),
                "main_product": product_name or "未明确", "duration": data.get("duration"),
                "total_seconds": round(sum(b["seconds"] for b in selected), 3),
                "target_seconds": {"min": target_seconds[0], "max": target_seconds[1]},
                "selected_blocks": [b["id"] for b in selected], "segments": segments,
                "output": os.path.relpath(output, workspace), "ai_calls": len(calls.ledger["calls"]),
                "text_review_passed": True, "visual_reviewed": False,
                "source_fingerprint": source_fp, "review_fingerprint": digest(approved)}
    save(directory / "manifest.json", manifest)
    save(workspace / "manifest.json", manifest)
    emit(on_stage, "render", "done", f"V2 已渲染 {manifest['total_seconds']:.2f}秒")
    return manifest
