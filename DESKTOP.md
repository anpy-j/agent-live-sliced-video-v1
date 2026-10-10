# 桌面版

桌面入口使用原有剪辑服务和界面，窗口关闭时停止后台服务。macOS 安装包中带独立 Python、FFmpeg、FFprobe、Codex、OpenCode 与 Python 依赖，不依赖开发机器的虚拟环境、Node.js 或 Homebrew。首次语音识别仍需联网下载模型，模型存入用户数据目录。

## 使用

打开 LiveCut.app，在「系统设置 → 连接 AI」选择以下方式：

- 登录 Codex：打开官方账户登录流程；登录成功后在 AI 服务商中选择 Codex、模型选择 auto 并保存设置。
- API Key：填写兼容 OpenAI Chat Completions 的服务地址（以 /v1 结尾）、具体模型名和密钥，保存后自动选择 API 模式。测试连接会产生一次小请求。

API Key 保存在系统钥匙串／Windows 凭据管理器中，界面和配置数据库不回显密钥。Codex 登录信息使用应用自己的 CODEX_HOME，与终端账号分开。OpenCode 已内置，但本版账户登录按钮对应 Codex；直接 API 模式不需要 CLI 登录。

用户数据位于 macOS `~/Library/Application Support/LiveCut`，Windows `%LOCALAPPDATA%/LiveCut`。数据库、任务文件、模型、日志、AI 登录目录及可编辑 Skill 均在此处，替换应用不会覆盖这些内容。不会自动搬迁源项目 data/workspaces；如需使用历史任务，应先停止原服务，再备份并迁移到用户目录。Skill 仅首次复制默认内容。

## 构建

macOS 构建机器需 uv、Python、FFmpeg/FFprobe、原生 Codex/OpenCode，以及 Xcode 命令行工具：

```sh
./packaging/build-macos.sh
```

产物保存在 `dist/LiveCut-darwin-架构-时间/`，包含应用和 ZIP。Apple Silicon 与 Intel 必须分别在对应架构构建。当前为本地 ad-hoc 签名，正式对外分发需要 Developer ID 签名、公证及第三方源码许可材料；没有完成这些步骤的包不能视作已正式发布。

Windows 需在 Windows 主机执行，先安装构建依赖：

```powershell
uv pip install --python python '.[build]'
./packaging/build-windows.ps1 --tools-dir C:\livecut-tools
```

tools-dir 中 `bin` 目录放 `ffmpeg.exe`、`ffprobe.exe`、`codex.exe`、`opencode.exe` 和这些工具必需的 DLL。目标电脑需 Microsoft Edge WebView2 Runtime。当前 Windows 脚本尚未在 Windows 主机实测，也未内置 WebView2 离线安装器；不能将其描述为已验证的零依赖 Windows 安装包。

安装包工具版本、构建参数和哈希在 `Resources/tools/manifest.json`（Windows 为 resources/tools）。Python 依赖清单在 python-packages.txt，第三方许可证在 licenses。分发 FFmpeg 及相关 GPL/LGPL 组件时需提供匹配源码、构建方式和修改内容。

## 开发与验证

```sh
uv venv --python 3.13.12
uv pip install --python .venv/bin/python -e '.[desktop,portable-asr]'
.venv/bin/python -m agent_video.desktop
.venv/bin/python -m unittest discover -s tests -q
```

原有 `python -m agent_video` 浏览器服务继续可用。桌面入口默认绑定本机随机端口，可用 `--headless --data-dir 路径` 排查服务启动，运行地址记录在该数据目录 runtime.json，启动错误记录在 logs/desktop.log。
