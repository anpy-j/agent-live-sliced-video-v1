# 桌面端启动（远端整体合并后）

当前默认桌面端为 Electron，包含三版本剪辑、人工剪辑、后处理和云端管理入口。

macOS 开发环境首次安装或更新依赖：

```sh
uv pip install --python .venv/bin/python -e '.[desktop,portable-asr]'
npm ci
npm run desktop
```

默认继续使用 `~/Library/Application Support/LiveCut` 中已有的任务、设置和工作文件。新桌面端会优先使用项目 `.venv`，不会误用系统 Python。请先等待原桌面端任务结束并退出，再启动新版；同一数据目录不能同时启动两个后台。自定义目录使用 `LIVE_CUT_DATA_DIR` 或 `LIVECUT_WORKSPACE`，测试用 Electron 配置目录可用 `LIVECUT_USER_DATA`。

原 pywebview 启动方式保留为 `npm run desktop:legacy`。下面的原打包说明对应 pywebview；Electron 打包入口是 `npm run dist:mac` / `npm run dist:win`，需要另行构建原生运行包。本次只验证 macOS 开发桌面端，没有生成新的安装包。

人工剪辑包含字幕时，优先使用现有支持 `subtitles` 的 FFmpeg；如果系统版本缺少该滤镜，自动使用项目依赖 `imageio-ffmpeg` 提供的程序。原 V1 渲染器保持不变。

---

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

原 pywebview 桌面端可用 `npm run desktop:legacy`，对应 `.venv/bin/python -m agent_video.desktop`。

```sh
uv venv --python 3.13.12
uv pip install --python .venv/bin/python -e '.[desktop,portable-asr]'
.venv/bin/python -m agent_video.desktop
.venv/bin/python -m unittest discover -s tests -q
```

原有 `python -m agent_video` 浏览器服务继续可用。桌面入口默认绑定本机随机端口，可用 `--headless --data-dir 路径` 排查服务启动，运行地址记录在该数据目录 runtime.json，启动错误记录在 logs/desktop.log。

## macOS 剪映草稿目录

默认优先读取 `~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft/root_meta_info.json`，也检查 Application Support 和剪映容器目录。自定义目录可用 `JIANYING_DRAFT_ROOT` 指定。索引里的草稿路径可以指向其他磁盘。macOS 不需要 `LOCALAPPDATA`，默认导出目录为 `~/Movies/LiveCut/exports`。

macOS 加密草稿通过本机剪映的 `libvideoeditor.dylib` 读取，调用在独立进程完成，不修改原草稿。按应用标识自动寻找安装目录，改名为 `VideoFusion-macOS.app` 等名称也可识别；非标准安装可设置 `JIANYING_INSTALL_DIR` 指向 .app，或 `JIANYING_MAC_LIBRARY` 指向 dylib。

源码启动首次读取时自动编译并缓存小型 C++ 读取器，需要 Xcode 命令行工具；缓存命中不再编译。桌面构建脚本会打包我们自己的读取器，最终用户无需编译工具，但仍需本机安装剪映。不会将剪映的原生库打包分发。剪映内部接口随版本可能变化，当前在 Apple Silicon、剪映 11.3.0 上实测。

旧的 macOS“读取失败”摘要缓存会自动失效，重新读取无需手动删除文件。Mac 冷读取最多两路并发，避免多份原生库同时加载导致内存压力。草稿为空、解密失败或缺少素材时给出对应原因，不创建假时间线。

## ASR 音频依赖兼容

`faster-whisper 1.2.1` 会向 `av.open()` 传入 `metadata_errors`；[PyAV 19 的变更说明](https://pyav.basswood.io/docs/stable/development/changelog.html)明确移除了该参数。项目的 `portable-asr` 依赖限制为 `av>=11,<19`，当前锁定 PyAV 18.1.0。源码环境和后续桌面构建均使用此范围，不修改模型、解码参数或三个剪辑版本的规则。

如果旧环境出现 `open() got an unexpected keyword argument 'metadata_errors'`，可在项目目录执行 `uv pip install --python .venv/bin/python 'av>=11,<19'`，随后退出桌面端并重新运行 `npm run desktop`。真实音频解码回归测试使用中文文件名的 48 kHz 双声道 WAV，验证转换为 16 kHz 单声道浮点数组，无需下载模型。
