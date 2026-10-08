# 手机管理本地剪辑

千川 Flutter 客户端新增「剪辑管理」。电脑剪辑助手主动通过 HTTP 每约 3 秒同步到千川后端，上传状态、领取操作、回传执行回执。服务器不需要连接电脑的入站端口，原视频、模型密钥和渲染产物留在电脑。仅上传最近 100 个主队列任务，每任务最近 20 条日志。

## 支持范围

一期接入 `agent-live-sliced-video-v1` 的精简五阶段主队列（包括成片重组）。V2/V3 独立执行器暂未接入，手机不会混入这些任务。当前主队列使用文本模型；不提供主队列不存在的画面模型切换或成片下载。

暂停是步骤边界暂停：运行中显示「暂停中」，下一个步骤开始前退出子进程并保存「已暂停」。当前正在执行的模型调用/渲染不会立即中断；如果最后步骤已完成，任务直接完成。排队任务立即暂停。普通任务继续时复用已有上游产物，从下一步骤恢复；重组任务继续时重新执行重组决策。暂停记录跨服务重启保留。服务在执行中断电时遵循原队列恢复规则，无法保证当前步骤不中断。

重试只允许失败、取消或暂停的任务，保留现有确定性素材缓存并重算后续步骤。模型切换只允许暂停、失败或取消状态，按任务记录模型，保持全局设置不变；有 S2 结果时继续会重算 S3 及下游，没有有效上游时重新开始。模型目录来自电脑当前配置；模型是否可调用仍取决于对应 CLI 的安装、登录、账号权限和网络。

## 服务器与电脑配置

千川后端新增接口无需访问凭证。Docker Compose 持久化 `CLIPPING_DB_PATH=/app/data/clipping.db`，沿用现有 HTTP 服务部署。

电脑启动剪辑助手前设置：

```powershell
$env:LIVE_CUT_REMOTE_URL = "http://你的服务器IP:端口"
$env:LIVE_CUT_DEVICE_ID = "editing-pc"
$env:LIVE_CUT_DEVICE_NAME = "剪辑电脑"
python -m agent_video
```

无需手机凭证、设备凭证或设备预注册。不配置远程地址时保持本地运行。设备 ID 在多台电脑之间应保持唯一。

## 手机配置

安装更新后的千川app，打开「剪辑管理」右上角设置，输入同一 HTTP 服务地址。Android 使用应用私有设置保存；其余平台一期仅在内存保存，重启需重新填写。配置生效后等待电脑首次同步，展开任务查看阶段、日志并操作。

电脑超过 20 秒未同步显示离线；离线禁止下发操作，仍显示最后记录。连接异常时页面立即禁用操作。操作区分已提交、电脑已接收、成功、失败、过期和结果未知。请求有唯一 ID，服务器与电脑持久化去重；未领取的指令 30 秒过期，已领取指令不会被自动重复下发。电脑在操作期间崩溃时结果标记未知，需要人工核对后重新操作。

接口按个人使用要求免鉴权：能够访问服务器地址的客户端可以查看设备、提交操作和上报状态。

## 接口

- `GET /api/clipping/devices`：设备及任务快照。
- `POST /api/clipping/devices/{device_id}/commands`：带 `request_id/job_id/action` 的操作，action 为 `pause/resume/retry/set-model`；模型操作另传 `provider/model`。
- `GET /api/clipping/commands/{id}`：操作回执。
- `POST /api/clipping/agent/{device_id}/exchange`：电脑上传快照、回执并领取指令。

本地剪辑接口新增 `POST /api/jobs/{id}/pause` 和 `/resume`，沿用本机 Web 服务的访问方式。

## 验证与发布

服务器：`python -m unittest test_clipping test_startup test_materials`。
剪辑助手：`python -m unittest discover -s tests -q`。
手机：`flutter analyze --no-pub lib`、`flutter test --no-pub`、`flutter build apk --debug --no-pub`。

更新代码后需要部署服务器、重启电脑剪辑服务、重新安装手机包。代码推送不等于线上部署。使用手机移动网络验证在线同步、排队暂停、运行步骤边界暂停、继续、失败重试、模型变更、断网重连以及离线操作禁用。
