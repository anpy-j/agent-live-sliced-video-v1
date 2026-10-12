"""macOS native draft reader. Never load the private C++ ABI into Python."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import plistlib
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from .app_runtime import resource_dir, user_data_dir

_BUILD_LOCK = threading.Lock()
_MAX_BYTES = 512 * 1024 * 1024


def _library_at(path: Path) -> Path | None:
    for candidate in (path, path / 'libvideoeditor.dylib',
                      path / 'Contents' / 'Frameworks' / 'libvideoeditor.dylib'):
        if candidate.name == 'libvideoeditor.dylib' and candidate.is_file():
            return candidate.resolve()
    return None


def find_mac_library(explicit: str | Path | None = None) -> Path:
    from .jianying_crypto import JianyingDecryptError
    for value in (explicit, os.environ.get('JIANYING_MAC_LIBRARY'),
                  os.environ.get('JIANYING_INSTALL_DIR'), os.environ.get('JY_INSTALL_DIR')):
        if value:
            library = _library_at(Path(value).expanduser())
            if library:
                return library
            # Explicit settings must not silently fall back to another version.
            raise JianyingDecryptError(f'指定的剪映安装路径没有 libvideoeditor.dylib：{value}')
    for root in (Path('/Applications'), Path.home() / 'Applications'):
        if not root.is_dir():
            continue
        for app in sorted([*root.glob('*.app'), *root.glob('*/*.app')]):
            try:
                with (app / 'Contents' / 'Info.plist').open('rb') as stream:
                    identifier = plistlib.load(stream).get('CFBundleIdentifier')
            except (OSError, ValueError, plistlib.InvalidFileException):
                continue
            if identifier not in {'com.lemon.lvpro', 'com.lemon.lvoverseas'}:
                continue
            library = _library_at(app)
            if library:
                return library
    # Spotlight also locates renamed apps installed outside /Applications.
    executable = shutil.which('mdfind')
    if executable:
        try:
            result = subprocess.run([executable, 'kMDItemCFBundleIdentifier == "com.lemon.lvpro"'],
                                    capture_output=True, text=True, timeout=5, check=False)
            if result.returncode == 0:
                for path in result.stdout.splitlines():
                    library = _library_at(Path(path))
                    if library:
                        return library
        except (OSError, subprocess.TimeoutExpired):
            pass
    raise JianyingDecryptError(
        '未找到 macOS 剪映原生库 libvideoeditor.dylib。请安装剪映专业版，'
        '或设置 JIANYING_INSTALL_DIR 指向剪映 .app。')


def native_helper() -> Path:
    from .jianying_crypto import JianyingDecryptError
    bundled = resource_dir() / 'tools' / 'bin' / 'jianying-decrypt'
    if bundled.is_file() and os.access(bundled, os.X_OK):
        return bundled
    source = Path(__file__).parent / 'native' / 'jianying_decrypt.cpp'
    architecture = platform.machine()
    if architecture not in {'arm64', 'x86_64'}:
        raise JianyingDecryptError(f'不支持的 macOS 架构：{architecture}')
    signature = hashlib.sha256(source.read_bytes() + architecture.encode()).hexdigest()[:20]
    cache = user_data_dir() / 'cache' / 'jianying-native'
    helper = cache / f'decrypt-{signature}'
    with _BUILD_LOCK:
        if helper.is_file() and os.access(helper, os.X_OK):
            return helper
        compiler = shutil.which('xcrun')
        if not compiler:
            raise JianyingDecryptError('首次读取加密草稿需要 macOS 命令行开发工具；请执行 xcode-select --install 后重试')
        cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(prefix='build-', dir=cache) as temporary:
            output = Path(temporary) / 'jianying-decrypt'
            try:
                result = subprocess.run(
                    [compiler, 'clang++', '-std=c++17', '-O2', '-arch', architecture,
                     str(source), '-Wl,-rpath,@loader_path', '-o', str(output)],
                    capture_output=True, text=True, timeout=60, check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise JianyingDecryptError(f'编译 macOS 草稿读取器失败：{exc}') from exc
            if result.returncode != 0 or not output.is_file():
                raise JianyingDecryptError('编译 macOS 草稿读取器失败；可执行 xcode-select --install 后重试：' + result.stderr[-1000:])
            output.chmod(0o700)
            output.replace(helper)
    return helper


def decrypt_mac_file(source: Path, install_dir=None, timeout: float = 90) -> dict:
    from .jianying_crypto import JianyingDecryptError
    if source.stat().st_size > _MAX_BYTES:
        raise JianyingDecryptError('剪映草稿超过 512 MiB 读取上限')
    library = find_mac_library(install_dir)
    helper = native_helper()
    environment = dict(os.environ)
    environment['DYLD_LIBRARY_PATH'] = str(library.parent)
    with tempfile.TemporaryDirectory(prefix='livecut-jianying-mac-') as temporary:
        output = Path(temporary) / 'draft.dec.json'
        try:
            result = subprocess.run([str(helper), str(library), str(source), str(output)],
                                    env=environment, capture_output=True, text=True,
                                    encoding='utf-8', errors='replace', timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise JianyingDecryptError(f'macOS 剪映草稿解密失败或超时：{exc}') from exc
        if result.returncode != 0 or not output.is_file():
            detail = (result.stderr or result.stdout or f'退出码 {result.returncode}').strip()
            raise JianyingDecryptError(f'macOS 剪映草稿解密失败：{detail[-1000:]}')
        if output.stat().st_size > _MAX_BYTES:
            raise JianyingDecryptError('解密结果超过 512 MiB 读取上限')
        try:
            data = json.loads(output.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError) as exc:
            raise JianyingDecryptError(f'macOS 解密结果不是有效 JSON：{exc}') from exc
        if not isinstance(data, dict):
            raise JianyingDecryptError('macOS 解密结果不是 JSON 对象')
        return data
