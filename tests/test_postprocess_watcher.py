import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from PIL import Image

from agent_video.postprocess import PostprocessService
from agent_video.postprocess.watcher import FolderWatcher


def video(path, color):
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"color={color}:s=160x240:r=30:d=1",
                    "-c:v", "libx264", str(path)], check=True)


def setup(tmp_path):
    service = PostprocessService(tmp_path)
    service.watcher.close()
    service.watcher = FolderWatcher(service, interval=100, stable_seconds=0)
    for name in ("input", "videos", "images"):
        (tmp_path / name).mkdir()
    video(tmp_path / "videos" / "a.mp4", "red")
    video(tmp_path / "videos" / "b.mp4", "green")
    Image.new("RGB", (80, 80), "red").save(tmp_path / "images" / "a.png")
    Image.new("RGB", (80, 80), "green").save(tmp_path / "images" / "b.png")
    recipe = {"progress_asset": service.progress_ids[0], "progress_style": "animated", "progress_height": 8,
              "sticker_asset": next(a["id"] for a in service.library()["assets"] if a["name"] == "闪烁星星.gif"),
              "sticker_visible": .001}
    config = {"input_dir": str(tmp_path / "input"), "output_dir": str(tmp_path / "out"),
              "video_dir": str(tmp_path / "videos"), "image_dir": str(tmp_path / "images"), "options": recipe}
    service.watcher.configure(config)
    return service, config


def finish(service):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        service.watcher.tick()
        records = service.watcher.status()["records"]
        if records and records[0]["status"] in {"completed", "failed"}:
            assert records[0]["status"] == "completed", records
            return records[0]
        time.sleep(.1)
    raise AssertionError("watch export timeout")


def test_actual_auto_export_no_material_reuse_exhaustion_and_restart(tmp_path):
    service, config = setup(tmp_path)
    try:
        video(tmp_path / "input" / "one.mp4", "blue")
        original = (tmp_path / "input" / "one.mp4").read_bytes()
        service.watcher.tick()
        first = finish(service)
        assert Path(first["output"]).parent == tmp_path / "out"
        assert Path(first["output"]).exists()
        assert (tmp_path / "input" / "one.mp4").read_bytes() == original
        snapshot = json.loads(Path(service.get_export(first["export_id"])["snapshot"]).read_text(encoding="utf-8"))
        assert snapshot["postprocess"]["progress"] and snapshot["postprocess"]["sticker_asset"]
        assert snapshot["postprocess"]["pip_video_asset"] and snapshot["postprocess"]["pip_image_asset"]
        shutil.copyfile(tmp_path / "input" / "one.mp4", tmp_path / "input" / "renamed.mp4")
        # Copied overlays with new names must not provide extra usable entries.
        shutil.copyfile(first["video"]["path"], tmp_path / "videos" / "renamed.mp4")
        shutil.copyfile(first["image"]["path"], tmp_path / "images" / "renamed.png")
        video(tmp_path / "input" / "two.mp4", "yellow")
        service.watcher.tick()
        second = finish(service)
        assert second["source"] != first["source"]
        assert second["video"]["hash"] != first["video"]["hash"]
        assert second["image"]["hash"] != first["image"]["hash"]
        video(tmp_path / "input" / "three.mp4", "white")
        service.watcher.tick()
        service.watcher.tick()
        status = service.watcher.status()
        assert status["state"] == "exhausted"
        assert status["used"] == {"video": 2, "image": 2}
        assert len(status["records"]) == 2
    finally:
        service.close()
    reopened = PostprocessService(tmp_path)
    reopened.watcher.close()
    reopened.watcher = FolderWatcher(reopened, interval=100, stable_seconds=0)
    try:
        assert reopened.watcher.status()["config"]["enabled"]
        reopened.watcher.tick()
        reopened.watcher.tick()
        assert reopened.watcher.status()["state"] == "exhausted"
        assert len(reopened.watcher.status()["records"]) == 2
        video(tmp_path / "videos" / "new.mp4", "purple")
        Image.new("RGB", (80, 80), "purple").save(tmp_path / "images" / "new.png")
        third = finish(reopened)
        assert third["source"].endswith("three.mp4")
        assert reopened.watcher.status()["used"] == {"video": 3, "image": 3}
    finally:
        reopened.close()


def test_file_stability_pause_and_failed_retry_keeps_reservations(tmp_path, monkeypatch):
    service, config = setup(tmp_path)
    try:
        video(tmp_path / "input" / "one.mp4", "blue")
        service.watcher.stable_seconds = 999
        service.watcher.tick()
        service.watcher.tick()
        assert service.watcher.status()["records"] == []
        service.watcher.stable_seconds = 0
        real_export = service.export
        monkeypatch.setattr(service, "export", lambda *a, **kw: (_ for _ in ()).throw(ValueError("test failure")))
        service.watcher.tick()
        failed = service.watcher.status()["records"][0]
        assert failed["status"] == "failed"
        service.watcher.pause()
        video(tmp_path / "input" / "two.mp4", "yellow")
        service.watcher.tick()
        assert len(service.watcher.status()["records"]) == 1
        monkeypatch.setattr(service, "export", real_export)
        service.watcher.retry(failed["id"])
        retried = finish(service)
        assert retried["id"] == failed["id"] and retried["video"] == failed["video"] and retried["image"] == failed["image"]
        assert service.watcher.status()["used"] == {"video": 1, "image": 1}
    finally:
        service.close()


@pytest.mark.parametrize("key,path", [("output_dir", "input"), ("output_dir", "input/nested"),
    ("video_dir", "input"), ("image_dir", "input"), ("input_dir", "missing")])
def test_rejects_recursive_input_or_material_overlap(tmp_path, key, path):
    service, config = setup(tmp_path)
    try:
        config[key] = str(tmp_path / path)
        with pytest.raises(ValueError):
            service.watcher.configure(config)
    finally:
        service.close()
