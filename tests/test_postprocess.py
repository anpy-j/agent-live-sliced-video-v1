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
        assert len(reopened.library()["assets"]) == 8
        assert reopened.library()["progress_ids"] == library["progress_ids"]
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


@pytest.mark.parametrize("template_index", [0, 1, 2])
def test_folder_pair_screen_rotation_and_actual_animated_progress(service, tmp_path, template_index):
    from agent_video.editor.render import render
    from PIL import ImageDraw
    source = tmp_path / "source.mp4"
    video(source)
    vf, pf = tmp_path / "视频", tmp_path / "图片"
    vf.mkdir()
    pf.mkdir()
    # A shorter video must repeat, and its audio must stay muted.
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=red:s=80x120:r=30:d=0.5",
                    "-f", "lavfi", "-i", "sine=frequency=900:duration=0.5", "-c:v", "libx264", "-c:a", "aac", str(vf / "pip.mp4")], check=True)
    image = Image.new("RGB", (180, 180), "black")
    ImageDraw.Draw(image).rectangle((0, 0, 85, 180), fill="green")
    image.save(pf / "image.png")
    videos = service.scan_folder({"path": str(vf), "kind": "video"})["assets"]
    images = service.scan_folder({"path": str(pf), "kind": "image"})["assets"]
    progress_id = service.library()["progress_ids"][template_index]
    options = {"pip_enabled": True, "pip_video_pool": [a["id"] for a in videos],
               "pip_image_pool": [a["id"] for a in images], "pip_rotation_end": 999,
               "pip_opacity": .5, "progress": True, "progress_style": "animated",
               "progress_asset": progress_id, "progress_height": 24, "sticker_visible": .001}
    project = service.prepare(str(source), options)
    assert project["postprocess"]["pip_video_asset"]
    assert project["postprocess"]["pip_image_asset"]
    output = tmp_path / f"new-{template_index}.mp4"
    render(project, output)
    pixels = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(output), "-vf", "fps=4", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    import numpy as np
    frames = np.frombuffer(pixels, dtype=np.uint8).reshape(-1, 240, 160, 3)
    # Full-screen red raises R, while screen blending preserves the original blue.
    assert frames[-1, :200, :, 0].mean()>90
    assert frames[-1, :200, :, 2].mean()>220
    # Rotating asymmetric image changes green over time, beyond static compression noise.
    assert np.abs(frames[1, :200, :, 1].astype(float)-frames[2, :200, :, 1]).mean()>10
    assert frames[-1, -24:].max()>60
    assert np.count_nonzero(frames[-1, -24:].max(axis=2)>50)>np.count_nonzero(frames[1, -24:].max(axis=2)>50)
    with Image.open(service.asset(service.library_id, progress_id)["path"]) as gif:
        first = gif.convert("RGBA").tobytes()
        gif.seek(6)
        assert gif.convert("RGBA").tobytes()!=first


def test_empty_folder_and_invalid_pair_do_not_queue(service, tmp_path):
    source = tmp_path / "source.mp4"
    video(source)
    assert not service.scan_folder({"path": str(tmp_path), "kind": "image"})["assets"]
    result = service.submit({"paths": [str(source)], "options": {"pip_enabled": True, "pip_video_pool": [], "pip_image_pool": []}})
    assert not result["jobs"] and result["errors"]


def test_animated_pattern_repeats_without_stretching(service, tmp_path):
    from agent_video.editor.render import render
    import numpy as np
    source = tmp_path / "source.mp4"
    video(source)
    project = service.prepare(str(source), {"progress": True, "progress_style": "animated",
                              "progress_asset": service.library()["progress_ids"][0], "progress_height": 6})
    output = tmp_path / "repeat.mp4"
    render(project, output)
    data = subprocess.check_output(["ffmpeg", "-v", "error", "-ss", "1.9", "-i", str(output),
                                    "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    frame = np.frombuffer(data, dtype=np.uint8).reshape(240,160,3)
    # A scaled row is 144px wide; visible colored detail beyond that is the repeated row.
    assert frame[-6:,144:,0].max()>60


def test_single_star_crosses_reserved_band_for_whole_video(service, tmp_path):
    from agent_video.editor.render import render
    import numpy as np
    source = tmp_path / "source.mp4"
    video(source)
    library = service.library()
    star = next(key for key, mode in library["progress_modes"].items() if mode == "moving_star")
    project = service.prepare(str(source), {"progress": True, "progress_style": "moving_star",
                              "progress_asset": star, "progress_height": 24})
    output = tmp_path / "moving-star.mp4"
    render(project, output)
    data = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(output), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    frames = np.frombuffer(data, dtype=np.uint8).reshape(-1,240,160,3)
    positions = []
    for frame in (frames[0], frames[len(frames)//2], frames[-1]):
        y,x = np.where(frame[-24:,:,0]>90)
        assert len(x)>5
        assert x.max()-x.min()<24  # Only one star, no repeated row or filled trail.
        positions.append(x.mean())
    assert positions[0]<24
    assert 65<positions[1]<95
    assert positions[2]>136
