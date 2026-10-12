"""Install ACE-Step into an isolated CPU environment; run with Python 3.12."""
import os
import subprocess
import sys
import tomllib
from pathlib import Path

REPO = "https://github.com/ace-step/ACE-Step-1.5.git"
REVISION = "ca1e85fe9430179831e6bc6be790c332190a3866"


def main():
    if sys.version_info[:2] != (3, 12):
        raise SystemExit("请使用 Python 3.12 运行这个 CPU 环境安装脚本")
    root = Path(__file__).resolve().parents[1] / ".runtime"
    root.mkdir(exist_ok=True)
    checkout = root / "ace-step"
    environment = root / "ace-cpu"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    def run(*args):
        subprocess.run([str(x) for x in args], check=True)

    if not checkout.exists():
        run("git", "clone", REPO, checkout)
        run("git", "-C", checkout, "checkout", "--detach", REVISION)
    else:
        revision = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
        if revision != REVISION:
            raise SystemExit("已有 ACE-Step 目录版本不同；请保留自己的目录，并按文档配置 LIVECUT_ACESTEP_ROOT")
    if not python.exists():
        run(sys.executable, "-m", "venv", environment)
    run(python, "-m", "pip", "install", "uv==0.13.0")
    run(python, "-m", "uv", "pip", "install", "--python", python, "--index-url", "https://download.pytorch.org/whl/cpu",
        "torch==2.10.0", "torchvision==0.25.0", "torchaudio==2.10.0")
    deps = tomllib.loads((checkout / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    # Keep CPU PyTorch; omit optional CUDA/Apple/LLM acceleration packages.
    deps = [x for x in deps if not x.lower().startswith(("torch", "nano-vllm", "mlx"))]
    requirements = root / "ace-cpu-requirements.txt"
    requirements.write_text("\n".join(deps)+"\n", encoding="utf-8")
    run(python, "-m", "uv", "pip", "install", "--python", python, "-r", requirements)
    print("CPU 环境安装完成。启动 LiveCut → 视频后处理 → 启动本机 CPU 服务。首次生成仍需下载模型。")


if __name__ == "__main__":
    main()
