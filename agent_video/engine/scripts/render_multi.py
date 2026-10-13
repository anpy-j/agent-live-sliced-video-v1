# -*- coding: utf-8 -*-
"""Render a single- or multi-source timeline with uniform output parameters.

Usage:
  python render_multi.py timeline.json output.mp4 --src 1=a.mp4 --src 2=b.mp4

默认值即账号成片标准：竖屏源保留原分辨率并封顶 1440x2560、源帧率、
H.264 CRF16 preset slow、AAC 256k、响度目标 -6.5 LUFS（单遍 loudnorm 实测
落在 -7.3~-7.8，与素材库现有成片一致）。
"""
import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
from fractions import Fraction

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ffmpeg_graph import filter_complex_args  # noqa: E402
try:
    from .render_resources import admit_render, run_render, render_batch_size, snapshot
except ImportError:  # Direct script execution by the render subprocess.
    from render_resources import admit_render, run_render, render_batch_size, snapshot  # noqa: E402

SEEK_PREROLL_SECONDS = 5.0


def run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                            errors="replace")
    if result.returncode:
        raise RuntimeError(result.stderr[-4000:])
    return result.stdout


def parse_sources(values):
    result = {}
    for value in values:
        key, sep, path = value.partition("=")
        if not sep or not key.isdigit() or not os.path.isfile(path):
            raise SystemExit(f"Invalid --src: {value!r}; expected ID=existing-media")
        result[int(key)] = os.path.abspath(path)
    return result


def source_video_stream(path):
    """Return the first video stream without CSV duplication from MPEG-TS programs."""
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
               "-show_entries", "stream=avg_frame_rate,r_frame_rate,width,height",
               "-of", "json", path])
    try:
        streams = json.loads(out).get("streams") or []
        return streams[0]
    except (AttributeError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"ffprobe returned no usable video stream for {path!r}") from exc


def source_fps(path):
    stream = source_video_stream(path)
    for field in ("avg_frame_rate", "r_frame_rate"):
        try:
            fps = float(Fraction(str(stream.get(field, "0/0"))))
        except (ValueError, ZeroDivisionError):
            continue
        if math.isfinite(fps) and fps > 0:
            return fps
    raise RuntimeError(f"ffprobe returned no usable frame rate for {path!r}")


def source_size(path):
    stream = source_video_stream(path)
    try:
        width, height = int(stream["width"]), int(stream["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"ffprobe returned no usable video size for {path!r}") from exc
    if width <= 0 or height <= 0:
        raise RuntimeError(f"ffprobe returned invalid video size {width}x{height} for {path!r}")
    return width, height


def output_size(path, width, height):
    if (width is None) != (height is None):
        raise SystemExit("Provide both --width and --height, or neither")
    if width is not None:
        return width, height
    src_w, src_h = source_size(path)
    if src_h >= src_w:
        # 封顶 1440x2560（账号成片标准）；旧值 1080x1920 会把 1440x2542 的源缩到
        # 1080x1906，出来比标准窄一圈。
        ratio = min(1.0, 1440 / src_w, 2560 / src_h)
        return max(2, int(src_w * ratio) // 2 * 2), max(2, int(src_h * ratio) // 2 * 2)
    return 1440, 2560


def video_filter(width, height, fps, allow_upscale, frame_count, speed=1.0,
                 trim_start=0.0, raw_duration=None):
    if allow_upscale:
        scale = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,")
    else:
        ratio = f"min(1\\,min({width}/iw\\,{height}/ih))"
        scale = (f"scale=w='trunc(iw*{ratio}/2)*2':"
                 f"h='trunc(ih*{ratio}/2)*2',")
    source_trim = (f"trim=start={trim_start:.6f}:duration={raw_duration:.6f},"
                   if raw_duration is not None else "")
    if raw_duration is not None:
        # Use the requested cut as the shared origin. STARTPTS would independently
        # zero video/audio and permanently erase their source timestamp offset.
        pts = f"setpts=(PTS-{trim_start:.6f}/TB)/{speed:.6f}"
    else:
        pts = (f"setpts=(PTS-STARTPTS)/{speed:.6f}"
               if abs(speed - 1.0) > 1e-4 else "setpts=PTS-STARTPTS")
    return (source_trim + scale + f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"{pts},fps={fps:.6f}:start_time=0,trim=end_frame={frame_count},"
            f"setpts=N/({fps:.6f}*TB),setsar=1")


def audio_filter(duration, speed=1.0, edge=0.0, trim_start=0.0,
                 raw_duration=None):
    """Keep audio on the exact same CFR clock as its corresponding video piece."""
    source_trim = (f"atrim=start={trim_start:.6f}:duration={raw_duration:.6f},"
                   if raw_duration is not None else "")
    tempo = f"atempo={speed:.6f}," if abs(speed - 1.0) > 1e-4 else ""
    fades = (f",afade=t=in:st=0:d={edge:.4f},"
             f"afade=t=out:st={max(0.0, duration - edge):.4f}:d={edge:.4f}") if edge else ""
    origin = (f"asetpts=PTS-{trim_start:.6f}/TB"
              if raw_duration is not None else "asetpts=PTS-STARTPTS")
    return (f"{source_trim}{origin},{tempo}"
            f"aresample=48000:first_pts=0,"
            f"aformat=sample_rates=48000:channel_layouts=stereo,"
            f"apad,atrim=duration={duration:.9f},asetpts=N/SR/TB{fades}")


def input_window(start, end, preroll=SEEK_PREROLL_SECONDS):
    """Seek before a cut, then trim in-filter so video is not lost to the next keyframe."""
    seek_start = max(0.0, start - preroll)
    return seek_start, end - seek_start, start - seek_start, end - start


def segment_frame_counts(timeline, fps):
    """Quantize cumulative cut positions so rounding cannot drift per segment."""
    counts = []
    cumulative_seconds = 0.0
    allocated_frames = 0
    for row in timeline:
        speed = float(row.get("speed", 1.0))
        if speed <= 0:
            raise SystemExit(f"Invalid segment speed: {speed}")
        cumulative_seconds += (float(row["end"]) - float(row["start"])) / speed
        cumulative_frames = round(cumulative_seconds * fps)
        frame_count = max(1, cumulative_frames - allocated_frames)
        counts.append(frame_count)
        allocated_frames += frame_count
    return counts


def loudness_filter(args):
    return "anull" if args.no_loudnorm else \
        f"loudnorm=I={args.loudness}:TP=-1.0:LRA=8,alimiter=limit=0.95:level=disabled"


def render_chunk(timeline, sources, frame_counts, fps, args, destination,
                 budget, initial_state, journal_output, *, intermediate=False,
                 frame_log=None):
    command = ["ffmpeg", "-y", "-v", "error",
               "-filter_threads", str(budget.filter_threads),
               "-filter_complex_threads", str(budget.filter_threads)]
    input_windows = []
    for row in timeline:
        start, end = float(row["start"]), float(row["end"])
        if start < 0 or end <= start:
            raise SystemExit(f"Invalid segment bounds: {start}-{end}")
        seek_start, input_duration, trim_start, raw_duration = input_window(start, end)
        input_windows.append((trim_start, raw_duration))
        command += ["-threads", str(budget.decoder_threads),
                    "-ss", f"{seek_start:.6f}", "-t", f"{input_duration:.6f}",
                    "-i", sources[int(row.get("src", 1))]]

    if frame_log:
        command += ["-progress", frame_log, "-stats_period", "0.5", "-nostats"]
    filters = []
    for index in range(len(timeline)):
        speed = float(timeline[index].get("speed", 1.0))
        frame_count = frame_counts[index]
        duration = frame_count / fps
        trim_start, raw_duration = input_windows[index]
        filters.append(f"[{index}:v]{video_filter(args.width, args.height, fps, args.allow_upscale, frame_count, speed=speed, trim_start=trim_start, raw_duration=raw_duration)}"
                       f"[v{index}]")
        edge = max(0.0, min(args.audio_edge_ms / 1000.0, duration / 4.0))
        filters.append(
            f"[{index}:a]{audio_filter(duration, speed=speed, edge=edge, trim_start=trim_start, raw_duration=raw_duration)}[a{index}]"
        )
    labels = "".join(f"[v{i}][a{i}]" for i in range(len(timeline)))
    filters.append(f"{labels}concat=n={len(timeline)}:v=1:a=1[vcat][acat]")
    final_audio_filter = "anull" if intermediate else loudness_filter(args)
    filters.append(f"[acat]{final_audio_filter}[aout]")

    filter_args, filter_script = filter_complex_args(filters)
    command += [*filter_args, "-map", "[vcat]", "-map", "[aout]",
                "-c:v", "libx264", "-threads:v", str(budget.encoder_threads),
                "-threads:a", "1", "-crf", str(args.crf), "-preset", args.preset,
                "-pix_fmt", "yuv420p", "-r", f"{fps:.6f}",
                "-ar", "48000", "-ac", "2"]
    if intermediate:
        command += ["-c:a", "pcm_f32le", "-f", "nut", destination]
    else:
        command += ["-c:a", "aac", "-b:a", args.audio_bitrate,
                    "-movflags", "+faststart", destination]
    try:
        run_render(command, budget, initial_state, journal_output)
    finally:
        if os.path.exists(filter_script):
            os.unlink(filter_script)


def render_batched(timeline, sources, frame_counts, decoder_costs, fps, args,
                   destination, journal_output, frame_log):
    # Keep intermediates next to the output, avoiding limited system temp disks.
    # NUT preserves the video time base; PCM avoids AAC padding at every join.
    with tempfile.TemporaryDirectory(prefix=".render-batches-",
                                     dir=os.path.dirname(destination)) as folder:
        manifest = os.path.join(folder, "concat.txt")
        completed = 0
        batch_index = 0
        with open(manifest, "w", encoding="utf-8") as handle:
            handle.write("ffconcat version 1.0\n")
            while completed < len(timeline):
                count = render_batch_size(decoder_costs[completed:], snapshot(), max_segments=4)
                stop = completed + count
                budget, state = admit_render(sum(decoder_costs[completed:stop]))
                part_name = f"batch-{batch_index:04d}.nut"
                part = os.path.join(folder, part_name)
                print(f"[render-resources] 低内存分批渲染：片段 {completed + 1}~{stop}"
                      f" / {len(timeline)}", file=sys.stderr, flush=True)
                render_chunk(timeline[completed:stop], sources, frame_counts[completed:stop],
                             fps, args, part, budget, state, part, intermediate=True)
                duration = sum(frame_counts[completed:stop]) / fps
                handle.write(f"file '{part_name}'\nduration {duration:.12f}\n")
                completed = stop
                batch_index += 1
                if frame_log:
                    with open(frame_log, "a", encoding="utf-8") as progress:
                        progress.write(f"frame={sum(frame_counts[:completed])}\nprogress=continue\n")
        # Video is copied directly, retaining CRF/preset quality with no second
        # video encoding. Apply loudness and AAC encoding once across the whole film.
        budget, state = admit_render()
        duration = sum(frame_counts) / fps
        audio = f"asetpts=N/SR/TB,apad,atrim=duration={duration:.9f},{loudness_filter(args)}"
        command = ["ffmpeg", "-y", "-v", "error", "-threads", "1",
                   "-filter_threads", "1", "-f", "concat", "-safe", "0", "-i", manifest,
                   "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy", "-af", audio,
                   "-c:a", "aac", "-threads:a", "1", "-b:a", args.audio_bitrate,
                   "-ar", "48000", "-ac", "2", "-t", f"{duration:.9f}",
                   "-movflags", "+faststart", destination]
        print("[render-resources] 正在拼接成片并处理音频", file=sys.stderr, flush=True)
        run_render(command, budget, state, journal_output)
        if frame_log:
            with open(frame_log, "a", encoding="utf-8") as progress:
                progress.write(f"frame={sum(frame_counts)}\nprogress=end\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("timeline")
    parser.add_argument("output")
    parser.add_argument("--src", action="append", default=[])
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument("--fps", type=float)
    parser.add_argument("--crf", type=int, default=16)
    parser.add_argument("--preset", default="slow")
    parser.add_argument("--audio-bitrate", default="256k")
    parser.add_argument("--no-loudnorm", action="store_true")
    parser.add_argument("--loudness", type=float, default=-6.5)
    parser.add_argument("--audio-edge-ms", type=float, default=12.0)
    parser.add_argument("--allow-upscale", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--progress-file")
    args = parser.parse_args()
    if os.path.exists(args.output) and not args.force:
        raise SystemExit(f"Output exists; use --force to replace: {args.output}")

    timeline = json.load(open(args.timeline, encoding="utf-8"))
    sources = parse_sources(args.src)
    if not timeline:
        raise SystemExit("Timeline is empty")
    missing = sorted({int(row.get("src", 1)) for row in timeline} - set(sources))
    if missing:
        raise SystemExit(f"Missing --src mappings: {missing}")
    fps = args.fps or source_fps(sources[int(timeline[0].get("src", 1))])
    args.width, args.height = output_size(
        sources[int(timeline[0].get("src", 1))], args.width, args.height)

    for row in timeline:
        start, end = float(row["start"]), float(row["end"])
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise SystemExit(f"Invalid segment bounds: {start}-{end}")
    sizes = {key: source_size(path) for key, path in sources.items()}
    decoder_costs = [max(64 * 1024 ** 2,
                         sizes[int(row.get("src", 1))][0] *
                         sizes[int(row.get("src", 1))][1] * 4 * 8)
                     for row in timeline]
    frame_counts = segment_frame_counts(timeline, fps)
    frame_log = None
    if args.progress_file:
        progress_file = os.path.abspath(args.progress_file)
        frame_log = progress_file + ".ffmpeg"
        with open(frame_log, "w", encoding="utf-8"):
            pass
        frame_ends, cumulative = [], 0
        for count in frame_counts:
            cumulative += count
            frame_ends.append(cumulative)
        temporary = progress_file + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump({"total": len(frame_counts), "frame_ends": frame_ends}, handle)
        os.replace(temporary, progress_file)
    final_output = os.path.abspath(args.output)
    partial_output = final_output + ".partial.mp4"
    try:
        if render_batch_size(decoder_costs, snapshot()) < len(timeline):
            render_batched(timeline, sources, frame_counts, decoder_costs, fps, args,
                           partial_output, final_output, frame_log)
        else:
            budget, state = admit_render(sum(decoder_costs))
            render_chunk(timeline, sources, frame_counts, fps, args, partial_output,
                         budget, state, final_output, frame_log=frame_log)
        os.replace(partial_output, final_output)
    finally:
        if os.path.exists(partial_output):
            os.unlink(partial_output)
    print(f"rendered {len(timeline)} segments -> {args.output}")


if __name__ == "__main__":
    # Parent processes decode these pipes as UTF-8, including Chinese diagnostics.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    main()
