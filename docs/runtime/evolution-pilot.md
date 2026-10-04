# 真实文字策略学习与消融小样本

训练前 Git 基线是 `3c36215`。本轮用已归档的两个真实 Actor Episode（attempt-6、attempt-7）作为训练材料，提取原始操作参数、执行顺序和白名单反馈；raw 与 Curator 使用同一份训练输入。Curator 调用配置中的 `deepseek-flash`，生成文字技能，未读取测试输入或私有答案。技能经 JSON/内容检查后冻结到 Mongo bank，通过既有逐回合选择器注入 Actor。

公开测试是 `sufficient-budget` 和 `no-partial`，各组从独立 ERP/Mongo/OpenSandbox 初态运行一次。所有组使用相同 Actor 源码、模型、工具、审批和费用限额；raw/curated 的技能选择请求也计入调用和费用预算。

| 组 | 业务通过 | 原始额外失败 | 模型请求 | 保守估算 CNY |
| --- | --- | --- | --- | --- |
| fixed | 2/2 | 端口占用，0 次模型调用，另留一条补跑 | 15 | 1.184787 |
| raw | 1/2 | 不允许部分采购案例触及调用预算 | 29 | 2.674548 |
| curated | 2/2 | 无 | 26 | 1.576539 |

Curator 成功调用 1 次，保守估算 0.072630 CNY；首次截断 JSON 的失败调用保留，0.083547 CNY。本轮新增累计保守费用 5.592051 CNY，授权会话总记账 10.710675 CNY；实际账单字段为 null。fixed 的环境失败仍保留在全部尝试分母，因此包含环境故障的可靠性是 2/3。没有测出 curated 超过 fixed，不能声称学习收益、统计显著或完整 TRACE 复现。

完整证据目录：`/mnt/c/dev/rsi-eval/procurement_eval/runs/planning-session-20261004/attempt-evolution-pilot-040413`，包括训练 JSON、Curator 输出、文字 bank、全部 Episode/计算、ERP、原始模型 usage、配置冻结和 manifest。`retry-fixed-no-partial` 只补环境故障，raw 的失败没有重跑。最初 Curator 失败在相邻 `attempt-evolution-pilot-040148`。

代码阅读：`src/agent/evolution/curator.py` → `scripts/planning/evolution_pilot.py` → `src/agent/evolution/orchestration.py`。只读复核不需要重启业务服务或新增模型调用：

```bash
PLANNING_EVOLUTION_SESSION=/mnt/c/dev/rsi-eval/procurement_eval/runs/planning-session-20261004/attempt-evolution-pilot-040413 \
UV_PROJECT_ENVIRONMENT=.venv-linux-t45 .venv-linux-t45/bin/python scripts/gate.py T47
```

收据 `artifacts/tasks/T47/20261004T043016Z/receipt.json`：11 个组件测试与只读真实证据检查通过。限制：两条公开案例、一次重复，train 没有配对业务失败；完整环境快照治理、晋升/回滚、封存测试和动态异步精炼留待后续，不以本轮管线完成冒充这些能力。
