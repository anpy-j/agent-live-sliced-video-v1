"""Atomic, read-only UI snapshots of validated S3 batch decisions."""
import json
import os
import hashlib
from pathlib import Path


class JudgeProgress:
    """Validated batch checkpoints, separate from the UI's progress snapshot."""

    def __init__(self, workdir):
        self.path = Path(workdir) / "judge.progress.json"
        self.checkpoint = Path(workdir) / "judge.checkpoint.json"
        self.decisions = {}
        try:
            saved = json.loads(self.checkpoint.read_text(encoding="utf-8"))
            self.batches = saved["batches"] if saved.get("version") == 1 else {}
            if not isinstance(self.batches, dict):
                self.batches = {}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self.batches = {}
        self({})

    @staticmethod
    def key(model, prompt, schema):
        # "auto" may resolve to a different subscription/model on the next run.
        if not model or model == "auto":
            return None
        if (os.environ.get("PIPELINE_AI_ENGINE", "llm") == "llm"
                and os.environ.get("PIPELINE_AI_PROVIDER", "auto") in ("", "auto")):
            return None
        settings = {key: os.environ.get(key, "") for key in (
            "PIPELINE_AI_ENGINE", "PIPELINE_AI_PROVIDER", "PIPELINE_AI_MODEL",
            "LIVE_CUT_API_BASE_URL", "TYPESAFE_BASE_URL")}
        payload = json.dumps([1, model, prompt, schema, settings], ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def lookup(self, key):
        return self.batches.get(key)

    def store(self, key, batch):
        self.batches[key] = list(batch.values())
        temporary = self.checkpoint.with_suffix(".tmp")
        temporary.write_text(json.dumps({"version": 1, "batches": self.batches},
                                        ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, self.checkpoint)

    def __call__(self, batch):
        self.decisions.update(batch)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"decisions": list(self.decisions.values())},
                                        ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, self.path)


def progress_writer(workdir):
    return JudgeProgress(workdir)
