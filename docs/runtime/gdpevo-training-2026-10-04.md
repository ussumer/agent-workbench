# GDPevo 文字技能训练与消融：2026-10-04

一次真实训练与对照已经完成，**没有观察到训练增益**。训练前提交 `9f2ea2e`，标签 `pre-training-gdpevo-20261004`；生产 Harness 保留，实验技能仅保存为 [gdpevo-text-skill-v1.json](../../fixtures/planning/gdpevo-text-skill-v1.json)，未切换生产活动技能指针。

| 组别 | 整题全对 | 加权均分 | 输入增量 |
| --- | --- | --- | --- |
| base（训练前 T53） | 2/5 | 0.76 | 无训练经验 |
| policy-visible（T53诊断） | 2/5 | 0.76 | 企业规则文字 |
| retrieval/raw | 2/5 | 0.76 | 五题训练经历和诊断 |
| curated | 2/5 | 0.76 | 一次真实 Curator 提炼的文字技能 |

五个 train、八次训练调用：train-01 0.267→0.267，train-02 1.000，train-03 1.000，train-04 0.467→0.467，train-05 0.467→1.000。最终训练整题成功 3/5，失败保留。Curator 一次，raw/curated 各五次 test，共 19 次真实调用，没有网络或格式失败。失败字段诊断可用于训练，不提供参考答案；Curator 请求只含 train，测试输出与私有 rubric 不进入其请求。

模型身份按配置为 `deepseek-flash`，temperature 0，thinking disabled，无工具、one-shot test。不是权重微调；该轮属于已用于 T53 校准的开发 test，不能声称封存 test、生产 Actor/OpenSandbox/ERP 端到端能力或统计显著学习收益。原技术栈和生产计算/下单链路没有替换。

本轮保守费用估算 1.030536 CNY：训练 0.203580、Curator 0.099000、raw 0.552033、curated 0.175923。会话账本累计保守预留/结算 18.867582 / 50 CNY，单 attempt 上限 2 CNY。用户报告其观察到的实际总花费为 0.51 CNY；原始 provider usage 未包含实际账单，`actual_cost_cny` 仍为 null。估算与用户观察不互相冒充。

证据目录：`/mnt/c/dev/rsi-eval/procurement_eval/runs/planning-session-20261004/attempt-gdpevo-training-20261004`。其中保存冻结输入和评分器、每次请求/响应/usage、反馈、Curator、bank、report 和 manifest。manifest SHA-256：`d1cd0da33fc2c127570d02da46a2cd499aebd74ffa81116df0c0709c9812ae29`。入口 [gdpevo_training.py](../../scripts/planning/gdpevo_training.py)；[T55](../plan/tasks/T55.md) gate `artifacts/tasks/T55/20261004T131342Z/receipt.json` 为 6 unit 和只读真实证据检查通过，0 failed/0 skipped。初轮缺环境配置的 blocked receipt `artifacts/tasks/T55/20261004T131100Z/receipt.json` 保留。

本轮结束后没有再次调用模型或循环调参。扩题 T54 由另一 Agent 在原工作区推进；训练代码和结果在 `training/gdpevo-v2` 分支，可与训练前标签直接 diff。下一轮若训练更大任务集，应重新冻结扩展数据和基线，并继续保留失败及 raw/curated 对照。
