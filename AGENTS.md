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

## **训练与评估认知固定声明（强调存在，后续任务必须遵守）**

以下规则是对上面执行原则的具体化，不能被“再跑一次”“先把检查做完”或任务文字中的默认假设弱化：

- **业务结果先于管线完整。** Harness、任务生成器、validator、selector、Curator 或单元测试通过，只能证明对应组件或协议通过；只有同一任务集上的真实 Actor 业务结果改善，才能支持“训练有效”。
- **缩小实验必须明示。** 文本 Actor、少量题目、单轮、单次重复、没有 OpenSandbox/DeepAgents、没有真实工具调用的实验，必须在目录、报告和交接记录中标为 exploratory/intermediate；不能称为完整训练、正式校准或生产能力。
- **比较必须同源。** baseline、raw、curated、skills、dynamic 等臂必须使用相同任务集版本、split、模型配置、运行时和评分器；不同任务集、不同难度或不同重复数的数字不得拼成提升结论。
- **已知缺口必须变成检查或任务。** 已经发现的 few-shot、逐回合反馈、真实 selector、持久计算、失败提交、迟到结果、进程终止等缺口，要么进入可运行的主线任务并产生证据，要么明确记录为未完成；不能只在口头计划里宣称“之后补”。
- **先做最小闭环。** 新机制先用一个能端到端改变业务结果的最小样本验证；若两次有依据的修复仍失败，或约 20 分钟没有实质进展，立即保留失败现场并报告卡点、影响和取舍，停止该方向。
- **证据诚实且可追溯。** 失败 attempt、调用、费用、源码版本和评分都保留；不把测试数量、文件数量、形式化状态或 scripted 回归当作模型学习证据，不覆盖失败来制造“完成”。
- **有效改动要落盘。** 与主线相关的有效代码、任务集或规则修改，在完成最小必要检查后创建 Git commit；运行时临时文件、用户已有修改和失败证据不得混入该提交。

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
