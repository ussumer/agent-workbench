# Harness 质量评审与自进化升级建议

评审日期：2026-10-01。对象是当前工作区，包括未提交、未跟踪以及被忽略的源码；不是仅评审 Git HEAD。

## 结论

这是一个有真实业务约束、审批和执行隔离的采购 Demo 底座，值得继续建设。但当前 `25 done` 高估了完整运行链路的成熟度：存在数据保留、技能发现、记忆更新和资源限制的接线缺口。当前技能模块完成的是文件校验、发布、分配与恢复，尚未形成“经验反馈 → 候选更新 → 独立评测 → 晋升 → 回滚”的自进化闭环。

建议先修正基线与评测，再做离线、受约束的技能/经验进化。保留 Java ERP、Vue、MongoDB、DeepAgents/LangGraph、MCP 和 OpenSandbox；第一阶段固定模型配置和业务规则，研究可持久化的程序性经验与技能更新。

## 阅读与证据边界

阅读了计划入口、执行协议、交接、架构、验收规格和接口契约，并沿 ERP/MCP/审批、Agent 装配、Store/技能、沙箱、聊天/SSE、异步任务、前端状态和演示评测链路审查关键实现与相关测试。另核对了当前安装的 DeepAgents 0.7.14 与 LangChain 1.4.0 的装配源码，版本与锁文件相符。

学习日志来自 `/mnt/l/Documents/MyVault/Work/Rsi-Harness`：已读 `s1-harness的基础项目.md` 和 `s2-调研学习中.md`；`s3-搭建评测/未命名.md` 当前为 0 字节。未将笔记中的论文批评全部当成已核实的论文结论，也未逐张检查笔记插图或逐行复核所有测试。以下是仓库系统评审，不是完整安全审计。

本次没有启动 live 演示、重跑所有任务 gate、调用采购模型、搜索或图表计费服务，也没有启动/清理数据库与沙箱。没有修改应用源码、已有任务状态或人工 review 字段。新增的诊断脚本仅使用组件边界替身，不构成真实服务通过的证据。

## 本次实际检查

| 检查 | 本次结果 | 能证明的范围 |
|---|---|---|
| `python3 scripts/plan_guard.py check` | 通过，25 tasks / 25 done | 计划与已有收据结构有效 |
| `python3 scripts/plan_guard.py next` | 无 eligible pending task | 本轮没有领取实现任务 |
| `python3 scripts/plan_guard.py packet T24` | 已读取 | 复核最后一个完成任务的验收范围 |
| `.venv/Scripts/python.exe -m pytest tests/plan tests/contract -q` | 43 passed | 计划、MCP 契约回归；不是全应用回归 |
| `.venv/Scripts/python.exe -m ruff check src scripts tests --output-format concise` | 退出 1，81 项 | 当前静态规范不通过；多数是导入/未用变量，也有未定义类型与闭包绑定问题 |
| `.venv/Scripts/python.exe -m mypy src scripts --no-error-summary` | 退出 1 | 当前类型检查不通过；其中包含技能函数返回形状不一致 |
| WSL 下前端 `npm run test -- --run` / `npm run build` | 均失败 | 现有 node_modules 缺 Linux Rolldown binding，属于运行环境问题 |
| Windows 下 `npm test` | 退出 1，零测试，5 个 worker 错误 | 默认 Node 20.18.1 不满足声明的版本；`ERR_REQUIRE_ESM` |
| Windows 下 `npm run build` | 退出 0 | vue-tsc 与 Vite 构建通过；仍提示 Node 版本不足 |
| 文档指定 Node 22.22.2 路径重试 | 路径不存在 | 无法用该历史路径复现前端单测，不能报告通过 |
| `.venv/Scripts/python.exe artifacts/reviews/20261001/probe.py` | 退出 0，三个观察结果 | 验证返回形状、冒烟判定、YAML→SubAgent 配置缺失；不调用服务 |

Windows 程序首次从受限 WSL 调用曾报 `UtilBindVsockAnyPort`；获批准后执行上面的 Python 检查。环境失败与后续检查结果分开保留，不能把环境失败直接解释成源码缺陷。

## 值得保留的实现

1. **业务事实在 ERP。** `erp/.../orders/OrdersService.java` 使用 BigDecimal、事务、整单更新、乐观版本和 owner 作用域的幂等账本。金额不是由模型自述确定。
2. **审批在执行边界。** `src/agent/approval/` 冻结操作载荷，MCP grant 绑定 owner、工具、目标、操作 ID、内容 hash 和有效期。权限与金额规则可以作为后续进化的固定边界。
3. **状态职责清楚。** Checkpointer、应用会话记录、Store 和沙箱执行副本分开，避免把临时容器文件当成长期记忆。
4. **恢复结构可复用。** 沙箱稳定 proxy、generation、Mongo 登记、技能 manifest/hash、读回验证与 assignment CAS，为候选版本实验提供已有基础。
5. **有业务验收的意识。** 演示通过 ERP 状态、文件和工具轨迹核对结果，分开统计真实试验、框架测试和人工审阅。这个方向应保留。

## 优先修正的问题

### F01：开发启动路径删除了应保留的 Mongo 数据（高优先级）

`scripts/dev.py:256` 调用 `tests/live/stack.py` 的 `running_stack(database_name=...)`。后者在第 454 行启动时、559 行退出时均无条件调用 `drop_test_database(settings)`。数据库名字固定，并没有禁用删除。

`tests/acceptance/test_t24.py:208` 只断言重启后的数据库名字相同，未断言其中的会话、偏好、技能、审批或任意哨兵文档仍存在。因此该测试能在数据库被清空重建后通过。README 的“数据默认保留”与实现冲突。

这是静态调用链确认，本次未运行删除数据库的路径。修复应将持久运行生命周期与测试清理策略分离；验收应写入真实记录，停机再启动后核对原 ID 与内容，覆盖 ERP 与 Mongo 各自的数据保留承诺。

### F02：Git 忽略规则吞掉业务源码（高优先级）

`.gitignore:27` 的 `artifacts/` 匹配任意层级同名目录，也匹配 `src/agent/artifacts/`。`git check-ignore -v src/agent/artifacts/service.py` 已确认；`git ls-files src/agent/artifacts` 无输出。

该目录包含产件 service/models/store，而多个应用模块依赖它。当前工作区能导入，不代表干净 checkout 能导入。建议只忽略根目录 `/artifacts/`，并审查源码跟踪与运行态文件；不在本轮自动提交、删除或重置用户文件。

### F03：技能配置没有接入框架发现机制（高优先级）

YAML 声明了 `skills`，`build_config` 也解析保存，但 `src/agent/subagents/loader.py:306` 构建 SubAgent spec 时没有传递它。诊断得到两个子代理的 emitted skills 均为 null。

`src/agent/main_agent.py` 的 `skills` 参数只用于渲染提示词元数据，没有传给 `create_deep_agent(skills=...)`；实际 `_assemble` 也没有提供元数据目录。已安装框架只在对应参数存在时安装 SkillsMiddleware。

结果是“文件同步到沙箱”成立，但框架的渐进发现机制没有按这些配置生效；模型仍可能靠提示词或目录探索完成任务。修复不能直接把 YAML slug 当路径传入：应解析真实预置目录和 owner/scope 的已分配目录，并用真实模型请求捕获验证元数据注入、按需正文读取及发布后下一轮刷新。

### F04：记忆、熔断和共享预算有组件实现，缺运行接线（高优先级）

- `MemoryUpdateMiddleware` 没有框架 hook，注释要求 run 层调用 `apply()`；应用 `src/` 中没有该调用点。`evaluate_run()` 与显式偏好更新入口同样未接入聊天运行链路。
- `_assemble` 只向子代理传入 `[approval]`，没有把上下文注入与预算中间件传给声明式子代理；安装版框架不会自动继承这些父代理自定义中间件。主子共享预算和子代理读取偏好不能据此成立。
- 即使把同一预算中间件实例传给主子代理，也不能直接证明总预算共享：已安装 `ModelCallLimitMiddleware` 将计数保存在 graph state 的 `thread_model_call_count` / `run_model_call_count` 中，而不是实例计数器。应验证跨图累计机制，不能照搬仓库注释中的解释。
- BreakerRegistry 在装配时被创建，但被 `isinstance(AgentMiddleware)` 过滤；未发现沙箱调用方连接 `record_failure` / `record_success`。RunBudget 与摘要前归档辅助函数也未发现应用调用点。

这些是调用链/安装版装配源码发现；不等于每一项都已用服务故障现场复现。补测试应通过实际装配入口触发成功采购记忆更新、子代理偏好、跨主子累计限额与沙箱熔断，不能只直接调用组件方法。

### F05：技能校验错误返回形状不一致（中高优先级）

`src/agent/tools/assign_skill.py:172` 在捕获 `SkillValidationError` 时返回 `_fail(...)`，即 JSON 字符串；调用方第 396 行按 dict 读取 `result["ok"]`。诊断确认该分支返回 str；沿真实调用方会触发类型错误，而不是返回可供模型修复的业务错误。

应统一内部结构化结果与外层序列化位置，用缺 SKILL.md、slug 不匹配、路径越界等实际错误验证工具返回。

### F06：冒烟可以通过，但不能证明技能有用（进化前必须补齐）

`src/agent/skills/pipeline.py:464` 的判定只有“示例退出 0、损坏 JSON 退出非 0”。未要求产件存在或数值正确；observed_total 只是记录字段。边界诊断验证：outputs={}、observed_total=null 时 passed=true。

坏输入是 JSON 语法损坏，不覆盖业务上非法但可解析的输入。没有脚本入口的技能也可跳过执行后发布。对于纯指导性技能，这种许可需要独立的行为评测；不能统一理解成质量已验证。

现有冒烟应保留为安装检查，再增加由评测侧提供的任务与 oracle，分别检查输出 schema、金额、边界输入和端到端表现。候选自己提供的 example 不能决定晋升。

### F07：历史演示成绩不足以做自进化提升的基线

`artifacts/live/r1/summary.json` 记录 23/24，是保留试验集上的历史演示结果，不是本次重跑结果。目录还有 `replaced/`，其中留有失败试验和旧判定。保留这些记录是好事，但不能据此把全部开发尝试称为独立 24 次的无选择成功率。

`scripts/demo.py:1434` 重用已有 round run_id；第 1476 行将同场景同 trial 的输出写到固定目录。反复 `--only` 跑同一编号可以覆盖历史试验，汇总只计算当前文件。T23 的多数测试读已有证据，而不是重新执行任务；41 个框架测试也不是 41 次独立模型试验。

自进化评测必须创建不可覆盖的 attempt ID、记录每次真实运行与候选 lineage，把开发迭代、环境故障、候选选择和冻结测试区分开。不能用反复修改话术后保留的展示轮次证明泛化提升。

### F08：异步分析适合演示协议，不适合作为学习器

`infra/agent-protocol/analyst_graph.py` 是固定 plan→read→analyse→finish 管线，没有模型，用户 instruction 不改变实际分析步骤。它真实使用 LangGraph/Protocol，但不是模型自主分析。

`AsyncTaskService.launch` 在 Mongo 唯一记录插入前启动远端 run；并发相同 request_id 可能返回同一业务 task，却已启动多个远端 run。独立图还从进程环境读取 owner，默认 demo-a；当前 start_run 未传递每次任务的 owner/thread 配置。库存预警当前是共享目录数据，所以不声称已观察到私人订单泄漏，但逐任务归属机制需要补齐。

若以后把 teacher 放后台，应先解决每 run 的身份传播、远端副作用幂等与取消语义。不要直接复用这条固定分析管线假称 teacher 学习。

### F09：版本发布还不具备实验候选管理语义

版本号按 `len(versions)` 分配（`pipeline.py:661`），文件前缀存在性检查与写入分离，且失败半写不一定有 metadata 行；并发发布和半写重试存在冲突风险。当前 CAS 更新 assignment 不等于完整发布事务。

进化需要独立 candidate_id、不可变内容、父版本、来源 episode、评测结果与稳定晋升动作。评测期间不能更新当前 assignment；使用固定 snapshot 评估后再以预期 revision 晋升。现有历史版本与 CAS 可以复用，但应单独核验并发与失败恢复。

### F10：完成门槛与真实执行清单不同

`tasks.json` 的 checks 是 acceptance、java-build 与 frontend-build，没有单独的 ruff/mypy/frontend-test check，尽管 verification.md 要求格式、类型与跨语言验证。本次静态失败证明“收据全绿”没有覆盖全部声明的质量门槛。

多数静态告警不应升级成业务故障，但校验返回形状、未接入能力和弱重启断言说明已有测试也有实际盲区。应补齐固定检查和装配回归，保留原收据，不将新失败改写成旧轮次通过。

## 与学习日志的对应

笔记把参数、上下文、记忆、技能和 scaffold 分开研究，这个分类有助于限定实验对象。当前仓库最接近技能版本生命周期，离“经验如何被提炼、选取以及证明有效”仍有明显空白。近期第一阶段宜固定模型参数与 Harness 业务边界，比较程序性记忆和技能更新。

[ReasoningBank](https://arxiv.org/abs/2509.25140) 的摘要明确包括从成功/失败经验提炼策略和后续检索，不是简单保存原始历史。可借鉴这个机制，但不直接移植其收益数字。

[GDPevo](https://arxiv.org/abs/2608.03764) 使用训练中分散的原子业务规则在留出任务中重新组合。这个机制适合采购域评测设计；它的跨行业实验成绩不能当成这个仓库的预期提升。其余论文在本次仅核对部分原文入口，未复核所有实验与消融，建议保留笔记的判断为待验证假设。

## 建议的升级顺序

### 阶段 0：得到可信的固定基线

先处理 F01–F05 和装配/版本问题；补齐质量门槛与真实恢复测试，冻结源码、锁文件、技能集合、模型配置和数据生成器版本。将实际应用装配放在运行时模块，让 dev 与 tests 调用同一个应用装配，清理策略留在测试侧。

阶段产物：一个能重启保留数据、能够发现技能、真实记录成功任务经验且预算有效的 baseline snapshot。没有这个基线，后续成绩变化无法归因。

### 阶段 1：采购领域评测 v1

保留 D01–D08 作为兼容回归，另建具有变化输入的评测集。任务覆盖信息缺失、供货关系、停用供应商、补货金额、整单修改、审批拒绝、重试、技能跨会话复用与沙箱恢复。优先改变数据与规则组合，而不是只改问法。维持现有业务范围，复杂新规则先作为明确扩展设计讨论。

分出 train / validation / sealed test：学习器只看训练轨迹；validation 用于选择候选；封存 test 用于最终评价。测试集若长期反复用于选择版本，就已是开发集，必须另留未触碰的测试批次。

至少比较：冻结 baseline、仅追加原始历史、提炼经验、提炼经验加技能更新。控制模型身份、可用工具、执行预算与任务初始状态，比较同一任务实例，并记录 teacher/reflection/search 的额外成本。初期每组先做小规模端到端调试，再按预算扩大重复次数；不预先虚构固定的统计显著门槛。

业务判断来自 ERP 状态、审批/幂等账本、数值 oracle、产件内容与权限检查。教师模型可以解释失败、提出候选；金额、违规写和跨用户访问不能只由 LLM judge 判定。报告成功率、每类失败、成本、延迟以及预算耗尽；预算耗尽不能仅因 graph 正常结束而记作任务成功。

### 阶段 2：离线 teacher + 候选技能闭环

建议数据对象：

- Episode：任务实例、snapshot、可观察工具轨迹、结果、反馈、成本与时长。无需依赖模型私有推理文本。
- Experience：适用条件、方法、反例、失败边界、来源 episode ID。
- Candidate：候选内容 hash、父版本、修改原因、可编辑范围与训练来源。
- Evaluation：独立任务集版本、各 attempt、比较对象、预算与结果。
- Promotion：预期当前 revision、晋升理由、旧 snapshot 和回滚目标。

闭环：训练任务执行 → 由 oracle 给反馈 → teacher 对照成功/失败轨迹提出小范围更新 → OpenSandbox 中验证候选 → validation 比较 → 通过固定业务不变量和提升标准后晋升 → 后续观察退化时回滚。

第一版允许更新用户作用域内技能与程序性经验。ERP 规则、审批、权限、评测 oracle 和 gate 留在固定控制面。可先由人工审阅晋升，再逐步自动化满足已定义条件的技能晋升；新审批策略需要显式计划确认。

沿用现有 SkillStore、manifest/hash、版本读回和 assignment CAS，但将“发布候选”与“变成当前版本”拆开。学生每轮获得明确 snapshot；跨会话读取的是被晋升的版本，不能混用评测中的临时内容。

### 阶段 3：连续学习与 scaffold 搜索

在阶段 2 已证明留出任务改善后，再研究经验合并、冲突淘汰、检索、技能触发率与跨代遗忘。每一代同时测新任务与旧任务，记录未触发的技能，不把“技能数量增长”当能力提升。

自动修改 Harness 源码、多个候选的树/DAG 搜索和模型参数训练属于后续实验。届时需要候选独立源码环境、固定外部评测与明确总搜索预算，不能让候选直接改自身裁判。Java ERP、MongoDB、MCP、OpenSandbox 等既有要求继续保留。

## 最先值得做的一个升级任务

先设计“采购自进化评测 v1”的任务生成、数据划分、oracle、attempt 格式与基线固定方式，并在同一入口上补足上述装配缺陷。随后完成一次完整对照：训练若干任务 → 提炼一个候选技能 → 冻结候选 → 在未参与优化的任务上与 baseline 比较，连同成本和退化一起报告。

这会直接回答：**系统究竟从经验中学到了什么，以及它是否帮助了新的任务。** 比先加一个反思 prompt 或直接开始改 Harness 源码，更容易得到可解释、可复现的学习结果。
