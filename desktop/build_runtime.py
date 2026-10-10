"""Build a host-native runtime with an explicit whitelist; never include user data."""
import os
import importlib.metadata
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
local_python = ROOT / ".runtime" / "desktop-build" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
build_python = Path(os.environ.get("LIVECUT_BUILD_PYTHON") or (str(local_python) if local_python.is_file() else sys.executable))
if build_python.resolve() != Path(sys.executable).resolve():
    raise SystemExit(subprocess.call([str(build_python), str(Path(__file__).resolve())], cwd=ROOT))
OUTPUT = ROOT / "desktop" / "runtime"
OUTPUT.mkdir(parents=True, exist_ok=True)
bin_dir = OUTPUT / "bin"
bin_dir.mkdir(exist_ok=True)
licenses = OUTPUT / "licenses"
licenses.mkdir(exist_ok=True)
python_license = Path(sys.base_prefix) / "LICENSE.txt"
if python_license.exists():
    shutil.copy2(python_license, licenses / "Python.txt")
for name in ("faster-whisper", "ctranslate2", "onnxruntime", "av", "Pillow", "psutil", "pypinyin", "zhconv", "numpy"):
    dist = importlib.metadata.distribution(name)
    for file in dist.files or []:
        if any(part.lower().startswith(("license", "copying", "notice")) for part in file.parts) and ".dist-info" in str(file):
            target = licenses / name / Path(file).name
            target.parent.mkdir(exist_ok=True)
            shutil.copy2(dist.locate_file(file), target)
for name in ("ffmpeg", "ffprobe"):
    source = shutil.which(name)
    if not source:
        raise SystemExit(f"Missing {name}; install host-native FFmpeg before building")
    shutil.copy2(source, bin_dir / Path(source).name)
    if name == "ffmpeg":
        result = subprocess.run([source, "-L"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        (licenses / "FFmpeg.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
    # macOS dynamically linked builds need their library dependencies bundled.
    if sys.platform == "darwin":
        check = subprocess.check_output(["otool", "-L", source], text=True)
        external = [line.strip().split(" ")[0] for line in check.splitlines()[1:]
                    if line.strip().startswith(("/opt/", "/usr/local/"))]
        if external:
            raise SystemExit("Use a standalone/static macOS FFmpeg build; external dylibs found: " + ", ".join(external))
subprocess.run([
    sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
    "--name", "livecut-backend", "--distpath", str(OUTPUT),
    "--workpath", str(ROOT / "build" / "pyinstaller"), "--specpath", str(ROOT / "build"),
    "--paths", str(ROOT), "--paths", str(ROOT / "agent_video" / "engine" / "scripts"),
    "--add-data", f"{ROOT / 'web'}{os.pathsep}web",
    "--add-data", f"{ROOT / 'integrations' / 'skill'}{os.pathsep}integrations/skill",
    "--add-data", f"{ROOT / 'agent_video' / 'engine' / 'profiles'}{os.pathsep}agent_video/engine/profiles",
    "--hidden-import", "agent_video.editor.render", "--hidden-import", "agent_video.engine.scripts.render_multi",
    "--hidden-import", "agent_video.pipeline.run", "--hidden-import", "PIL.Image",
    "--hidden-import", "agent_video.editor.transcribe", "--collect-all", "faster_whisper",
    "--collect-all", "ctranslate2", "--collect-all", "onnxruntime",
    "--exclude-module", "torch", "--exclude-module", "mlx_whisper",
    str(ROOT / "desktop" / "backend_entry.py")
], cwd=ROOT, check=True)
print("Host-native desktop runtime ready:", OUTPUT)
