"""Content checks for generated background music, using the existing FFmpeg runtime."""
import array
import math
import os
import subprocess
import sys


def analyze_generated_audio(path, expected_duration):
    rate, channels, window = 8000, 2, .5
    result = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-t", "181",
                             "-ar", str(rate), "-ac", str(channels), "-f", "f32le", "-"],
                            capture_output=True, timeout=60,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise ValueError("生成音频解码失败")
    samples = array.array("f")
    samples.frombytes(result.stdout)
    if sys.byteorder != "little":
        samples.byteswap()
    size = int(rate*channels*window)
    levels = []
    lengths = []
    for start in range(0, len(samples), size):
        block = samples[start:start+size]
        if any(not math.isfinite(x) for x in block):
            raise ValueError("生成音频包含无效采样")
        # Measure channels separately to avoid cancelling opposite-phase stereo.
        rms = max(math.sqrt(sum(x*x for x in block[c::channels])/max(1,len(block[c::channels]))) for c in range(channels))
        levels.append(20*math.log10(max(1e-12,rms)))
        lengths.append(len(block)/(rate*channels))
    duration = len(samples)/(rate*channels)
    threshold = max(-55, max(levels, default=-240)-35)
    quiet = [db<threshold for db in levels]
    tail, run, longest = 0, 0, 0
    for is_quiet, length in zip(quiet, lengths):
        run = run+length if is_quiet else 0
        longest = max(longest,run)
    for is_quiet, length in zip(reversed(quiet), reversed(lengths)):
        if not is_quiet:
            break
        tail += length
    error = ""
    if not levels or all(quiet):
        error = "生成音频接近全程静音，请重新生成"
    elif abs(duration-expected_duration)>.5:
        error = f"生成音频时长异常：需要 {expected_duration:g} 秒，实际 {duration:.2f} 秒"
    elif tail>=2:
        error = f"生成音频末尾 {tail:.1f} 秒接近静音，请重新生成"
    elif longest>=3:
        error = f"生成音频有连续 {longest:.1f} 秒接近静音，请重新生成"
    return {"duration": round(duration,3), "window_seconds": window,
            "rms_db": [round(db,2) for db in levels], "threshold_db": round(threshold,2),
            "quiet_tail_seconds": round(tail,3), "longest_quiet_seconds": round(longest,3),
            "error": error}
