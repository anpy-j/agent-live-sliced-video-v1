#!/usr/bin/env python3
"""Build an offline desktop bundle on its target OS; no user data is packaged."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(args, **options):
    return subprocess.run([str(a) for a in args], check=True, **options)


def copy_macos_binary(source: Path, target: Path, lib_dir: Path, seen: dict):
    source = source.resolve()
    if source in seen:
        return seen[source]
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    target.chmod(0o755)
    seen[source] = target
    output = subprocess.check_output(['otool', '-L', str(source)], text=True)
    for line in output.splitlines()[1:]:
        dependency = line.strip().split(' (', 1)[0]
        if dependency.startswith(('/usr/lib/', '/System/Library/')):
            continue
        if dependency.startswith('@loader_path/'):
            origin = source.parent / dependency.removeprefix('@loader_path/')
        elif dependency.startswith('@rpath/'):
            # Homebrew libraries normally use absolute install names; fail explicitly
            # rather than silently ship a bundle that only runs on this Mac.
            origin = source.parent / dependency.removeprefix('@rpath/')
        else:
            origin = Path(dependency)
        if origin.resolve() == source:
            continue
        if not origin.is_file():
            raise RuntimeError(f'无法定位动态库：{dependency}（{source}）')
        dest = copy_macos_binary(origin, lib_dir / origin.name, lib_dir, seen)
        new_name = '@loader_path/' + os.path.relpath(dest, target.parent)
        run(['install_name_tool', '-change', dependency, new_name, target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if target.suffix == '.dylib':
        run(['install_name_tool', '-id', '@loader_path/' + target.name, target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    run(['codesign', '--force', '--sign', '-', target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return target


def find_tool(name: str):
    found = shutil.which(name)
    if found: return Path(found)
    for folder in [Path('/opt/homebrew/bin'), Path('/usr/local/bin'), Path.home() / '.opencode/bin', Path.home() / '.local/bin']:
        candidate = folder / name
        if candidate.is_file(): return candidate
    return None


def locate_codex() -> Path:
    found = find_tool('codex')
    if not found:
        raise RuntimeError('构建机器没有 Codex；请安装或提供 --tools-dir')
    path = Path(found).resolve()
    if path.suffix in {'.js', '.cmd', '.bat'}:
        package = path.parent.parent
        suffix = 'codex.exe' if os.name == 'nt' else 'codex'
        matches = list(package.glob(f'node_modules/@openai/codex-*/vendor/*/bin/{suffix}'))
        if not matches:
            matches = list(package.glob(f'vendor/*/codex/{suffix}'))
        if len(matches) != 1:
            raise RuntimeError('无法唯一定位 Codex 原生二进制；请使用 --tools-dir')
        return matches[0]
    return path


def collect_tools(resources: Path, supplied: Path | None):
    tools = resources / 'tools'; binary_dir = tools / 'bin'; lib_dir = tools / 'lib'
    binary_dir.mkdir(parents=True, exist_ok=True); lib_dir.mkdir(exist_ok=True)
    if supplied:
        shutil.copytree(supplied, tools, dirs_exist_ok=True)
    manifest, seen = [], {}
    for name in ['ffmpeg', 'ffprobe', 'codex', 'opencode']:
        filename = name + ('.exe' if os.name == 'nt' else '')
        destination = binary_dir / filename
        if not destination.is_file():
            if os.name == 'nt':
                raise RuntimeError(f'Windows 构建请通过 --tools-dir 提供原生 {filename} 与所需 DLL（不能使用 .cmd 启动器）')
            source = locate_codex() if name == 'codex' else find_tool(name)
            if source is None or not source.is_file():
                raise RuntimeError(f'未找到 {name}，请提供 --tools-dir')
            if sys.platform == 'darwin':
                copy_macos_binary(source, destination, lib_dir, seen)
                if name == 'codex':
                    helper = source.parent / 'codex-code-mode-host'
                    if helper.is_file():
                        copy_macos_binary(helper, binary_dir / helper.name, lib_dir, seen)
            else:
                shutil.copy2(source, destination)
        flag = '-version' if name in {'ffmpeg', 'ffprobe'} else '--version'
        version_output = subprocess.check_output([str(destination), flag], text=True, stderr=subprocess.STDOUT)
        version = version_output.splitlines()[0]
        manifest.append({'name': name, 'version': version,
            'build_info': version_output if name in {'ffmpeg', 'ffprobe'} else version,
            'sha256': hashlib.sha256(destination.read_bytes()).hexdigest()})
    (tools / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf8')
    licenses = resources / 'licenses'; licenses.mkdir(exist_ok=True)
    sources = {
        'Codex-APACHE-2.0.txt': 'https://raw.githubusercontent.com/openai/codex/rust-v0.147.0/LICENSE',
        'OpenCode-MIT.txt': 'https://raw.githubusercontent.com/anomalyco/opencode/v1.17.14/LICENSE',
        'FFmpeg-GPL-2.0.txt': 'https://raw.githubusercontent.com/FFmpeg/FFmpeg/n8.0.1/COPYING.GPLv2',
        'FFmpeg-GPL-3.0.txt': 'https://raw.githubusercontent.com/FFmpeg/FFmpeg/n8.0.1/COPYING.GPLv3',
        'FFmpeg-LGPL-2.1.txt': 'https://raw.githubusercontent.com/FFmpeg/FFmpeg/n8.0.1/COPYING.LGPLv2.1',
    }
    for name, url in sources.items():
        with urllib.request.urlopen(url, timeout=30) as response:
            (licenses / name).write_bytes(response.read())
    # Retain locally installed dependency licenses and FFmpeg's build recipe.
    for original in seen:
        parts = original.parts
        if 'Cellar' in parts:
            index = parts.index('Cellar')
            formula = parts[index + 1]
            prefix = Path(*parts[:index + 3])
            destination = licenses / 'homebrew' / formula
            if not destination.exists():
                destination.mkdir(parents=True)
                for folder in [prefix / 'share' / 'licenses', prefix / '.brew']:
                    if folder.is_dir(): shutil.copytree(folder, destination / folder.name, dirs_exist_ok=True)
                for file in prefix.glob('*'):
                    if file.is_file() and any(word in file.name.upper() for word in ['COPYING', 'LICENSE', 'NOTICE']):
                        shutil.copy2(file, destination / file.name)
    (licenses / 'README.txt').write_text('This bundle includes third-party programs and their license notices.\n'
        'FFmpeg source: https://ffmpeg.org/releases/ffmpeg-8.0.1.tar.xz\n'
        'Build options are recorded in the tools manifest and Homebrew build recipes.\n'
        'When redistributing LGPL/GPL binaries, provide their matching source and any modifications under their licenses.\n', encoding='utf8')


def copy_resources(destination: Path):
    for name in ['agent_video', 'web', 'integrations']:
        shutil.copytree(ROOT / name, destination / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store'))
    for name in ['README.md', 'REMOTE_CLIPPING.md', 'DESKTOP.md']:
        if (ROOT / name).exists(): shutil.copy2(ROOT / name, destination / name)


def build(args):
    if sys.platform not in {'darwin', 'win32'}:
        raise RuntimeError('桌面包目前支持在 macOS 或 Windows 本机构建')
    architecture = platform.machine().lower()
    out = (ROOT / 'dist' / f'LiveCut-{sys.platform}-{architecture}-{__import__("datetime").datetime.now().strftime("%Y%m%d-%H%M%S")}').resolve()
    # Unique output keeps previous deliverables and prevents destructive rebuilds.
    out.mkdir(parents=True, exist_ok=False)
    if sys.platform == 'darwin':
        app = out / 'LiveCut.app'; resources = app / 'Contents' / 'Resources'
    else:
        app = out / 'LiveCut'; resources = app / 'resources'
    resources.mkdir(parents=True)
    copy_resources(resources)
    runtime = resources / 'runtime'
    uv = shutil.which('uv') or next((str(p) for p in [Path.home() / '.local/bin/uv', Path.home() / '.cargo/bin/uv'] if p.is_file()), None)
    if not uv: raise RuntimeError('构建需要 uv；最终应用不需要安装 uv')
    run([uv, 'python', 'install', args.python_version])
    python_path = Path(subprocess.check_output([uv, 'python', 'find', '--system', '--managed-python', args.python_version], text=True).strip()).resolve()
    source = python_path.parent if os.name == 'nt' else python_path.parent.parent
    shutil.copytree(source, runtime, symlinks=True)
    interpreter = runtime / ('python.exe' if os.name == 'nt' else 'bin/python3')
    if not interpreter.resolve().is_relative_to(runtime.resolve()):
        raise RuntimeError('打包解释器必须位于产物目录内')
    run([uv, 'pip', 'install', '--break-system-packages', '--python', interpreter, str(ROOT) + '[desktop,portable-asr]'])
    freeze = subprocess.check_output([uv, 'pip', 'freeze', '--python', interpreter], text=True)
    (resources / 'python-packages.txt').write_text(freeze)
    collect_tools(resources, args.tools_dir)
    if sys.platform == 'darwin':
        # Ship our bridge, not Jianying's proprietary dylibs. Runtime uses the
        # user's own installed app and does not require development tools.
        helper = resources / 'tools' / 'bin' / 'jianying-decrypt'
        run(['xcrun', 'clang++', '-std=c++17', '-O2', '-arch', architecture,
             '-mmacosx-version-min=11.0', ROOT / 'agent_video' / 'native' / 'jianying_decrypt.cpp',
             '-Wl,-rpath,@loader_path', '-o', helper])
        run(['codesign', '--force', '--sign', '-', helper])
    # Verify runtime imports and bundled binaries before emitting an archive.
    run([interpreter, '-c', 'import webview, keyring, faster_whisper, PIL, pypinyin, zhconv'])
    if sys.platform == 'darwin':
        executable_dir = app / 'Contents' / 'MacOS'; executable_dir.mkdir()
        launch = executable_dir / 'LiveCut'
        launch.write_text('#!/bin/sh\nBASE="$(CDPATH= cd -- "$(dirname -- "$0")/../Resources" && pwd)"\n'
            'export LIVE_CUT_RESOURCE_DIR="$BASE"\nexport PYTHONPATH="$BASE"\n'
            'exec "$BASE/runtime/bin/python3" -m agent_video.desktop "$@"\n')
        launch.chmod(0o755)
        with (app / 'Contents' / 'Info.plist').open('wb') as file:
            plistlib.dump({'CFBundleExecutable': 'LiveCut', 'CFBundleIdentifier': 'com.livecut.desktop',
                'CFBundleName': '切片剪辑助手 V1', 'CFBundleDisplayName': '切片剪辑助手 V1',
                'CFBundlePackageType': 'APPL', 'CFBundleShortVersionString': '0.1.0', 'CFBundleVersion': '1',
                'NSHighResolutionCapable': True, 'NSAppTransportSecurity': {'NSAllowsLocalNetworking': True}}, file)
        run(['codesign', '--force', '--deep', '--sign', '-', app], stdout=subprocess.DEVNULL)
        archive = out / 'LiveCut-macOS.zip'
        run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', app, archive])
    else:
        run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onefile', '--windowed',
            '--name', 'LiveCut', '--distpath', app, '--workpath', out / 'launcher-build',
            '--specpath', out, ROOT / 'packaging' / 'windows_launcher.py'])
        archive = Path(shutil.make_archive(str(out / 'LiveCut-Windows'), 'zip', out, 'LiveCut'))
    print(json.dumps({'application': str(app), 'archive': str(archive)}, ensure_ascii=False), flush=True)
    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--python-version', default='3.13.12')
    parser.add_argument('--tools-dir', type=Path, help='Folder with bin/ffmpeg, bin/ffprobe, bin/codex, bin/opencode and optional lib/')
    build(parser.parse_args())
