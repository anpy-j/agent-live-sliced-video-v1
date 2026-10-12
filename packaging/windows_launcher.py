"""Small native launcher; pipeline workers run under the bundled real Python."""
import ctypes
import os
from pathlib import Path
import subprocess
import sys


def main():
    root = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
    resources = root / 'resources'
    env = dict(os.environ, LIVE_CUT_RESOURCE_DIR=str(resources), PYTHONPATH=str(resources))
    python = resources / 'runtime' / 'python.exe'
    try:
        result = subprocess.run([str(python), '-m', 'agent_video.desktop', *sys.argv[1:]], cwd=resources,
            env=env, creationflags=subprocess.CREATE_NO_WINDOW)
        return result.returncode
    except Exception as exc:
        ctypes.windll.user32.MessageBoxW(0, str(exc), 'LiveCut 启动失败', 0x10)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
