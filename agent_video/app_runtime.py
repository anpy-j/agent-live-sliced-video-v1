"""Desktop resources are immutable; all user state lives outside the installation."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def user_data_dir() -> Path:
    override = os.environ.get('LIVE_CUT_DATA_DIR')
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'LiveCut'
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'LiveCut'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share')) / 'LiveCut'


def resource_dir() -> Path:
    return Path(os.environ.get('LIVE_CUT_RESOURCE_DIR', Path(__file__).resolve().parent.parent)).resolve()


def configure_desktop(resources: Path, data: Path) -> None:
    """Inherited by spawned pipeline workers, without requiring global installs."""
    data.mkdir(parents=True, exist_ok=True)
    tools = resources / 'tools' / 'bin'
    os.environ['PATH'] = str(tools) + os.pathsep + str(Path(sys.executable).parent) + os.pathsep + os.environ.get('PATH', '')
    os.environ['LIVE_CUT_RESOURCE_DIR'] = str(resources)
    os.environ['LIVE_CUT_DATA_DIR'] = str(data)
    os.environ['CODEX_HOME'] = str(data / 'ai' / 'codex')
    os.environ['XDG_CONFIG_HOME'] = str(data / 'ai' / 'config')
    os.environ['XDG_DATA_HOME'] = str(data / 'ai' / 'data')
    os.environ['XDG_CACHE_HOME'] = str(data / 'ai' / 'cache')
    os.environ['HF_HOME'] = str(data / 'models')
    for key in ['CODEX_HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME']:
        Path(os.environ[key]).mkdir(parents=True, exist_ok=True)
    # Skill is user-editable, unlike the read-only copy shipped in the app.
    source = resources / 'integrations' / 'skill' / 'SKILL.md'
    destination = data / 'integrations' / 'skill' / 'SKILL.md'
    if source.is_file() and not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


class DataLock:
    """One desktop process per user database, including on Windows."""
    def __init__(self, data: Path):
        self.path = data / 'data' / 'agent.lock'
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                self.handle.seek(0)
                if not self.handle.read(1):
                    self.handle.write(b'0'); self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            raise RuntimeError('已有一个 LiveCut 正在使用当前用户数据，请先关闭它') from None
        return self

    def __exit__(self, *_):
        if self.handle:
            if os.name == 'nt':
                import msvcrt
                self.handle.seek(0); msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None
