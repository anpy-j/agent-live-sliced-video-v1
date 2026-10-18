"""Bounded, offline audio checks for every filler deletion; never apply a fixed offset."""
from __future__ import annotations

import copy
import math
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path

from pypinyin import lazy_pinyin

from .engine.scripts.prep import simp
from .filler_rules import clips_digest
from .pipeline.render import build_segments, build_virtual_segments

STEP = .02
MAX_ADJUSTMENT = .24
MIN_WORD_REMAINDER = .10
_lock = threading.RLock()
_recognizer = None


class BoundaryError(ValueError):
    def __init__(self, message, attempts):
        super().__init__(message)
        self.attempts = attempts


def sounds(text):
    # Preserve every spoken syllable; only punctuation and script variants are ignored.
    return tuple(lazy_pinyin(re.sub(r'[^\w\u3400-\u9fff]', '', simp(text))))


def get_recognizer():
    """Reuse a local model. No downloads or transcript hints during acceptance checks."""
    global _recognizer
    with _lock:
        if _recognizer is not None:
            return _recognizer
        configured = os.environ.get('LIVECUT_FILLER_VERIFY_MODEL')
        roots = [Path(os.environ[k]).expanduser() for k in ('MODELSCOPE_CACHE', 'HF_HOME')
                 if os.environ.get(k)]
        local = [Path(configured)] if configured else [
            root.parent / 'asr' / 'funasr-paraformer-zh' /
            'speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch'
            for root in roots]
        paraformer = next((p for p in local if (p / 'config.yaml').is_file()
                          and (p / 'model.pt').is_file()), None)
        if paraformer:
            from funasr import AutoModel
            model = AutoModel(model=str(paraformer), device='cpu', ncpu=4, disable_update=True)

            def recognize(path):
                return ''.join(r.get('text', '') for r in model.generate(
                    input=str(path), disable_pbar=True))
        else:
            from faster_whisper import WhisperModel
            from .engine.scripts.asr_backend import resolve_snapshot
            model = WhisperModel(resolve_snapshot(configured or os.environ.get('DOUYIN_WHISPER_MODEL')
                                                   or 'small'), device='cpu', compute_type='int8',
                                 local_files_only=True, cpu_threads=4)

            def recognize(path):
                segments, _ = model.transcribe(str(path), language='zh', beam_size=5,
                                               vad_filter=False, condition_on_previous_text=False)
                return ''.join(s.text for s in segments)
        _recognizer = recognize
        return recognize


def _run(command):
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                            errors='replace', timeout=90,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if result.returncode:
        raise ValueError('口头词音频复核提取失败：' + result.stderr[-400:])


def extract_audio(part, media, virtual, destination):
    """Decode just this retained range on the same source/speed clock as the renderer."""
    from .engine.scripts.render_multi import (audio_filter, input_window, source_fps,
                                               segment_frame_counts)
    candidate = {**part, 'id': 0}
    segments = (build_virtual_segments([candidate], virtual) if virtual
                else build_segments([candidate]))
    fps = source_fps(segments[0].get('source_path') or media)
    frame_counts = segment_frame_counts(segments, fps)
    command = ['ffmpeg', '-y', '-v', 'error', '-filter_complex_threads', '1']
    filters = []
    for index, segment in enumerate(segments):
        start, end = segment['source_start'], segment['source_end']
        seek, duration, trim, raw = input_window(start, end)
        speed = float(segment.get('speed', 1))
        command += ['-threads', '1', '-ss', str(seek), '-t', str(duration),
                    '-i', str(segment.get('source_path') or media)]
        output_duration = frame_counts[index] / fps
        filters.append(f'[{index}:a]' + audio_filter(output_duration, speed=speed,
                        edge=min(.012, output_duration / 4), trim_start=trim, raw_duration=raw)
                       + f'[a{index}]')
    labels = ''.join(f'[a{i}]' for i in range(len(segments)))
    filters.append(f'{labels}concat=n={len(segments)}:v=0:a=1[out]')
    encoded = destination.with_suffix('.m4a')
    # Acceptance must hear AAC with the renderer's frame rounding and fades. PCM-only
    # probes can pass while the encoded export still produces a residual syllable.
    command += ['-filter_complex', ';'.join(filters), '-map', '[out]', '-ar', '48000', '-ac', '2',
                '-c:a', 'aac', '-b:a', '256k', str(encoded)]
    _run(command)
    _run(['ffmpeg', '-y', '-v', 'error', '-i', str(encoded), '-ar', '16000', '-ac', '1',
          str(destination)])


def _extra_edges(actual, expected):
    """Only allow adjusting excess edge syllables; never repair missing/interior words by cutting."""
    for position in range(len(actual) - len(expected) + 1):
        if actual[position:position + len(expected)] == expected:
            return bool(position), position + len(expected) < len(actual)
    return None


def _allowance(part, words, side, changed):
    if not changed:
        return 0.
    # The edge word must retain at least 100 ms on the task clock. Missing boundaries
    # are not permission to trim an unknown amount of the next word.
    edge = part['start'] if side == 'start' else part['end']
    key = 's' if side == 'start' else 'e'
    tokens = [w for w in words if abs(float(w[key]) - edge) <= .002]
    if not tokens:
        return 0.
    token = tokens[0] if side == 'start' else tokens[-1]
    return max(0., min(MAX_ADJUSTMENT, float(token['e']) - float(token['s']) - MIN_WORD_REMAINDER,
                       part['end'] - part['start'] - .15))


def refine_part(part, source, words, media, virtual, recognize, directory):
    expected = sounds(part['text'])
    if not expected:
        raise ValueError('保留正文为空，无法复核')
    attempts = []
    current = copy.deepcopy(part)

    def check(proposal):
        path = directory / 'boundary.wav'
        extract_audio(proposal, media, virtual, path)
        actual = recognize(path)
        attempts.append({'start': proposal['start'], 'end': proposal['end'], 'text': actual})
        return _extra_edges(sounds(actual), expected)

    edges = check(current)
    if edges is None:
        raise BoundaryError('音频与保留正文不一致或正文缺字，保留原句', attempts)
    for side, edge_index in [('start', 0), ('end', 1)]:
        if not edges[edge_index]:
            continue
        allowance = _allowance(part, words, side,
                               part[side] != source[side])
        accepted = False
        for step in range(1, math.floor((allowance + .00001) / STEP) + 1):
            proposal = {**current, side: round(part[side] + step * STEP *
                                              (1 if side == 'start' else -1), 3)}
            if proposal['end'] - proposal['start'] < .15:
                break
            candidate_edges = check(proposal)
            if candidate_edges is None:
                # A rejected probe is never used. ASR can vary between nearby cuts;
                # keep searching only inside the same protected edge-word allowance.
                continue
            if (not candidate_edges[edge_index]
                    and not (candidate_edges[1 - edge_index] and not edges[1 - edge_index])):
                current, edges, accepted = proposal, candidate_edges, True
                break
        if not accepted:
            raise BoundaryError('残音无法在正文保护范围内清除，保留原句', attempts)
    if any(edges):
        raise BoundaryError('切点仍有多余语音，保留原句', attempts)
    return current, attempts


def refine_cleanup(report, words, media, virtual=None, *, recognize=None, progress=None):
    """Shared by V1 S4 and one-sentence trials. A failed row keeps its original audio/text."""
    result = copy.deepcopy(report)
    sources = {c['id']: c for c in result['source_candidates']}
    clips = []
    with _lock, tempfile.TemporaryDirectory(prefix='livecut-filler-audio-') as temp:
        recognizer_error = None
        for row in result['rows']:
            source = sources[row['id']]
            if row['status'] != 'cleaned':
                clips.append(copy.deepcopy(source))
                continue
            if progress:
                progress(f"正在复核语句 {row['id']} 的口头词切点")
            original_parts = copy.deepcopy(row['retained_parts'])
            try:
                if recognizer_error:
                    raise ValueError(recognizer_error)
                if recognize is None:
                    try:
                        recognize = get_recognizer()
                    except Exception as error:
                        recognizer_error = '本地音频复核模型不可用，请配置本地模型'
                        raise ValueError(recognizer_error) from error
                parts, checks = [], []
                for part in original_parts:
                    refined, attempts = refine_part(part, source, words, media, virtual,
                                                    recognize, Path(temp))
                    parts.append(refined)
                    checks.append(attempts)
                changed = parts != original_parts
                row['audio_verification'] = {'status': 'adjusted' if changed else 'verified',
                                             'original_parts': original_parts, 'checks': checks}
                for deletion in row['removed_spans']:
                    for before, after in zip(original_parts, parts):
                        if abs(deletion['start'] - before['end']) < .001:
                            deletion['start'] = after['end']
                        if abs(deletion['end'] - before['start']) < .001:
                            deletion['end'] = after['start']
                row.update(retained_parts=parts, cleaned_start=parts[0]['start'],
                           removed_seconds=round(source['end'] - source['start'] -
                                                 sum(p['end'] - p['start'] for p in parts), 3),
                           reason=row['reason'] + ('；音频复核后已校准切点' if changed else '；音频复核通过'))
                clips.extend({**source, **part} for part in parts)
            except Exception as error:
                row.update(status='skipped', reason=str(error), cleaned_text=source['text'],
                           audio_verification={'status': 'skipped', 'original_parts': original_parts,
                                               'failed_checks': getattr(error, 'attempts', [])})
                for key in ('retained_parts', 'removed_spans', 'cleaned_start', 'removed_seconds'):
                    row.pop(key, None)
                clips.append(copy.deepcopy(source))
    result.update(cleaned_candidates=clips, cleaned_digest=clips_digest(clips),
                  total_seconds=round(sum(c['end'] - c['start'] for c in clips), 3),
                  audio_verification={'version': 1, 'step_seconds': STEP,
                                      'max_adjustment_seconds': MAX_ADJUSTMENT})
    return result


def verify_export(path, text, *, recognize=None):
    """Check encoded audio too: a PCM probe alone cannot certify the final export."""
    with _lock, tempfile.TemporaryDirectory(prefix='livecut-filler-export-') as temp:
        wav = Path(temp) / 'export.wav'
        _run(['ffmpeg', '-y', '-v', 'error', '-i', str(path), '-vn', '-ar', '16000',
              '-ac', '1', str(wav)])
        actual = (recognize or get_recognizer())(wav)
        if sounds(actual) != sounds(text):
            raise ValueError('导出音频复核未通过（残音或正文不一致），请人工检查切点')
        return actual
