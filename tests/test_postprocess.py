import json
import subprocess
import time
from pathlib import Path

import pytest
from PIL import Image

from agent_video.postprocess import PostprocessService
from agent_video.postprocess.music import validate_url


@pytest.fixture
def service(tmp_path):
    s = PostprocessService(tmp_path)
    s.settings({"output_dir": str(tmp_path / "out"), "acestep_url": "http://127.0.0.1:8001"})
    yield s
    s.close()


def video(path):
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=blue:s=160x240:r=30:d=2",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=2", "-c:v", "libx264", "-c:a", "aac", str(path)], check=True)


def test_library_survives_restart_and_dynamic_assets_are_original(tmp_path):
    s = PostprocessService(tmp_path)
    library = s.library()
    gif = next(a for a in library["assets"] if a["name"] == "闪烁星星.gif")
    with Image.open(gif["path"]) as image:
        assert image.n_frames > 1
        image.seek(0)
        first = image.convert("RGBA").tobytes()
        image.seek(6)
        assert image.convert("RGBA").tobytes() != first
    generated = s.generate_texture()
    assert Path(generated["path"]).exists()
    s.create({"title": "a later task"})
    s.close()
    reopened = PostprocessService(tmp_path)
    try:
        assert reopened.library()["id"] == library["id"]
        assert len(reopened.library()["assets"]) == 4
        assert not (tmp_path / "data" / "editor").exists()
    finally:
        reopened.close()


@pytest.mark.parametrize("style", ["solid", "striped", "glow"])
def test_actual_standalone_export_with_animation_rotated_pip_and_loop_music(service, tmp_path, style):
    source = tmp_path / "source.mp4"
    video(source)
    original = source.read_bytes()
    music = tmp_path / "music.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=600:duration=0.7", str(music)], check=True)
    audio = service.import_asset({"path": str(music)})
    library = service.library()["assets"]
    options = {"progress": True, "progress_style": style, "progress_color": "ffcc44", "progress_height": 8,
               "pip_asset": next(a["id"] for a in library if a["name"].endswith(".png")), "pip_rotation": 20,
               "sticker_asset": next(a["id"] for a in library if a["name"] == "闪烁星星.gif"),
               "music_asset": audio["id"], "music_volume": .1, "music_loop": True}
    result = service.submit({"paths": [str(source)], "options": options})
    assert not result["errors"]
    job_id = result["jobs"][0]["id"]
    deadline = time.monotonic()+30
    while time.monotonic()<deadline:
        job = service.get_export(job_id)
        if job["status"] in {"completed", "failed"}:
            break
        time.sleep(.05)
    assert job["status"] == "completed", job
    probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", job["output"]]))
    assert abs(float(probe["format"]["duration"])-2)<.1
    v = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (160, 240)
    assert source.read_bytes() == original
    snapshot = json.loads(Path(job["snapshot"]).read_text(encoding="utf-8"))
    assert snapshot["timelines"][0]["tracks"][2]["clips"][0]["loop"] is True


def test_invalid_resource_and_injection_do_not_queue(service, tmp_path):
    source = tmp_path / "source.mp4"
    video(source)
    for options in ({"pip_asset": "missing"}, {"progress_color": "red;movie=secret"}):
        result = service.submit({"paths": [str(source)], "options": options})
        assert not result["jobs"]
        assert result["errors"]
    for url in ("file:///tmp/a", "http://user:secret@localhost", "javascript:test"):
        with pytest.raises(ValueError):
            validate_url(url)
