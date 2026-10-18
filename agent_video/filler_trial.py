"""Render only the retained ranges of one explicitly selected S4 candidate."""
import json
import re
import secrets
import threading
from pathlib import Path

from .pipeline.render import build_segments, build_virtual_segments, render_segment
from .filler_audio import refine_cleanup, verify_export
from .filler_rules import load_words


class FillerTrials:
    def __init__(self, app):
        self.app = app
        self.root = app.data_dir / 'filler_trials'
        self.lock = threading.Lock()
        self.active = None

    def folder(self, token):
        if not re.fullmatch(r'[a-f0-9]{24}', token):
            raise ValueError('无效的口头词预览 ID')
        return self.root / token

    def read(self, token):
        file = self.folder(token) / 'status.json'
        if not file.is_file():
            raise ValueError('口头词预览不存在')
        state = json.loads(file.read_text(encoding='utf-8'))
        if state['status'] == 'running' and self.active != token:
            state.update(status='failed', error='服务已重启，请重新渲染')
        return state

    def start(self, payload):
        if not payload.get('job_id') or 'candidate_id' not in payload:
            raise ValueError('请选择任务中的指定语句')
        result = self.app.test_filler_rules(payload)
        row = result['rows'][0]
        if payload.get('revision') != result['revision']:
            raise ValueError('规则已变化，请重新测试后再渲染')
        if row['status'] != 'cleaned':
            raise ValueError('该语句无法精确清理：' + row['reason'])
        job = self.app.store.get_job(str(payload['job_id']))
        workspace = Path(job['workspace'])
        virtual_file = workspace / 'virtual_timeline.json'
        virtual = None
        if virtual_file.is_file():
            from .timeline import load_virtual_timeline
            virtual = load_virtual_timeline(virtual_file)
        # Map each retained part independently, never the original encompassing range.
        groups = []
        for part in row['retained_parts']:
            candidate = {**part, 'id': row['id']}
            segments = (build_virtual_segments([candidate], virtual) if virtual
                        else build_segments([candidate]))
            for segment in segments:
                if not Path(segment.get('source_path') or job['source_path']).is_file():
                    raise ValueError('原素材不存在，请先恢复素材路径')
            groups.append(segments)
        if not self.lock.acquire(blocking=False):
            raise ValueError('已有口头词片段正在渲染，请等待完成')
        token = secrets.token_hex(12)
        state = {'id': token, 'job_id': job['id'], 'candidate_id': row['id'],
                 'status': 'running', 'parts': [], 'total': len(groups)}
        folder = self.folder(token)

        def save():
            temporary = folder / 'status.tmp'
            temporary.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
            temporary.replace(folder / 'status.json')

        def execute():
            try:
                def progress(message):
                    state['progress'] = message
                    save()
                report = refine_cleanup({'source_candidates': [
                    {k: row[k] for k in ('id', 'start', 'end', 'text')}], 'rows': [row]},
                    load_words(workspace, []), job['source_path'], virtual, progress=progress)
                checked = report['rows'][0]
                state['audio_verification'] = checked['audio_verification']
                if checked['status'] != 'cleaned':
                    raise ValueError(checked['reason'])
                state['progress'] = '切点复核通过，正在生成指定语句片段'
                save()
                refined_groups = []
                for part in checked['retained_parts']:
                    candidate = {**part, 'id': row['id']}
                    refined_groups.append(build_virtual_segments([candidate], virtual) if virtual
                                          else build_segments([candidate]))
                for index, segments in enumerate(refined_groups):
                    name = f'part-{index + 1}.mp4'
                    render_segment(job['source_path'], segments, str(folder / name),
                                   str(folder), index=index, preset='fast')
                    part = checked['retained_parts'][index]
                    actual = verify_export(folder / name, part['text'])
                    state['parts'].append({**part, 'name': name, 'verified_text': actual})
                    save()
                state['status'] = 'completed'
                state['progress'] = '指定语句片段已生成，导出音频复核通过'
                save()
            except Exception as error:
                state.update(status='failed', error=str(error))
                save()
            finally:
                self.active = None
                self.lock.release()

        try:
            folder.mkdir(parents=True)
            self.active = token
            save()
            threading.Thread(target=execute, daemon=True).start()
        except Exception:
            self.active = None
            self.lock.release()
            raise
        return {'id': token, 'status': 'running'}

    def media(self, token, name):
        if not re.fullmatch(r'part-[1-9]\d*\.mp4', name):
            raise ValueError('无效的片段视频')
        if self.read(token)['status'] != 'completed':
            raise ValueError('片段尚未通过音频复核')
        file = self.folder(token) / name
        if not file.is_file():
            raise ValueError('视频尚未生成')
        return file
