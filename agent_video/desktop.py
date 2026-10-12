"""Native window backed by the existing local service and a bundled Python."""
from __future__ import annotations

import argparse
import json
import logging
import multiprocessing
import sys
from pathlib import Path

from .app_runtime import configure_desktop, resource_dir, user_data_dir
from .server import ServiceSession


def main():
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(description='LiveCut 桌面剪辑助手')
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--headless', action='store_true', help='Start the packaged service without the window for diagnostics')
    args = parser.parse_args()
    resources = resource_dir()
    data = (args.data_dir or user_data_dir()).resolve()
    configure_desktop(resources, data)
    logs = data / 'logs'; logs.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=logs / 'desktop.log', level=logging.INFO)
    try:
        with ServiceSession(resources, port=args.port, data_root=data) as session:
            (data / 'runtime.json').write_text(json.dumps({'url': session.url, 'pid': __import__('os').getpid()}))
            try:
                if args.headless:
                    print(session.url, flush=True)
                    session.thread.join()
                else:
                    import webview
                    webview.create_window('切片剪辑助手 V1', session.url, width=1360, height=900,
                        min_size=(1000, 700), text_select=True)
                    webview.start(private_mode=True)
            finally:
                (data / 'runtime.json').unlink(missing_ok=True)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        logging.exception('Desktop startup failed')
        if sys.platform == 'darwin' and not args.headless:
            import subprocess
            # JSON quoting produces a safe AppleScript string literal.
            message = json.dumps(f'启动失败：{exc}\\n日志：{logs / "desktop.log"}', ensure_ascii=False)
            subprocess.run(['/usr/bin/osascript', '-e', f'display alert "LiveCut" message {message}'], check=False)
        elif sys.platform == 'win32' and not args.headless:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, f'{exc}\n日志：{logs / "desktop.log"}', 'LiveCut 启动失败', 0x10)
        else:
            raise
        raise SystemExit(1)


if __name__ == '__main__':
    main()
