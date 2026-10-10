from __future__ import annotations

import json
import math
import os
import subprocess
from pathlib import Path

from .model import SUBTITLE_STYLES, timeline_duration


def escape_filter_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def ass_time(value):
    centis = max(0, round(value * 100))
    return f"{centis // 360000}:{centis // 6000 % 60:02}:{centis // 100 % 60:02}.{centis % 100:02}"


def ass_text(text):
    # Treat user text literally, never as injected ASS override tags.
    return str(text).replace("\\", "＼").replace("{", "｛").replace("}", "｝").replace("\r", "").replace("\n", "\\N")


def write_subtitles(project, timeline, folder):
    w, h = project["width"], project["height"]
    font = "Microsoft YaHei" if os.name == "nt" else "PingFang SC"
    lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}",
             "WrapStyle: 0", "[V4+ Styles]",
             "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"]
    for key, style in SUBTITLE_STYLES.items():
        lines.append(f"Style: {key},{font},56,&H00{style['color']},&H00FFFFFF,&H00101010,&H80000000,"
                     f"{-1 if style.get('bold') else 0},0,0,0,100,100,0,0,{3 if style.get('box') else 1},{style['outline']},1,2,35,35,120,1")
    lines += ["[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for layer, track in enumerate(timeline["tracks"]):
        if track["kind"] != "subtitle" or track.get("hidden"):
            continue
        for clip in track["clips"]:
            style = clip.get("style", "classic")
            text = ass_text(clip.get("text", ""))
            if style == "highlight" and text:
                chars = list(text.replace("\\N", " "))
                d = max(1, round(clip["duration"] * 100 / len(chars)))
                text = "".join(f"{{\\kf{d}}}{c}" for c in chars)
            x = round(w / 2 + clip.get("x", 0) * w)
            y = round(h * .9 + clip.get("y", 0) * h)
            alpha = round((1 - clip.get("opacity", 1)) * 255)
            overrides = f"{{\\pos({x},{y})\\fs{clip.get('font_size',56)}\\alpha&H{alpha:02X}&}}"
            lines.append(f"Dialogue: {layer},{ass_time(clip['start'])},{ass_time(clip['start']+clip['duration'])},{style},,0,0,0,,{overrides}{text}")
    path = folder / "subtitles.ass"
    path.write_text("\n".join(lines), encoding="utf-8-sig")
    return path


def tempo_filters(speed):
    parts = []
    while speed > 2:
        parts.append("atempo=2")
        speed /= 2
    while speed < .5:
        parts.append("atempo=0.5")
        speed *= 2
    parts.append(f"atempo={speed:.9f}")
    return ",".join(parts)


def build_command(project, output: Path):
    timeline = project["timelines"][0]
    duration = timeline_duration(timeline)
    if duration <= 0:
        raise ValueError("时间线为空")
    w, h, fps = project["width"], project["height"], project["fps"]
    assets = {a["id"]: a for a in project["assets"]}
    folder = output.parent
    folder.mkdir(parents=True, exist_ok=True)
    work = folder / (".livecut-" + output.stem)
    work.mkdir(exist_ok=True)
    command = ["ffmpeg", "-hide_banner", "-y", "-v", "error", "-filter_complex_threads", "1",
               "-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:r={fps}:d={duration:.9f}",
               "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo:d={duration:.9f}"]
    filters = ["[0:v]format=rgba[canvas]"]
    audio_labels = ["[1:a]"]
    base, input_index, overlay_index = "canvas", 2, 0

    entries = [(track, clip) for track in timeline["tracks"] if not track.get("hidden")
               for clip in sorted(track["clips"], key=lambda c: c["start"]) if track["kind"] != "subtitle"]
    post = project.get("postprocess", {})
    if post.get("pip_asset"):
        a = assets[post["pip_asset"]]
        entries.append(({"kind": "video", "muted": True}, {"asset_id": a["id"], "start": 0, "in": 0,
                        "duration": duration, "speed": 1, "scale": .18, "x": .8, "y": .05,
                        "opacity": post.get("pip_opacity", .01), "loop": True}))
    if post.get("sticker_asset"):
        a = assets[post["sticker_asset"]]
        sticker_w = max(2, int(w * .2) // 2 * 2)
        sticker_h = round(sticker_w * a["height"] / max(1, a["width"]))
        for x in ((-sticker_w + w * .02) / w, .98):
            for y in ((-sticker_h + h * .02) / h, .98):
                entries.append(({"kind": "image", "muted": True}, {"asset_id": a["id"], "start": 0, "in": 0,
                                "duration": duration, "speed": 1, "scale": .2, "x": x, "y": y, "loop": True}))
    for track, clip in entries:
        a = assets[clip["asset_id"]]
        path = Path(a["path"])
        stat = path.stat()
        if stat.st_size != a["size"] or stat.st_mtime_ns != a["mtime_ns"]:
            raise ValueError(f"素材已修改：{a['name']}")
        dur, start, speed = clip["duration"], clip["start"], clip.get("speed", 1)
        if a["kind"] == "image":
            command += ["-loop", "1"] if path.suffix.lower() != ".gif" else ["-stream_loop", "-1"]
        elif clip.get("loop"):
            command += ["-stream_loop", "-1"]
        command += ["-threads", "1", "-ss", str(clip.get("in", 0)), "-t", f"{dur*speed:.9f}", "-i", str(path)]
        if track["kind"] != "audio":
            idx = overlay_index
            scale = clip.get("scale", 1)
            if track["kind"] == "video" and scale == 1 and not clip.get("rotation"):
                geometry = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black"
            else:
                geometry = f"scale={max(2,int(w*scale)//2*2)}:-2"
            vf = f"[{input_index}:v]setpts=(PTS-STARTPTS)/{speed:.9f},fps={fps},setsar=1,{geometry},format=rgba"
            effect = clip.get("effect", "none")
            vf += {"warm": ",colorbalance=rs=.12:bs=-.08", "cool": ",colorbalance=rs=-.08:bs=.12",
                   "mono": ",hue=s=0", "blur": ",gblur=sigma=3"}.get(effect, "")
            rotation = clip.get("rotation", 0)
            if rotation:
                angle = rotation*math.pi/180
                vf += f",rotate={angle:.9f}:ow=rotw({angle:.9f}):oh=roth({angle:.9f}):c=none"
            opacity = clip.get("opacity", 1)
            vf += f",colorchannelmixer=aa={opacity}"
            transition = clip.get("transition", "none")
            td = min(dur, clip.get("transition_duration", .3))
            if transition == "fade" and td > 0:
                vf += f",fade=t=in:st=0:d={td}:alpha=1"
            if transition == "wipe" and td > 0:
                vf += f",geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='alpha(X,Y)*lte(X,W*min(1,T/{td}))'"
            vf += f",setpts=PTS+{start}/TB[overlay{idx}]"
            filters.append(vf)
            x, y = clip.get("x", 0)*w, clip.get("y", 0)*h
            xexpr = f"'{x}+(W+w)*max(0,1-(t-{start})/{td})'" if transition == "slide" and td > 0 else str(x)
            filters.append(f"[{base}][overlay{idx}]overlay=x={xexpr}:y={y}:enable='gte(t,{start})*lt(t,{start+dur})':eof_action=pass:repeatlast=0[v{idx}]")
            base = f"v{idx}"
            overlay_index += 1
        if a["has_audio"] and not track.get("muted") and not clip.get("muted"):
            label = f"aud{input_index}"
            fi, fo = min(dur, clip.get("fade_in", 0)), min(dur, clip.get("fade_out", 0))
            af = f"[{input_index}:a]asetpts=PTS-STARTPTS,{tempo_filters(speed)},aresample=44100,aformat=channel_layouts=stereo,volume={clip.get('volume',1)}"
            if fi:
                af += f",afade=t=in:st=0:d={fi}"
            if fo:
                af += f",afade=t=out:st={dur-fo}:d={fo}"
            af += f",apad,atrim=duration={dur},adelay={round(start*44100)}S:all=1[{label}]"
            filters.append(af)
            audio_labels.append(f"[{label}]")
        input_index += 1
    if any(t["kind"] == "subtitle" and t["clips"] and not t.get("hidden") for t in timeline["tracks"]):
        subtitles = write_subtitles(project, timeline, work)
        filters.append(f"[{base}]subtitles=filename='{escape_filter_path(subtitles)}'[subtitled]")
        base = "subtitled"
    if post.get("progress"):
        # Keep the full picture visible; reserve a bottom band instead of covering it.
        filters += [f"[{base}]scale={w}:{h-6},pad={w}:{h}:0:0:color=black[reserved]",
                    f"color=c=0x58d5b0:s={w}x6:r={fps}:d={duration}[bar]",
                    f"[reserved][bar]overlay=x='-W+W*t/{duration}':y=H-6:shortest=1[progress]"]
        base = "progress"
    filters.append(f"{''.join(audio_labels)}amix=inputs={len(audio_labels)}:duration=longest:normalize=0,alimiter=limit=.95:level=disabled,atrim=duration={duration}[audio]")
    graph = work / "render.filter"
    graph.write_text(";\n".join(filters), encoding="utf-8")
    # Generic file option for FFmpeg 7+, legacy option selected by shared helper.
    from ..engine.scripts.ffmpeg_graph import _script_option
    partial = output.with_suffix(".partial.mp4")
    command += [_script_option(), str(graph), "-map", f"[{base}]", "-map", "[audio]",
                "-c:v", "libx264", "-threads", "2", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", project.get("audio_bitrate", "192k"), "-ar", "44100",
                "-t", str(duration), "-movflags", "+faststart", "-progress", "pipe:1", str(partial)]
    return command, partial, duration


def render(project, output: Path, progress=lambda value: None, process_started=lambda process: None):
    command, partial, duration = build_command(project, output)
    log = partial.with_suffix(".log")
    process = None
    try:
        with log.open("w", encoding="utf-8") as errors:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors, text=True,
                                       encoding="utf-8", errors="replace",
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            process_started(process)
            for line in process.stdout:
                if line.startswith("out_time_us="):
                    try:
                        value = int(line.strip().partition("=")[2])
                    except ValueError:
                        continue
                    progress(min(98, max(0, value / 1_000_000 / duration * 100)))
            if process.wait() != 0:
                raise RuntimeError(log.read_text(encoding="utf-8")[-2000:] or "FFmpeg 导出失败")
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(partial)],
                               capture_output=True, encoding="utf-8", timeout=30,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if probe.returncode:
            raise RuntimeError("输出文件检查失败")
        data = json.loads(probe.stdout)
        types = {s["codec_type"] for s in data["streams"]}
        if not {"audio", "video"} <= types or abs(float(data["format"]["duration"]) - duration) > .15:
            raise RuntimeError("导出音视频轨或时长异常")
        progress(99)
        if output.exists():
            raise RuntimeError("输出文件已存在，拒绝覆盖")
        os.replace(partial, output)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if process is not None and process.stdout is not None:
            process.stdout.close()
        partial.unlink(missing_ok=True)
