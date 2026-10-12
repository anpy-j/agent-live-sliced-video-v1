# 桌面改造验证记录

2026-10-10，macOS Apple Silicon，Python 3.13.12。

- 完整回归：277 passed、2 skipped、4 subtests passed（pytest；TMPDIR=/private/tmp，避免 macOS /var 和 /private/var 别名导致断言差异）。
- 新增桌面用例覆盖资源与用户数据隔离、重复进程锁、升级后的内置解释器路径更新、密钥不写入 SQLite／不回显、API 环境恢复、登录取消、HTTP 页面及跨来源拒绝、模拟模型请求。
- 原生窗口及系统设置已实际打开，账号状态、登录按钮、API Key 表单显示正常；关闭窗口后服务停止。
- 最终 ZIP 解压到临时目录，验证签名、应用内符号链接、独立 Python 依赖导入；限制 PATH 后启动本机服务并查询健康、配置与 AI 接入接口，使用内置 FFmpeg 生成视频。
- 修复 FFmpeg 8 草稿变速片段中无限 apad 引发的卡死；真实草稿渲染与画面映射回归通过。
- 没有发起真实账户登录或付费模型请求；登录流程和模型接入使用模拟测试。
- Windows 构建脚本已提供，尚未在 Windows 主机实测；WebView2 需目标系统已有。
- 当前应用为本地 ad-hoc 签名，尚未做 Developer ID 签名和 Apple 公证。

最终产物：dist/LiveCut-darwin-arm64-20261010-113233/LiveCut-macOS.zip。
