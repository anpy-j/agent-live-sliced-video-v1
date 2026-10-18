"""Conservative acoustic pause boundary calibration. Never expands selected ranges."""
from __future__ import annotations

import copy
import math
import re
import subprocess

from .filler_rules import clips_digest

OPTIONS = {"noise_db": -38, "min_pause": .12, "keep_pause": .08, "max_trim": .6, "word_tolerance": .12}


def pauses(media, start, end):
    result = subprocess.run([
        "ffmpeg", "-hide_banner", "-nostdin", "-ss", str(start), "-t", str(end-start),
        "-i", str(media), "-vn", "-af",
        f"silencedetect=noise={OPTIONS['noise_db']}dB:d={OPTIONS['min_pause']}",
        "-f", "null", "-"], capture_output=True, text=True, errors="replace", timeout=90)
    if result.returncode:
        raise ValueError("气口音频分析失败：" + result.stderr[-500:])
    intervals, pending = [], None
    for kind, raw in re.findall(r"silence_(start|end):\s*([\d.eE+-]+)", result.stderr):
        value = max(0., min(end-start, float(raw)))
        if kind == "start":
            pending = value
        elif pending is not None:
            intervals.append((start+pending, start+value)); pending = None
    if pending is not None:
        intervals.append((start+pending, end))
    return intervals


def calibrate(clips, media, virtual_timeline=None, progress=None, words=None):
    """Only trim edge pauses, leave interior pauses and continuous speech untouched.

    Analyse bounded edge windows, including virtual timeline source/speed mapping.
    Low-energy detection is a pause candidate, not a literal inhalation classifier.
    """
    from .pipeline.render import build_virtual_segments
    cleaned, rows = [], []
    for index, before in enumerate(clips):
        start, end = float(before['start']), float(before['end'])
        if not all(math.isfinite(t) for t in (start, end)) or end <= start:
            raise ValueError("气口检测片段区间无效")
        spoken = [(float(w['s']), float(w['e'])) for w in (words or [])
                  if isinstance(w, dict) and isinstance(w.get('s'), (int,float))
                  and isinstance(w.get('e'), (int,float)) and w['e'] > w['s']
                  and w['e'] > start and w['s'] < end]
        found = []
        for a, b in [(start, min(end, start+1.0)), (max(start, end-1.0), end)] if spoken else []:
            window = {**before, 'start': a, 'end': b}
            pieces = build_virtual_segments([window], virtual_timeline) if virtual_timeline else [
                {'source_path': media, 'source_start': a, 'source_end': b,
                 'timeline_start': a, 'speed': 1}]
            for piece in pieces:
                source_start, source_end = piece['source_start'], piece['source_end']
                speed = float(piece.get('speed', 1))
                offset = piece['timeline_start']
                found.extend((offset+(x-source_start)/speed, offset+(y-source_start)/speed)
                             for x, y in pauses(piece['source_path'], source_start, source_end))
        new_start, new_end = start, end
        for a, b in found:
            # A pause must touch the boundary and end inside the analysis window.
            if a <= start+.025 and b < min(end, start+1.)-.025:
                new_start = max(new_start, min(start+OPTIONS['max_trim'], b-OPTIONS['keep_pause']))
            if b >= end-.025 and a > max(start, end-1.)+.025:
                new_end = min(new_end, max(end-OPTIONS['max_trim'], a+OPTIONS['keep_pause']))
        if spoken:
            # ASR word edges are estimates. Permit at most 120ms acoustic correction
            # into the edge token, while preserving its body and all interior words.
            first = min(spoken)
            last = max(spoken, key=lambda w: w[1])
            new_start = min(new_start, max(start, min(first[0]+OPTIONS['word_tolerance'], first[1]-.08)))
            new_end = max(new_end, min(end, max(last[1]-OPTIONS['word_tolerance'], last[0]+.08)))
        if new_end-new_start < .3:
            new_start, new_end = start, end
        new_start = max(start, round(new_start, 3)) if new_start-start >= .015 else start
        new_end = min(end, round(new_end, 3)) if end-new_end >= .015 else end
        after = {**copy.deepcopy(before), 'start': new_start, 'end': new_end}
        cleaned.append(after)
        changed = (after['start'], after['end']) != (before['start'], before['end'])
        rows.append({'id': before['id'], 'text': before.get('text', ''),
                     'start': start, 'end': end, 'new_start': after['start'], 'new_end': after['end'],
                     'removed_seconds': round(end-start-(after['end']-after['start']), 3),
                     'status': 'adjusted' if changed else 'unchanged',
                     'reason': '边缘停顿校准，保留 80ms 自然停顿' if changed else ('没有可靠边缘气口，保留原切点' if spoken else '缺少词级时间戳保护，保留原切点')})
        if progress:
            progress(index+1, len(clips))
    return {'version': 1, 'options': dict(OPTIONS), 'source_candidates': copy.deepcopy(clips),
            'cleaned_candidates': cleaned, 'cleaned_digest': clips_digest(cleaned), 'rows': rows,
            'total_seconds': round(sum(c['end']-c['start'] for c in cleaned), 3)}


def restore(report, clips):
    if report is None:
        return clips
    cleaned = report.get('cleaned_candidates')
    if (report.get('version') != 1 or report.get('source_candidates') != clips
            or not isinstance(cleaned, list) or len(cleaned) != len(clips)
            or report.get('cleaned_digest') != clips_digest(cleaned)):
        raise ValueError('气口检测快照失配，请重新执行 S4')
    for before, after in zip(clips, cleaned):
        if ({k:v for k,v in before.items() if k not in ('start','end')} !=
                {k:v for k,v in after.items() if k not in ('start','end')}
                or not before['start'] <= after['start'] < after['end'] <= before['end']):
            raise ValueError('气口检测切点无效，请重新执行 S4')
    return cleaned
