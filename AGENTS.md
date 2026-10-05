# Coding Instructions

Implement the procurement Harness demo from `docs/plan/README.md`.
Read `docs/plan/execution.md` and `docs/plan/HANDOFF.md` before each task.
Run `python3 scripts/plan_guard.py check`, then `next`, then `packet TXX`.
Work on one eligible task at a time. The user reviews and learns from the code.

## **强制执行原则：以有效产出为重**

### **用户强调性声明（固定执行，不得弱化）**

本声明是用户对执行方式的明确要求，不是建议，也不是可由任务说明自行覆盖的偏好：

- **主线有效产出高于边缘完美。** 先交付能运行、能演示、能改变业务结果的结果；不要为了边缘情况、低影响的严格性或形式上的完整性，牺牲主线推进速度。
- **主动止损是硬规则。** 同一问题两次有依据的修复仍失败，或连续约 20 分钟没有实质进展，立即停止该问题上的尝试，向用户报告卡点、已尝试内容、影响和取舍，等待讨论。
- **禁止后台循环。** 不得重复启动同一失败命令、堆叠未经验证的补丁、反复执行已通过的检查，或在用户不可见的后台继续消耗时间；除非用户明确改变决定，否则不得自行恢复已暂停目标。
- **结果必须诚实。** 失败保留，组件通过不等于业务目标完成；不把测试数量、文件数量或形式化状态当作业务成果，不把 scripted 回归当作真实模型成果。

后续条款必须按这组要求解释；如果边缘问题开始拖慢主线，先记录，再停下沟通。任何“继续跑一遍看看”的动作都必须有明确的新证据或用户决定作为依据。

### **训练主线边界（固定强调性声明）**

- **取消逐题零退化否决（用户 2026-10-05 明确决定）。** 训练候选按匹配 train validation 的总体表现选择；逐题退化只记录诊断。多次重复全部计入，不能挑最好一次，也不能用 test 选候选。单次结果不当作稳定能力或停止探索的依据。

- **局部机制通过不等于训练有效。** 单次闭环、少量题目、组件测试、Curator 成功或候选生成，只能证明对应机制运行；没有同题 fixed/训练对照、足够覆盖、重复评估和独立验证，不得汇报为学习收益。
- **缩小版实验必须显式降级。** 如果为了先验证管线只跑少量题目，必须在任务、报告和交接中写明“机制验证/小样本”，并立即登记完整实验的下一条主线任务；不得让缩小版 gate 代替完整目标。
- **已知缺口必须转成行动。** 已经识别的 few-shot、reflect、多轮迭代、重复对照、validation/sealed-test、credit assignment 或训练/执行错位，不能只留在诊断文档；下一步必须有明确任务、输入规模和验收证据。
- **不得混用不同任务集。** 必须在每次比较中明确任务集、train/test 划分、arm、模型配置和运行次数。不同任务集的 baseline 不能互相解释；不得把一组任务的 8/8 外推为另一组任务的训练效果。
- **用户解除限制后立即更新策略。** 用户已撤销预算或调用限制时，不得继续沿用旧限制来缩小实验；应重新计算可执行的完整主线范围，并如实记录实际用量。遇到新的网关/服务限制才停止并报告。
- **主线目标优先于局部 gate。** 如果任务包的验收范围小于用户已确认的业务目标，完成该 gate 后仍须把目标缺口列为未完成，不得因为计划显示 done 就停止或宣称整体完成。

**主线交付优先。** 先做能运行、能演示、能改变业务结果的主线工作；不要把
边缘情况、低影响的严格性或重复核验变成主要工作。已经通过的检查不重复跑，
除非本次改动直接影响它。

**主动止损。** 同一个问题经过两次有依据的修复尝试仍未解决，或连续约 20 分钟
没有产生实质进展，必须停止该问题上的尝试，向用户说明卡点、已尝试内容、影响和可选取舍，
等待讨论。不得继续堆补丁、重复启动服务或循环执行同一失败命令。

**控制改动规模。** 优先复用现有实现，围绕当前主线做最小必要改动；不为假设
风险另造复杂机制，不用测试数量、文件数量或形式化状态替代可见业务成果。

**结果诚实且可读。** 失败尝试保留并说明；完成既定必要检查后，只有新改动、失败
或具体未决问题才需要扩大或重复验证。边缘问题记录后按实际影响安排；涉及必要
验收或范围取舍时，与用户讨论，不自行降低标准。
不能把 scripted 回归当成真实模型成果，也不能把组件通过当成完整目标完成。

**遇到阻塞先沟通。** 缺凭据、服务不可达、依赖冲突或需求取舍若持续阻塞主线，
主动停下与用户讨论，而不是在后台长时间运行或自行扩大范围。已明确暂停的目标，
须等用户 resume 后才恢复；修订执行规则不代表恢复目标。

## Constraints

- Preserve the course stack and capabilities. Obtain user agreement before a
  technology substitution or scope reduction. Java ERP, Vue, MongoDB,
  DeepAgents/LangGraph, MCP and OpenSandbox are required.
- Runtime model identity comes from configuration. Do not guess an API model ID
  from the shorthand DSV4.1flash.
- Match contracts under `docs/plan/contracts/`. Document interface changes and
  check dependent tasks. Inspect installed library signatures; lock versions.
- Do not replace ERP state with fixed responses, MongoDB with process memory,
  OpenSandbox with host execution, or live model calls with scripted workflows.
- Preserve unrelated files and user edits. Keep secrets out of logs and evidence.

## Completion

Application checks are specified in `docs/plan/verification.md` and established
by T00. A documented command is not an implemented check. Zero collected tests,
skipped mandatory checks, missing credentials, and unavailable services are not
passes. Keep failed attempts visible. Never weaken acceptance to pass a task.

Mark a task done only with required evidence and passing dependency gates.
Update `docs/plan/state.json` and `HANDOFF.md` when completing or pausing a task.
The plan checker validates structure, not business correctness or honest work.
