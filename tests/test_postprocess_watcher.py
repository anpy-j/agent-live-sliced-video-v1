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


def test_actual_all_pairs_once_then_restart_round(tmp_path):
    service, config = setup(tmp_path)
    Image.new("RGB", (80, 80), "blue").save(tmp_path / "images" / "c.png")
    records = []
    colors = ["blue", "yellow", "white", "black", "purple", "cyan", "orange"]
    try:
        for index, color in enumerate(colors):
            if index == 2:
                service.close()
                service = PostprocessService(tmp_path)
                service.watcher.close()
                service.watcher = FolderWatcher(service, interval=100, stable_seconds=0)
                assert service.watcher.status()["config"]["enabled"]
                assert service.watcher.status()["cycle"]["round"] == 1
            source = tmp_path / "input" / f"{index}.mp4"
            video(source, color)
            original = source.read_bytes()
            service.watcher.tick()
            record = finish(service)
            records.append(record)
            assert Path(record["output"]).parent == tmp_path / "out"
            assert Path(record["output"]).exists()
            assert source.read_bytes() == original
            if index == 0:
                snapshot = json.loads(Path(service.get_export(record["export_id"])["snapshot"]).read_text(encoding="utf-8"))
                assert snapshot["postprocess"]["progress"] and snapshot["postprocess"]["sticker_asset"]
                assert snapshot["postprocess"]["pip_video_asset"] and snapshot["postprocess"]["pip_image_asset"]
                shutil.copyfile(source, tmp_path / "input" / "renamed.mp4")
                # Copied overlays do not create extra combinations.
                shutil.copyfile(record["video"]["path"], tmp_path / "videos" / "renamed.mp4")
                shutil.copyfile(record["image"]["path"], tmp_path / "images" / "renamed.png")
        pairs = {(r["video"]["hash"], r["image"]["hash"]) for r in records[:6]}
        assert len(pairs) == 6
        assert {r["round"] for r in records[:6]} == {1}
        assert records[6]["round"] == 2
        assert (records[6]["video"]["hash"], records[6]["image"]["hash"]) in pairs
        status = service.watcher.status()
        assert status["used"] == {"video": 2, "image": 3}
        assert status["available"] == {"video": 2, "image": 3}
        assert status["cycle"] == {"round": 2, "total": 6, "used": 1, "remaining": 5, "allocated": 7}
        assert len(status["records"]) == 7
    finally:
        service.close()


def test_empty_pool_waits_then_continues_after_adding_material(tmp_path):
    service, config = setup(tmp_path)
    try:
        for path in (tmp_path / "images").glob("*.png"):
            path.unlink()
        video(tmp_path / "input" / "one.mp4", "blue")
        service.watcher.tick()
        service.watcher.tick()
        assert service.watcher.status()["state"] == "exhausted"
        assert service.watcher.status()["records"] == []
        Image.new("RGB", (80, 80), "purple").save(tmp_path / "images" / "new.png")
        record = finish(service)
        assert record["round"] == 1
    finally:
        service.close()


def test_migrates_legacy_selections_without_resetting_history(tmp_path):
    service, config = setup(tmp_path)
    try:
        video(tmp_path / "input" / "one.mp4", "blue")
        service.watcher.tick()
        first = finish(service)
    finally:
        service.close()
    # Recreate the pre-pair schema, retaining its source/material audit records.
    import sqlite3
    with sqlite3.connect(tmp_path / "data" / "postprocess" / "editor.db") as con:
        con.execute("DROP TABLE watch_pairs")
        con.execute("DROP TABLE watch_round")
        first.pop("round")
        con.execute("UPDATE watch_records SET data=? WHERE id=?", (json.dumps(first), first["id"]))
    reopened = PostprocessService(tmp_path)
    reopened.watcher.close()
    reopened.watcher = FolderWatcher(reopened, interval=100, stable_seconds=0)
    try:
        assert reopened.watcher.status()["cycle"]["allocated"] == 1
        assert reopened.watcher.status()["records"][0]["round"] == 1
        video(tmp_path / "input" / "two.mp4", "yellow")
        reopened.watcher.tick()
        record = finish(reopened)
        assert record["round"] == 1
        assert (record["video"]["hash"], record["image"]["hash"]) != (first["video"]["hash"], first["image"]["hash"])
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
        assert service.watcher.status()["cycle"]["allocated"] == 1
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
