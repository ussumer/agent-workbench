# T64 faithful v4：多轮技能提炼与重复消融

本次结果来自同一份 v3 任务集、同一 `deepseek-flash` 配置和同一独立评分器。20 道 train 只用于 fixed、三轮真实 rollout→诊断→Curator 修订、gold-answer few-shot 技能提炼和 train validation；20 道 held-out test 没有回流反馈、重修或倒选，每个 arm 重复三次。

证据目录：

`/mnt/c/dev/rsi-eval/procurement_eval/runs/planning-session-20261005/attempt-gdpevo-v4-faithful-20261005j`

运行命令：

```text
uv run --frozen python scripts/planning/gdpevo_v4_training.py verify \
  --output /mnt/c/dev/rsi-eval/procurement_eval/runs/planning-session-20261005/attempt-gdpevo-v4-faithful-20261005j
```

verify 通过，矩阵完整：validation 20×4，held-out 20×4×3；所有调用、响应、失败和版本哈希保留。`h` 和 `i` 是恢复器目录冲突的失败尝试，未覆盖；`j` 从 `g` 复用已完成的 train/Curator 证据，只补做未完成的 validation/test。

| arm | validation 成功 | validation 均分 | held-out 成功 | held-out 均分 |
|---|---:|---:|---:|---:|
| fixed | 3/20 | 0.826923 | 0/60 | 0.592308 |
| few-shot 技能 | 5/20 | 0.853846 | 10/60 | 0.734615 |
| reflect-3 技能 | 6/20 | 0.861538 | 10/60 | 0.796154 |
| dynamic selector | 5/20 | 0.857692 | 5/60 | 0.770513 |

reflect-3 技能相对 fixed 的 held-out 加权均分提升 `+0.203846`，三次重复分别为 `+0.203846`、`+0.207692`、`+0.200000`；整题成功从 `0/60` 提升到 `10/60`。few-shot 提升 `+0.142308`，dynamic 提升 `+0.178205`。提升在三次重复中方向一致，说明这次不是单次偶然通过。

逐题退化仍在 validation/test 中完整保留，用于定位技能误导和任务组差异。用户已明确取消“逐题零退化即否决”规则；T64 当前按匹配 train validation 的总体表现选择候选，test 只做冻结后的泛化报告，不能参与选择。此次结果达到工程层面的总体提升信号，但不宣称统计显著性，也不把单次 validation 当作稳定保证。

这份 faithful attempt 仍有一个范围边界：四个 arm 是文本 Actor，尚未把同一 bank 全部接入 DeepAgents/OpenSandbox 计算 Actor。已有独立小样本证明真实计算路径可运行：T61 的两题同题 text/compute 对照含 5 次完成的 OpenSandbox computation，T63 的三臂闭环含 5 次完成 computation；这些证据证明执行边界，不替代本次 20×4×3 的全臂计算实验。因此 T64 仍不能登记为完整生产训练完成，下一步是把当前 faithful bank 接入同一计算 Actor，再做等协议的计算臂复验。

