# T65：充分 few-shot 的完整 compute 再训练

同一 v3 合成任务集、20 train/20 development test、DeepAgents/OpenSandbox compute Actor、配置模型 `deepseek-flash`、temperature=0、thinking disabled，test 四臂各三次重复。T64 原代码、结果和 gate 未覆盖。当前 test 已在开发中使用过，不称新的独立封存泛化。

代码先提交 `60ef9bf`，服务路径修复 `3b3e32d` 后运行。T65 的修改是组合干预：few-shot Curator 加公开企业规则、few-shot Actor 加完整同组 train 输入和正确决策、修正失败评分字段匹配。validation 原始示例排除当前题，test 示例仅来自 train。全部训练后重新冻结 bank；dynamic 使用本轮 reflect bank，并未注入这些原始 few-shot 示例。因此不能将 dynamic 的变化归因于更多原始示例，也不能单独归因于示例数量。

运行目录：`artifacts/experiments/t65-compute-dense-20261007`。

## 全量结果

60 为同一 20 题的三次重复数，不是 60 道独立题。所有失败保留在分母中。

| arm | validation 成功/均分 | test repeat 1 | repeat 2 | repeat 3 | test 成功率 | test 均分 |
|---|---|---:|---:|---:|---:|---:|
| fixed | 17/20；0.965385 | 3/20 | 1/20 | 4/20 | 8/60；13.3% | 0.726923 |
| fewshot | 16/20；0.953846 | 16/20 | 12/20 | 13/20 | 41/60；68.3% | 0.928205 |
| skills | 16/20；0.953846 | 12/20 | 10/20 | 9/20 | 31/60；51.7% | 0.893590 |
| dynamic | 15/20；0.946154 | 13/20 | 14/20 | 14/20 | 41/60；68.3% | 0.914103 |

同任务集 T64 compute 的描述性比较（不同训练 attempt）：

| arm | T64 test 成功/均分 | T65 test 成功/均分 |
|---|---|---|
| fixed | 7/60；0.758974 | 8/60；0.726923 |
| fewshot | 0/60；0.601282 | 41/60；0.928205 |
| skills | 35/60；0.876923 | 31/60；0.893590 |
| dynamic | 35/60；0.888462 | 41/60；0.914103 |

充分 few-shot 在本轮三次 test 都优于匹配 fixed，原 few-shot 监督不足是有证据的诊断。dynamic 从 35/60 到 41/60，增加 6 个成功执行，但仍有约三分之一失败。skills 均分提高而成功数降低，不能用均分掩盖业务失败。

候选只能按 train validation 总体选择；三个候选均低于本轮 fixed，`candidate-selection.json` 的 promote 均为 false。没有用 test 倒选，没有安装生产 bank；`learning_gain_proven=false`、`production_assignment_changed=false`。

## 剩余失败

| group | fewshot 成功 | dynamic 成功 |
|---|---:|---:|
| quotes | 14/15 | 11/15 |
| packages | 8/15 | 10/15 |
| kits | 7/15 | 9/15 |
| revisions | 12/15 | 11/15 |

fewshot 的失败评分点为 procurement 18 次、freight_audit 1 次。dynamic 的 scored 行失败评分点为 procurement 16 次、source_selection 2 次、freight_audit 1 次；另有一条未 scored 的失败不能按点归因。配套、包装、全局分配仍是缺口，更多示例不能完全解决。

test 240 行中 236 scored、3 format_failed、1 environment_failed。另有一个 reflect environment_failed。两个 environment_failed 实际都是 `GraphRecursionError`，属于 Actor 退化/图护栏终止，不能解释为服务不可达。Docker 临时端口冲突由已有启动机制恢复，无人工重跑题目。30 次 ModelCallLimit 只计 Actor；dynamic selector 另计，最大 episode 合计 48 次模型请求，由 recursion_limit=120 截断。原始分类及结果不改写。

## 用量与证据

合并全部 `model_calls.jsonl`，每份日志按 call 去重：Actor/selector 2684 次 + Curator 20 次 = **2704 次**；每次都有 HTTP 200 和 usage。输入 22,492,359、输出 1,029,377 tokens；保守全价估算 **230.224410 CNY**，实际账单未知。原始 `report.usage.total` 只统计共享 Actor/selector 网关的 228.066219 CNY；这里补齐 Curator 费用，原始证据保持不变。

独立 verify 通过：240 test rows、三次重复、完整四臂；399 scored evidence rows 中 396 个包含真实 computation execution。Gate `artifacts/tasks/T65/20261007T112429Z/receipt.json` passed：8/8 单元测试、compute evidence verify 通过。结构检查为 66 tasks、65 done；T51 历史阻塞未处理。

保留零调用服务失败目录 `...-startup-denied`、`...-sandbox-config-relative-failed`，以及 gate 的凭据/uv 缓存权限失败 receipts。最终 gate 在内存加载既有凭据和可写缓存权限下通过，没有重跑训练或新增模型调用。
