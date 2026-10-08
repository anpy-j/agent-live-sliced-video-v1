"""Outbound HTTP relay; optional and disabled unless configured."""
import json
import logging
import os
import sqlite3
import threading
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)


class RemoteConnector:
    def __init__(self, app, url, device_id):
        if not url.startswith(('http://', 'https://')):
            raise ValueError('LIVE_CUT_REMOTE_URL 必须使用 HTTP 或 HTTPS')
        self.app, self.url, self.device_id = app, url.rstrip('/'), device_id
        self.stopping = threading.Event()
        self.path = Path(app.root) / 'data' / 'remote_commands.db'
        self.models = {}
        self._model_time = 0
        with self.journal() as con:
            con.execute('CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, status TEXT, result TEXT, acknowledged INTEGER DEFAULT 0)')
            con.execute("UPDATE receipts SET status='unknown', result=? WHERE status='running'",
                        (json.dumps({'message': '电脑重启，操作结果未知，请核对任务'}),))

    @classmethod
    def from_environment(cls, app):
        url = os.getenv('LIVE_CUT_REMOTE_URL', '')
        if not url:
            return None
        device = os.getenv('LIVE_CUT_DEVICE_ID', '').strip()
        if not device:
            raise ValueError('请配置 LIVE_CUT_DEVICE_ID')
        return cls(app, url, device)

    @contextmanager
    def journal(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path, timeout=10)
        try:
            with con:
                yield con
        finally:
            con.close()

    def start(self):
        self.thread = threading.Thread(target=self.run, name='clipping-relay', daemon=True)
        self.thread.start()

    def stop(self):
        self.stopping.set()
        self.thread.join(timeout=15)

    def snapshot(self):
        if time.time()-self._model_time > 60:
            self.models = self.app.settings()['ai_models']
            self._model_time = time.time()
        jobs = []
        for item in self.app.store.list_jobs()[:100]:
            j = self.app.store.get_job(item['id'])
            if not j:
                continue
            public = {k: j.get(k) for k in ('id', 'title', 'status', 'current_stage', 'progress', 'error', 'created_at', 'updated_at')}
            public['stages'] = [{k: s.get(k) for k in ('stage_id', 'name', 'status', 'progress', 'message')} for s in j.get('stages', [])]
            public['events'] = [{k: e.get(k) for k in ('created_at', 'level', 'message')} for e in j.get('events', [])[:20]]
            public['model'] = json.loads(j.get('remote_model_json') or '{}')
            public['workflow'] = 'legacy'
            jobs.append(public)
        return {'name': os.getenv('LIVE_CUT_DEVICE_NAME', self.device_id), 'jobs': jobs,
                'models': self.models, 'capabilities': ['pause', 'resume', 'retry', 'set-model'],
                'default_model': {k: self.app.store.get_setting(k) for k in ('ai_engine', 'ai_provider', 'ai_model')}}

    def execute(self, command):
        runner, store = self.app.runner, self.app.store
        with runner.control_lock:
            job_id = command['job_id']
            job = store.get_job(job_id)
            if not job:
                raise ValueError('任务不存在')
            action = command['action']
            if action == 'pause':
                runner.pause(job_id)
            elif action == 'resume':
                runner.resume(job_id)
            elif action == 'retry':
                if job['status'] not in {'failed', 'cancelled', 'paused'}:
                    raise ValueError('请先暂停，或选择失败/取消的任务')
                if runner._current == job_id:
                    raise ValueError('上一次执行尚未退出，请稍后重试')
                runner._pause_requested.discard(job_id)
                runner.retry(job_id)
            elif action == 'set-model':
                if job['status'] not in {'paused', 'failed', 'cancelled'}:
                    raise ValueError('请先暂停任务再切换模型')
                provider, model = command['provider'], command['model']
                if model not in {m['id'] for m in self.models.get(provider, [])}:
                    raise ValueError('模型不在电脑的模型列表中')
                # Recalculate AI judgments and their dependents with the new model.
                settings = {'ai_engine': 'llm', 'ai_provider': provider, 'ai_model': model}
                workspace = Path(job['workspace'])
                stage = 'judge' if (workspace/'clauses.filtered.json').is_file() else None
                store.update_job(job_id, remote_model_json=json.dumps(settings), run_stage=stage)
                store.add_event(job_id, None, 'info', 'model_changed', '下次执行使用 '+provider+'/'+model+'；重算 AI 判定及下游')
            else:
                raise ValueError('不支持的操作')
            return {'message': '操作已执行', 'job_status': store.get_job(job_id)['status']}

    def accept(self, command):
        ident = command['id']
        with self.journal() as con:
            if con.execute('SELECT 1 FROM receipts WHERE id=?', (ident,)).fetchone():
                return
            con.execute('INSERT INTO receipts(id,status,result) VALUES (?, ?, ?)', (ident, 'running', '{}'))
        try:
            if time.time() > command['expires']:
                raise ValueError('指令已过期')
            result = self.execute(command)
            status = 'succeeded'
        except Exception as exc:
            result, status = {'message': str(exc)}, 'failed'
        with self.journal() as con:
            con.execute('UPDATE receipts SET status=?, result=? WHERE id=?', (status, json.dumps(result), ident))

    def tick(self):
        with self.journal() as con:
            results = [dict(id=r[0], status=r[1], **json.loads(r[2])) for r in con.execute("SELECT id,status,result FROM receipts WHERE acknowledged=0 AND status!='running' LIMIT 100")]
        payload = json.dumps({'snapshot': self.snapshot(), 'results': results}, ensure_ascii=False).encode()
        req = urllib.request.Request(self.url+'/api/clipping/agent/'+self.device_id+'/exchange', data=payload,
                headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=10) as response:
            body = json.load(response)
        with self.journal() as con:
            for ident in body.get('acknowledged', []):
                con.execute('UPDATE receipts SET acknowledged=1 WHERE id=?', (ident,))
        for command in body.get('commands', []):
            if self.stopping.is_set():
                break
            self.accept(command)

    def run(self):
        while not self.stopping.is_set():
            try:
                self.tick()
            except Exception as exc:
                # Do not log request headers or tokens.
                log.warning('剪辑远程同步失败 (%s)', type(exc).__name__)
            self.stopping.wait(3)
