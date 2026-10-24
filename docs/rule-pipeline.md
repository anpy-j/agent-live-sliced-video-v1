# V1 规则流程开关

配置保存在应用数据目录 `data/rule_pipeline.json`。各模块独立控制，默认状态不覆盖用户已保存的其他设置。

| 模块 | 接入位置 | 默认状态 | 行为 |
| --- | --- | --- | --- |
| 拼音限制 | S2 | 开 | 使用启用的拼音规则筛除命中子句 |
| 语义归组插件 | S3–S4 | 关 | 增加信息提取、语义归组节点，保留候选供 S4 同组择优 |
| 口头词清理 | S4 | 开 | 编排后按词级时间戳清理指定范围内的口头词 |
| 气口检测 | S4 | 关 | 口头词清理后保守校准音频边界 |

旧“重复限制流程开关”及对应的 S2 自动完整重复剔除已删除，不再固定保留首次出现的候选。原 S2 基础粗筛中的违禁词、时长、场控、病句和基础去重保持不变。

重复限制页面仅保留语义归组插件开关，以及脚本只读试匹配。试匹配条件只影响此次检测，不再提供“保存当前检测条件到 V1 S2”。试匹配不修改任务、子句或成片。

历史配置文件中的 `features.repetition` 会被忽略，保存其他设置时移除该旧字段；不再接受写入这个旧开关。历史快照和成片无需迁移或删除。

语义归组插件关闭时不显示新增节点、不增加 AI 调用，S3 契约与 S4 提示词保持原样。开启后按任务固定配置，运行中切换不会改变当前任务。新任务或手动重跑非渲染节点采用当前配置；仅重新渲染复用原 S4 结果。详见 [语义归组插件](semantic-groups-plugin.md)。

拼音、口头词和气口检测的试匹配与生产开关独立；试匹配不会自动开启正式流程。单条规则的启停与模块开关也独立。

API：`GET /api/rules/pipeline` 获取有效配置；`PUT /api/rules/pipeline/{pinyin|filler|breath|semantic_groups}` 接收布尔 `enabled`。版本和接入位置固定，不接受任意修改阶段。

验证：

```powershell
python -X utf8 -m unittest tests.test_rule_pipeline tests.test_repetition_rules tests.test_semantic_groups
node tests/rules-ui.test.cjs
node tests/filler-ui.test.cjs
```
