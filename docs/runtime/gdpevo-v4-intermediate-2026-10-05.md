# T64 中间实验：全量 v3 文本技能对照

这是 T64 runner 的中间 attempt，不是 T64 gate，也没有修改生产 bank。证据目录为：

`/mnt/c/dev/rsi-eval/procurement_eval/runs/planning-session-20261005/attempt-gdpevo-v4-full-20261005f`

本轮使用完整 v3 的 20 train/20 held-out test，test 每个 arm 三次，共 60 个 test task 实例。fixed、few-shot、skills 和 dynamic 均使用同一配置的直接文本 Actor；test 期间没有反馈、重修或倒选。模型为配置中的 `deepseek-flash`，temperature=0，thinking disabled。生产 assignment 未改变。

## 结果

| arm | validation train 全题成功 | validation 均分 | held-out 实例 | held-out 全题成功 | held-out 均分 |
|---|---:|---:|---:|---:|---:|
| fixed | 3/20 | 0.803846 | 60 | 0/60 | 0.597436 |
| few-shot（当前实现为训练答案示例注入） | 8/20 | 0.857692 | 60 | 3/60 | 0.747436 |
| 聚焦 skills | 7/20 | 0.857692 | 60 | 9/60 | 0.801282 |
| dynamic（当前实现为任务级首技能选择） | 4/20 | 0.857692 | 60 | 1/60 | 0.658974 |

聚焦 skills 相对 fixed 的 held-out 加权均分提升 `+0.203846`，全题成功从 `0/60` 到 `9/60`。当时按旧逐题零退化规则拒绝候选；用户于 2026-10-05 取消该规则，后续按 train validation 总体表现选择，逐题退化仅作诊断。此前草稿把事后新增的 test 均分 +0.05/至少 2 次提高写成“预先声明门槛”不正确，已撤回；不使用 test 挑候选。此旧实验仍有下述方法缺口，新实验 j 另行报告。

## 未完成边界

1. 当前 runner 的 few-shot 仍是直接示例注入，尚未实现论文定义的“用五道 train gold answers 先提炼技能再测试”。
2. 当前 reflect-3 是 Curator 连续重写同一输入，尚未实现三轮 `train rollout → grader feedback → skill revision`。
3. 当前 dynamic 是任务级固定首技能，不能代表 TRACE 的逐回合 state-conditioned selector。
4. 当前四臂是文本 Actor，尚未把同一监督/技能 bank 接到 OpenSandbox 计算 Actor，因此不证明真实工具执行提升。

这些缺口保留在 T64 任务中，不能把本报告或 verify 通过当作完整 T64 完成。下一步必须先补齐上述语义，再做最终 gate；本报告只作为中间证据和候选设计反馈。
