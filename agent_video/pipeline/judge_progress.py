"""Atomic, read-only UI snapshots of validated S3 batch decisions."""
import json
import os
from pathlib import Path


def progress_writer(workdir):
    path = Path(workdir) / "judge.progress.json"
    decisions = {}

    def publish(batch):
        decisions.update(batch)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"decisions": list(decisions.values())},
                                        ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)

    publish({})
    return publish
