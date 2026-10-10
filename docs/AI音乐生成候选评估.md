# AI 音乐生成候选评估

日期：2026-10-10。仅为官方文档与本机硬件检查，未安装、下载模型或生成试听样本。

## 候选

用户未提供名称，目前最符合「热门 GitHub 开源 AI 音乐制作」描述的候选为 ACE-Step 1.5，不能确定就是用户所指项目。

官方仓库：https://github.com/ace-step/ACE-Step-1.5

它提供音乐生成模型、Gradio 界面和异步 REST API，可用于生成背景音乐后导入编辑器。官方列出时长、BPM、风格等控制，以及参考音频、局部重生成等能力。不同模型支持的任务不完全相同，应按实际选用模型验证。

官方 API：https://github.com/ace-step/ACE-Step-1.5/blob/main/docs/zh/API.md

API 支持提交任务、查询结果、获取音频，输出格式含 WAV/FLAC 等。建议用独立服务，生成候选 → 人工试听 → 保存音乐库 → 编辑器使用；不让每条视频必须等待即时生成。

## 本机条件

只读检查发现 CPU 为 Intel i7-12700，物理内存 34,088,579,072 字节（约 31.7 GiB），视频控制器只有 Intel UHD Graphics 770，未检测到 NVIDIA CUDA 显卡。未证明该核显能使用项目的 Intel XPU 加速。

官方支持 CPU 推理，但明确说明速度显著变慢。官方 Intel 测试设备与 UHD 770 不同，不能把「支持 Intel」推断成当前核显已经兼容。可在独立环境做短音频 CPU 实验；生产使用优先考虑预生成资源库或另一台有兼容 GPU 的机器提供接口。

安装文档：https://github.com/ace-step/ACE-Step-1.5/blob/main/docs/zh/INSTALL.md

GPU 文档：https://github.com/ace-step/ACE-Step-1.5/blob/main/docs/zh/GPU_COMPATIBILITY.md

Python 要求 3.11–3.12，核心模型磁盘约 10 GB，实际依赖与其他模型额外占用空间。应与 V1 的 Python 环境隔离。官方 Windows CUDA 便携包不适合直接按 CUDA 模式用于当前核显电脑。

## 适合本项目的使用方式

- 保存一组女装切片配乐预设，例如轻快时尚、轻柔钢琴、舒缓电子、节奏感纯音乐。
- 传入目标时长、风格、BPM 和无歌声的描述，生成若干候选。无歌声与成片质量需要试听验证，不保证提示词完全遵循。
- 保留 WAV/FLAC 作为库原件，同时生成低成本试听文件；剪辑与混音后最终编码。
- 记录提示词、模型版本、种子与生成时间，便于管理和复现。
- AI 配乐为可选功能，基础剪辑、素材导入、导出不依赖生成服务在线。
- 不把音乐生成/重生成能力当成直播口播保真人声分离，V1 音频问题仍按单独备忘录处理。

## 许可与判断边界

代码 LICENSE 为 MIT；模型卡也标注 MIT。软件与权重许可不构成每次输出都具有排他版权或绝无相似性的承诺，不应宣传成「保证无版权音乐」。

代码许可：https://github.com/ace-step/ACE-Step-1.5/blob/main/LICENSE

模型卡：https://huggingface.co/ACE-Step/Ace-Step1.5

结论：文档与接口层面适合接入为 AI 配乐服务。本机性能、无歌声效果、口播背景适配与持续运行可靠性尚未实测，不能认定已经可生产使用。
