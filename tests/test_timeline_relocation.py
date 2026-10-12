import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_video.db import Store
from agent_video.runner import JobRunner, refresh_timeline_media_paths


def relocated_draft(tmp_path):
    media = tmp_path / "new" / "renamed.mp4"
    media.parent.mkdir()
    media.touch()
    draft = tmp_path / "selected.json"
    meta = {"timeline_id": "selected", "segments": [{
        "segment_id": "片段1", "timeline_start": 0, "timeline_end": 10,
        "source_start": 20, "source_end": 33, "speed": 1.3,
        "source_path": str(tmp_path / "old" / "original.mp4"),
    }]}
    fresh = json.loads(json.dumps(meta))
    fresh["segments"][0]["source_path"] = str(media)
    draft.write_text(json.dumps(fresh), encoding="utf-8")
    return meta, draft, media


def test_refresh_legacy_snapshot_uses_exact_timeline_and_keeps_cut(tmp_path):
    meta, draft, media = relocated_draft(tmp_path)
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"project": {"timelines": [
        {"timeline_id": "other", "path": "other.json", "active": True},
        {"timeline_id": "selected", "path": str(draft)},
    ]}}), encoding="utf-8")
    updated = refresh_timeline_media_paths(meta, cache)
    expected = dict(meta["segments"][0], source_path=str(media))
    assert updated["segments"] == [expected]
    assert updated["draft_path"] == str(draft)
    assert meta["segments"][0]["source_path"] != str(media)


def test_refresh_refuses_changed_edit(tmp_path):
    meta, draft, _ = relocated_draft(tmp_path)
    meta["draft_path"] = str(draft)
    fresh = json.loads(draft.read_text())
    fresh["segments"][0]["source_start"] = 21
    draft.write_text(json.dumps(fresh))
    with pytest.raises(ValueError, match="剪辑范围或倍速"):
        refresh_timeline_media_paths(meta, tmp_path / "missing-cache")


def test_existing_media_and_ambiguous_legacy_cache_are_unchanged(tmp_path):
    meta, draft, media = relocated_draft(tmp_path)
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"project": {"timelines": [
        {"timeline_id": "selected", "path": str(draft)},
        {"timeline_id": "selected", "path": "another.json"},
    ]}}))
    assert refresh_timeline_media_paths(meta, cache) == meta
    meta["segments"][0]["source_path"] = str(media)
    assert refresh_timeline_media_paths(meta, cache) == meta


def test_retry_persists_paths_and_invalidates_old_clause_mapping(tmp_path):
    meta, draft, media = relocated_draft(tmp_path)
    meta["draft_path"] = str(draft)
    store = Store(tmp_path / "data" / "agent.db")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "clauses.json").write_text('{"source_path":"old"}')
    job_id = store.create_job(title="test", source_path="timeline", workspace=str(workspace),
                              job_type="timeline", timeline_meta=meta)
    store.update_job(job_id, status="failed")
    runner = JobRunner(store, tmp_path)
    with patch.object(runner, "enqueue") as enqueue:
        runner.retry(job_id)
    enqueue.assert_called_once_with(job_id)
    saved = store.get_job(job_id)["timeline_meta"]
    assert saved["segments"][0]["source_path"] == str(media)
    assert json.loads((workspace / "virtual_timeline.json").read_text(encoding="utf-8")) == saved
    assert not (workspace / "clauses.json").exists()
