"""Verified, immutable S1 snapshots shared by edits of the same draft timeline."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .timeline import VirtualTimeline


SNAPSHOT = "s1-cache.json"


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def input_key(timeline: VirtualTimeline, version: str) -> str:
    files = []
    for name in timeline.source_paths:
        path = Path(name)
        metadata = path.stat()
        files.append({"path": str(path.resolve()), "size": metadata.st_size,
                      "mtime_ns": metadata.st_mtime_ns})
    # Bump this contract when S1's transcription/splitting semantics change.
    return _digest({"contract": 2, "pipeline_version": version,
                    "timeline": timeline.to_dict(), "files": files})


def prepare(workspace: Path, timeline: VirtualTimeline, version: str):
    """Seed only S1; unverified historical artifacts must be regenerated once."""
    key = input_key(timeline, version)
    candidates = [workspace / SNAPSHOT]
    if workspace.parent.name == "edits":
        candidates.extend(sorted(workspace.parent.glob(f"*/{SNAPSHOT}"), reverse=True))
        root = workspace.parent.parent
        if root.parent.name == "timelines":
            # A relocated draft/media path can change the task directory digest,
            # while older jobs retain their original directory. The verified key,
            # not directory membership, decides whether S1 is still reusable.
            prefix = root.name.rsplit("-", 1)[0] + "-"
            for sibling in sorted(root.parent.iterdir(), reverse=True):
                if sibling != root and sibling.is_dir() and sibling.name.startswith(prefix):
                    candidates.extend(sorted((sibling / "edits").glob(f"*/{SNAPSHOT}"), reverse=True))
    reused = None
    data = None
    for candidate in candidates:
        try:
            snapshot = json.loads(candidate.read_text(encoding="utf-8"))
            result = snapshot["data"]
            if (snapshot["input_key"] == key and snapshot["data_digest"] == _digest(result)
                    and isinstance(result, dict) and result.get("clauses")
                    and result.get("source") == timeline.title
                    and result.get("virtual_timeline") == timeline.to_dict()):
                data, reused = result, candidate.parent
                break
        except (OSError, ValueError, KeyError, TypeError):
            continue
    # timeline.json may include S2/S3/S4 decisions. Never seed a new attempt with it.
    for name in ("clauses.json", "timeline.json"):
        (workspace / name).unlink(missing_ok=True)
    if data is not None:
        (workspace / "clauses.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        # The existing audio cache checks timeline geometry, not source bytes.
        for name in ("virtual_audio.wav", "virtual_audio.wav.manifest.json",
                     "audio16k.wav", "audio16k.wav.manifest.json", SNAPSHOT):
            (workspace / name).unlink(missing_ok=True)
    return key, reused


def save(workspace: Path, key: str):
    """Called synchronously at S1 completion, before downstream mutations."""
    data = json.loads((workspace / "clauses.json").read_text(encoding="utf-8"))
    snapshot = {"input_key": key, "data_digest": _digest(data), "data": data}
    temporary = workspace / (SNAPSHOT + ".tmp")
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(workspace / SNAPSHOT)
