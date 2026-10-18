"""Isolated asynchronous video comparisons; production job files are read-only."""
import json
import re
import secrets
import threading
from pathlib import Path

from .breath import calibrate
from .pipeline.render import group_segments_for_export, render_video


class BreathTrials:
    def __init__(self, app):
        self.app = app
        self.root = app.data_dir / 'breath_trials'
        self.lock = threading.Lock()
        self.active = None

    def folder(self, token):
        if not re.fullmatch(r'[a-f0-9]{24}', token):
            raise ValueError('无效的气口测试 ID')
        return self.root / token

    def read(self, token):
        file = self.folder(token) / 'status.json'
        if not file.is_file():
            raise ValueError('气口测试不存在')
        data = json.loads(file.read_text(encoding='utf-8'))
        if data['status'] == 'running' and self.active != token:
            data.update(status='failed', error='服务已重启，请重新测试')
        return data

    def start(self, payload):
        job = self.app.store.get_job(str(payload.get('job_id') or ''))
        if (not job or job['status'] != 'completed' or job.get('pipeline_version', 'v1') != 'v1'
                or job.get('job_type') == 'remix'):
            raise ValueError('请选择已完成的 V1 任务')
        manifest_file = Path(job['workspace']) / 'manifest.json'
        if not manifest_file.is_file():
            raise ValueError('任务缺少最终片段清单，请重新渲染任务')
        manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
        segments = manifest.get('segments')
        if not segments:
            raise ValueError('任务没有可测试的视频片段')
        source = job['source_path']
        for segment in segments:
            if not Path(segment.get('source_path') or source).is_file():
                raise ValueError('原素材不存在，请先恢复素材路径')
        if not self.lock.acquire(blocking=False):
            raise ValueError('已有气口测试正在执行，请等待完成')
        try:
            return self._launch(job, segments, source)
        except Exception:
            self.active = None
            self.lock.release()
            raise

    def _launch(self, job, segments, source):
        token = secrets.token_hex(12)
        folder = self.folder(token)
        folder.mkdir(parents=True)
        state = {'id': token, 'job_id': job['id'], 'title': job['title'], 'status': 'running',
                 'progress': '准备分析全部片段', 'pairs': []}
        def save():
            temporary = folder / 'status.tmp'
            temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(folder / 'status.json')
        save()
        self.active = token
        def execute():
            try:
                from .filler_rules import load_words
                words = load_words(Path(job["workspace"]), [])
                adjusted, rows = [], []
                for i, seg in enumerate(segments):
                    state['progress'] = f'分析音频 {i+1} / {len(segments)}'; save()
                    clip = {**seg, 'start': seg.get('source_start', seg['start']),
                            'end': seg.get('source_end', seg['end'])}
                    virtual = None
                    source_start = clip['start']
                    speed = float(seg.get('speed', 1))
                    if 'timeline_start' in seg:
                        from .timeline import VirtualTimeline, TimelineSegment
                        virtual = VirtualTimeline('breath-trial', segments=[TimelineSegment(
                            str(seg.get('segment_id', i)), seg['timeline_start'], seg['timeline_end'],
                            seg.get('source_path') or source, clip['start'], clip['end'], speed)])
                        clip.update(start=seg['timeline_start'], end=seg['timeline_end'])
                    report = calibrate([clip], seg.get('source_path') or source, virtual, words=words)
                    after = report['cleaned_candidates'][0]
                    a, b = after['start'], after['end']
                    if virtual:
                        a = source_start + (a-clip['start'])*speed
                        b = source_start + (b-clip['start'])*speed
                    adjusted.append({**seg, 'start': a, 'end': b, 'source_start': a, 'source_end': b})
                    rows.extend(report['rows'])
                state.update(rows=rows, changed=sum(r['status']=='adjusted' for r in rows),
                             removed_seconds=round(sum(r['removed_seconds'] for r in rows),3))
                before_groups, after_groups = group_segments_for_export(segments), group_segments_for_export(adjusted)
                for i, (before, after) in enumerate(zip(before_groups, after_groups)):
                    state['progress'] = f'生成片段对比 {i+1} / {len(before_groups)}'; save()
                    for name, clips in [('before', before), ('after', after)]:
                        render_video(source, clips, str(folder/f'{name}-{i}.mp4'), str(folder), preset='fast')
                    state['pairs'].append({'index': i+1, 'text': before[0].get('text',''),
                                           'before': f'before-{i}.mp4', 'after': f'after-{i}.mp4'})
                    save()
                state['progress'] = '生成全部片段合集'; save()
                for name, clips in [('before', segments), ('after', adjusted)]:
                    render_video(source, clips, str(folder/f'{name}.mp4'), str(folder), preset='fast')
                state.update(status='completed', progress='对比视频已生成', before='before.mp4', after='after.mp4')
                save()
            except Exception as error:
                state.update(status='failed', error=str(error)); save()
            finally:
                self.active = None
                self.lock.release()
        threading.Thread(target=execute, daemon=True).start()
        return {'id': token, 'status': 'running'}

    def media(self, token, name):
        if not re.fullmatch(r'(before|after)(-\d+)?\.mp4', name):
            raise ValueError('无效的对比视频')
        file = self.folder(token) / name
        if not file.is_file():
            raise ValueError('视频尚未生成')
        return file
