import hashlib
import json
import subprocess
import threading
from pathlib import Path

import pytest

from agent_video.postprocess import PostprocessService


def test_real_batch_is_silent_unique_animated_and_persistent(tmp_path):
    service = PostprocessService(tmp_path)
    output = tmp_path / "素材"
    output.mkdir()
    existing = output / "保留.txt"
    existing.write_text("original")
    try:
        job = service.materials.start({"output_dir": str(output), "video_count": 2,
                                      "image_count": 2, "duration": 2, "resolution": "160x240"})
        service.materials.thread.join(30)
        result = service.materials.jobs()["jobs"][0]
        assert result["status"] == "completed", result
        assert result["progress"] == 100
        assert result["video_done"] == result["image_done"] == 2
        manifest = json.loads(Path(job["manifest"]).read_text(encoding="utf-8"))
        assert len(manifest["files"]) == 4
        assert len({f["seed"] for f in manifest["files"]}) == 4
        assets = [a for a in service.library()["assets"] if a["id"] in result["assets"]]
        assert len(assets) == 4
        assert len({hashlib.sha256(Path(a["path"]).read_bytes()).hexdigest() for a in assets}) == 4
        for a in assets:
            assert (a["width"], a["height"]) == (160, 240)
            if a["kind"] == "video":
                assert not a["has_audio"]
                assert abs(a["duration"] - 2) < .05
                first = subprocess.check_output(["ffmpeg", "-v", "error", "-i", a["path"], "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
                middle = subprocess.check_output(["ffmpeg", "-v", "error", "-ss", "1", "-i", a["path"], "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
                assert first != middle
        assert not list(output.rglob("*.partial.mp4"))
        assert existing.read_text() == "original"
    finally:
        service.close()
    reopened = PostprocessService(tmp_path)
    try:
        assert reopened.materials.jobs()["jobs"][0]["status"] == "completed"
        assert len(reopened.scan_folder({"path": job["video_dir"], "kind": "video"})["assets"]) == 2
    finally:
        reopened.close()


def test_cancel_preserves_images_and_rejects_concurrent_batch(tmp_path, monkeypatch):
    service = PostprocessService(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    def slow_encode(*args):
        entered.set()
        release.wait(5)
        service.materials.check_cancel()
    monkeypatch.setattr(service.materials, "encode", slow_encode)
    try:
        job = service.materials.start({"video_count": 1, "image_count": 1, "resolution": "160x240"})
        assert entered.wait(10)
        with pytest.raises(ValueError, match="已有素材"):
            service.materials.start({})
        service.materials.cancel(job["id"])
        release.set()
        service.materials.thread.join(10)
        result = service.materials.jobs()["jobs"][0]
        assert result["status"] == "cancelled"
        assert result["image_done"] == 1 and result["video_done"] == 0
        assert len(list(Path(job["image_dir"]).glob("*.png"))) == 1
        assert not list(Path(job["manifest"]).parent.rglob("*.partial.mp4"))
        assert json.loads(Path(job["manifest"]).read_text(encoding="utf-8"))["status"] == "cancelled"
    finally:
        release.set()
        service.close()


@pytest.mark.parametrize("payload", [{"video_count": 101}, {"image_count": -.1}, {"image_count": 1.5},
    {"image_count": 0, "video_count": 0}, {"duration": 31}, {"output_dir": "relative"}, {"resolution": "4k"}])
def test_invalid_request_creates_no_batch(tmp_path, payload):
    service = PostprocessService(tmp_path)
    try:
        with pytest.raises(ValueError):
            service.materials.start(payload)
        assert service.materials.jobs()["jobs"] == []
    finally:
        service.close()
