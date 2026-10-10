"""Observable authentication and direct API configuration for the desktop UI."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

from .api_transport import json_completion, validate_base_url
from .credentials import CredentialVault


class AIAccess:
    def __init__(self, store, root: Path, vault=None):
        self.store = store
        self.root = root
        self.vault = vault or CredentialVault(root)
        self._process = None
        self._lock = threading.RLock()
        self._login = {'status': 'idle', 'message': ''}

    def configuration(self):
        return {'base_url': self.store.get_setting('api_base_url', 'https://api.openai.com/v1'),
                'model': self.store.get_setting('api_model', ''),
                'api_key_configured': bool(self.vault.get('model-api-key'))}

    def save(self, payload):
        base = validate_base_url(str(payload.get('base_url') or 'https://api.openai.com/v1'))
        model = str(payload.get('model') or '').strip()
        if not model or len(model) > 200:
            raise ValueError('请填写具体模型名称')
        key = str(payload.get('api_key') or '').strip()
        if key:
            self.vault.set('model-api-key', key)
        elif not self.vault.get('model-api-key'):
            raise ValueError('请填写 API Key')
        self.store.set_setting('api_base_url', base)
        self.store.set_setting('api_model', model)
        self.store.set_setting('ai_provider', 'api')
        self.store.set_setting('ai_model', model)
        self.store.set_setting('ai_engine', 'llm')
        return self.configuration()

    def clear_key(self):
        self.vault.delete('model-api-key')
        return self.configuration()

    def test(self):
        config = self.configuration()
        data, _ = json_completion(base_url=config['base_url'], model=config['model'],
            api_key=self.vault.get('model-api-key'), prompt='Return JSON {"ok":true}.', timeout=30)
        if data.get('ok') is not True:
            raise RuntimeError('连接成功，但模型未按要求返回 JSON')
        return {'ok': True, 'message': '连接成功，模型可以返回结构化结果'}

    def status(self):
        executable = shutil.which('codex')
        logged_in = False
        if executable:
            try:
                result = subprocess.run([executable, 'login', 'status'], capture_output=True,
                    timeout=5, **self._spawn_options())
                logged_in = result.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                pass
        with self._lock:
            login = dict(self._login)
        return {'provider': self.store.get_setting('ai_provider', 'auto'), 'api': self.configuration(), 'codex': {'available': bool(executable),
                'logged_in': logged_in, 'login': login},
                'opencode': {'available': bool(shutil.which('opencode'))}}

    @staticmethod
    def _spawn_options():
        return {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}

    def login(self):
        executable = shutil.which('codex')
        if not executable:
            raise ValueError('应用内未找到 Codex，请检查工具包')
        with self._lock:
            if self._process and self._process.poll() is None:
                return dict(self._login)
            self._process = subprocess.Popen([executable, 'login'], stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **self._spawn_options())
            self._login = {'status': 'waiting', 'message': '请在官方浏览器页面完成登录；若未自动打开，请使用下方登录地址。'}
            threading.Thread(target=self._watch, args=(self._process,), daemon=True).start()
        return dict(self._login)

    def _watch(self, process):
        # A separate reader keeps the GUI responsive and avoids filling CLI pipes.
        lines = []
        def read():
            for line in iter(process.stdout.readline, b''):
                text = line.decode(errors='replace').strip()
                if 'https://auth.openai.com/' in text:
                    import re
                    match = re.search(r'https://auth\.openai\.com/[^\s\x1b]+', text)
                    if match:
                        with self._lock:
                            if self._process is process:
                                self._login['url'] = match.group(0)
                lines.append(text)
                del lines[:-20]
        reader = threading.Thread(target=read, daemon=True); reader.start()
        try:
            code = process.wait(timeout=300)
            state = 'success' if code == 0 else 'failed'
            message = '登录成功，可以选择 Codex 开始剪辑' if code == 0 else '登录未完成，请重试'
            if code == 0:
                self.store.set_setting('ai_provider', 'codex')
                self.store.set_setting('ai_model', 'auto')
                self.store.set_setting('ai_engine', 'llm')
        except subprocess.TimeoutExpired:
            self._terminate(process)
            state, message = 'failed', '登录已超时，请重试'
        finally:
            reader.join(timeout=1)
            process.stdout.close()
        with self._lock:
            if self._process is process:
                self._login = {'status': state, 'message': message}
                self._process = None

    def _terminate(self, process):
        if process.poll() is not None:
            return
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
        else:
            import signal
            try: os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError: pass
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait()

    def cancel(self):
        with self._lock:
            process = self._process
            self._process = None
            self._login = {'status': 'idle', 'message': '登录已取消'}
        if process: self._terminate(process)
        return dict(self._login)

    def logout(self):
        self.cancel()
        executable = shutil.which('codex')
        if executable:
            subprocess.run([executable, 'logout'], capture_output=True, timeout=10, **self._spawn_options())
        return {'ok': True}

    def close(self):
        self.cancel()
