# 采购 Harness 自进化实施规格

日期：2026-10-03。状态：用户已整体接受，可交给编码 Agent 从 SE00 开工。

机制核查补充：用户随后要求重新核对论文。见 [paper-fidelity-audit-2026-10-03.md](paper-fidelity-audit-2026-10-03.md)。旧稿对 TRACE 算法及 GDPevo 学习问题设计规定不足，Prime 仅为有限参考；SE00 通用基础保留，SE01 证据工作可继续，SE03/SE04 不得按笼统旧稿落实算法。新增业务与在线演化范围尚未自动接受。

用户已确认 Q1–Q10 的设计选择，并在整体审阅确认问题后回复“可以”，接受本文作为交给另一 Agent 的实施规格。本文未授权新的模型费用，也不声明应用实现或实验增益已经通过验收。核查事实与阅读边界见 [研究记录](self-evolution-research-2026-10-02.md)。

## 1. 目标与范围

在真实采购订单链路上验证：从先前执行经验提炼的文字策略，是否提高未见条件组合的任务成功率和重复执行一致性。

首期演化产物为 Skill 的文字策略与经验。学习器不得修改脚本、主提示词、子代理编排、Harness 代码、业务权限或审批政策。编码 Agent 可以为实现本规格新增基础设施代码；这一权限不授予运行中的 Curator。

保留 Java ERP、Vue、MongoDB、DeepAgents/LangGraph、MCP、OpenSandbox。报价、税费、单位换算及新增 ERP 能力另行立项，首期不据此声称已支持。首期离线学习后冻结评测；在线学习是后续独立协议。

实验环境满足预先锁定的验证门槛后自动晋升。日常 Demo 生效由用户确认，保持独立活动指针和所有权。

## 2. 论文成果如何使用

三篇固定版本的原始 PDF、HTML 与可检索 TXT 已保存在 [本地论文目录](../papers/rsi/README.md)。编码 Agent 应读取相关原文章节，不以本规格的机制摘要代替论文；公式和表格以 PDF 为准。

| 工作 | 首期采纳 | 项目声明边界 |
| --- | --- | --- |
| [TRACE v2](https://arxiv.org/html/2608.22793v2) | 按操作/技能比较成功与失败轨迹，提炼条件、步骤、例外与检查点，修订文字策略 | TRACE-inspired；未实现原论文逐轮状态条件选择/注入时不称完整复现 |
| [Prime Agent v1](https://arxiv.org/html/2608.23552v1) | 持久策略的版本、来源、任务边界更新与回滚 | Prime-inspired 状态治理；不声称首期具有完整 RLM/REPL 与递归架构 |
| [GDPevo v1](https://arxiv.org/html/2608.03764v1) | train 覆盖规则子集，test 重组条件，独立判分与冻结对照 | 公开业务规则下的采购适配；不等同于隐藏企业惯例学习或论文原始成绩 |

## 3. 工作区与接入位置

- 被测应用：`/mnt/c/dev/rush-harness`。
- 独立评测：`/mnt/c/dev/rsi-eval/procurement_eval`，复用已有真实 worker、预算代理、业务观测和证据归档。
- `src/agent/main_agent.py`、`subagents/loader.py`：装配实验策略检索、不可变快照和主子调用记录。所有实验组使用相同装配代码，以配置区分组别。
- `src/api_view/api/chat.py`：在任务开始和审批恢复时绑定同一 snapshot；不得恢复到当前最新 assignment。
- `src/agent/skills/pipeline.py`、`store.py`：复用收集、结构验证、版本存储与 CAS 能力；独立候选保存和评测晋升不得调用会立即修改日常 assignment 的发布路径。
- `src/agent/middlewares/`：采集压缩/offload 前的结果与技能使用证据，保持既有业务策略及框架协议。
- 现有 RunEvidence、历史更新、摘要归档和审批 executed 不能独立充当任务成功标签。

推荐新增 `src/agent/evolution/` 承担快照、候选、检索和 Curator 服务；评测控制与裁判留在独立工作区。具体文件划分由执行 Agent 确定并登记，不能复制一份替代 ERP 或 Agent。

## 4. 数据契约

所有记录携带 `schema_version`、`owner_id`、`experiment_id`、唯一 ID、时间、内容 SHA256。外部引用必须可校验存在与 hash；敏感记录不自动发布。

| 对象 | 必需内容 |
| --- | --- |
| Episode | task/dataset/split、attempt、全部 thread/run/resume 关联、父子调用、公开输入、实际可见消息、原始工具结果与产物引用、审批、前后业务状态、snapshot、评分、资源消耗 |
| Feedback | success、强制约束结果、error_categories、公开 rule_ids、证据引用；不得含预期订单或完整解法 |
| Candidate | parent_snapshot、来源 train Episode、修改正文与 diff、适用条件、处理步骤、例外、支持/反例证据、变更原因、内容 hash |
| Snapshot | 源码与配置 manifest、ERP JAR、依赖/镜像身份、全部基础技能与演化技能正文、经验语料、检索配置、模型配置指纹；不包含密钥 |
| Evaluation | 被评 snapshot、taskset/rubric/config hash、全部 attempt、分数/约束/失败、重复执行指标、资源与成本、证据 manifest |
| Promotion | parent/candidate/target、validation 与 regression evaluation IDs、policy hash、decision/reasons、CAS 结果、rollback 关联 |

一次业务目标跨多个 API run 仍是一个 Episode，不能把审批恢复当独立样本。失败记录可作为 train 学习素材，但必须完成归档、注明失败类型；环境失败不伪装成采购策略反例。

接口语义：

```text
collect_episode(task_context, run_refs) -> Episode
score(episode, private_rubric) -> private Evaluation + sanitized Feedback
learn(train_episodes, allowed_feedback, parent_snapshot) -> Candidate
freeze(candidate_or_base, manifest) -> immutable Snapshot
execute(public_task, snapshot) -> Episode
evaluate(snapshot, declared_taskset, policy) -> Evaluation
promote(candidate_snapshot, evaluations, expected_parent, target) -> Promotion
rollback(target, expected_current, prior_snapshot) -> Promotion
```

未经验证的候选可以冻结供评估；freeze 不等于晋升。接口缺实现时显式 blocked，不回落到固定 Agent 后冒称学习成功。

## 5. 学习与执行

1. 固定基础 Actor，在 train 上采集真实 Episode。Curator 与 Actor 使用同一模型配置，独立角色和输入，所有请求经过预算网关。
2. Curator 按操作/技能组织完成的记录，比较成功与失败，输出可迁移策略。缺少成对证据时标记不足，不虚构轨迹；失败无法归因时不强行修订。
3. 服务校验候选仅含允许的文字文件与结构，去除实例答案、具体订单 ID、敏感数据和越权指令。保留“来源证据”和“未来可用知识”的区别。
4. 快照按版本独立物化，模型无权编辑权威内容；不依赖可原地修改的共享技能目录。正文 hash 在物化及执行时核验。
5. 任务内对话态允许改变，跨任务策略保持冻结。审批恢复、沙箱恢复和并发任务均引用绑定 snapshot；撤回或晋升不改在途任务。
6. 采用有固定配置和上下文上限的策略检索，将选中内容明确注入；基础技能通过现有框架机制发现。记录选中、读取、执行与业务效果，避免只凭目录存在判断使用。

候选轮数、最大条目数、检索 token 上限、每任务模型请求/时间上限全部配置化。首个 pilot 前锁定并记录；不得失败后静默增大以取得通过。对照阶段使用相同检索实现和知识输入上限，正文内容不同。

## 6. 任务集与实验组

首期三个任务族：完整订单与业务校验、信息缺失/歧义澄清、审批/幂等/异常恢复。允许向测试业务边界注入明示且可复现的临时故障，不能替代真实 ERP 行为；记录注入配置和正常服务类型。

推荐首版每族 5 train、3 validation、5 sealed_test，总计 39 任务，加现有 3 个公开 pilot 和固定旧能力回归。规模是工程起点，不是用户已授权的付费任务数或论文复现规模。

先写规则/条件覆盖矩阵，再写实例。test 含 train 未出现的条件组合，不能仅替换 ID/数量。强制审批与权限规则保持公开。用户补充策略必须按实际被问字段应答；错误问题或无必要澄清要有明确评分。

| 新协议组 ID | 跨任务状态 | 作用 |
| --- | --- | --- |
| fixed-v1 | 固定基础技能，无经验 | 基线 |
| retrieval-v1 | 同一 train 的原始执行经验与允许反馈 | 直接复用经历 |
| curated-v1 | 同一 train 经验提炼的文字技能 | 策略提炼贡献 |

禁止无声重定义旧 A/B/C/D。更新独立适配器契约、CLI schema、报告和迁移说明，保存历史定义。

各组使用相同 Actor 模型、工具权限、任务、初态与执行额度。B/C 获得相同训练记录和监督信息；C 的学习计算单独计费。后续可加入仅成功轨迹消融，不在首期混入未验收组。

固定最终 snapshot 后，sealed_test 每任务每次重复从新业务初态执行。建议重复 3 次，分别报告平均分、单次成功率、至少一次成功率、全部成功率与任务级分布。预算不足时明确缩小 pilot 试运行，不能把 pilot 包装为完整封存实验。

## 7. 独立评分与晋升

成功必须来自真实业务状态与审批证据。正确拒绝且无写入可以是该任务成功；存在订单不自动代表成功。必查终止、内容/金额、无额外/重复/跨用户写入、审批绑定、必要澄清、任务所需产物。

裁判有私有预期状态；反馈采用固定字段白名单。训练可使用已结束任务的允许反馈；validation 的私有详细结果只由评测控制面处理，首期不回传 Curator 继续修订；sealed 的答案与反馈不用于学习或选优。

在候选评估前锁定 `promotion_policy.json`：业务强制检查、验证成功率改善条件、重复一致性底线、固定回归判定、平均执行成本与耗时上限、候选轮数。成本/耗时上限取得固定基线后确定；字段未定时 promote 返回 blocked。

晋升条件：强制合规全通过；同任务/同重复协议下验证成功率较父版本提高；固定回归集合的必需项不退化；满足一致性底线与成本/耗时上限。政策外部服务超时等失败的分母处理必须事先声明，另报全部 attempts 的端到端可靠性。

没有候选满足条件时保留父版本并报告无晋升/无已证明增益。小样本规则通过不等于统计显著性或全面不退化。封存测试失败必须报告，不据此继续调候选后仍使用同一封存集声称独立测试。

## 8. 隔离、预算与部署

Actor 与 Curator 只获得各自允许的输入，不可读取评测答案、未来任务、其他组结果、裁判库或控制数据库。检查宿主挂载、Docker socket、服务端凭据、网络访问和身份权限；缺一项时公开 pilot 可继续，sealed 必须拒绝。

每 attempt 独立 ERP H2、评测 Mongo 数据库/namespace 与实例哨兵，不清空开发数据。主子 Actor 与 Curator 的所有模型请求、重试、未知 usage 预留计入统一累计预算。分别报告学习、候选评估、最终执行及总估算成本；供应商账单缺失时实际费用为 null。

现有历史额度不自动成为新授权。先执行零模型生成的 doctor、validate、prepare 与组件测试；首次新增付费调用前确认具体总额度。权限外外部工具默认按订单实验既有契约拒绝，各组保持一致。

复用现有部署与正式 chat/resume 路径。日志脱敏，证据排他创建，重试新 attempt。report/verify 必须校验完整 manifest，不覆盖旧报告。准备并实测启动、健康检查、恢复、停止命令，仅操作本评测资源，不删除数据卷。

## 9. 实施任务与验收

以下是待登记的工作包，不是现有任务状态。执行 Agent 先核对现场，补齐独立评测任务计划；需改 Harness 时按 AGENTS.md 登记任务及依赖，运行 check → next → packet，一次一个 eligible task。

| 包 | 交付 | 必须验证的行为 |
| --- | --- | --- |
| SE00 契约 | 版本化 schema、组定义、覆盖矩阵与迁移说明 | 旧报告不误分类；不支持能力显式拒绝 |
| SE01 证据 | 完整 Episode 与白名单 Feedback | 主子关联、审批恢复聚合、offload 原始状态、无答案泄露 |
| SE02 快照 | 不可变物化与 run 绑定 | 在途晋升/撤回、暂停恢复、并发隔离、hash 损坏拒绝、无模型写权 |
| SE03 学习 | 真实 Curator 与候选存储 | 从真实 train 产出修订；越界文件拒绝；保留无效候选及失败；费用计入 |
| SE04 晋升 | 独立评估、CAS、回滚 | 未过验证不生效；并发父版本冲突；回滚；Demo 指针不变 |
| SE05 任务与隔离 | 组合任务、裁判与权限边界 | 错单、额外写入、错审批、错澄清被发现；封存资源不可访问 |
| SE06 实验 | 三组冻结运行与报告 | 相同初态与协议、全部 attempts 留存、成本分列、一致性指标正确 |

每包保留真实检查命令、日志、receipt、测试数量与文件 hash。必需检查零收集、跳过、缺凭据或服务不可用均不通过。确定性替身限于组件测试；真实部署和学习验收须调用真实模型与业务栈，不能用手写“演化经验”冒充模型结果。

工程验收与研究结论分开：管线真实运行、验证与冻结有效可以证明工程实现；只有实际独立测试结果支持时，才声称成功率或一致性提升。本文及后续计划 checker 不证明任何实验结果。

## 10. 编码 Agent 开工指令

将本文与研究记录作为开工资料。先阅读两个仓库交接和适用 AGENTS.md，核实当前 git 修改、服务、任务与证据。优先补齐已有系统，禁止重写已验证组件。

先完成 SE00 并建立后续任务依赖、验收与状态，不同时实现全部包。保留用户文件；未获得明确新预算前不新增付费模型调用。文档和状态记录完成的是实际行为及证据，不能把未来命令写成通过。

遇到契约冲突先记录影响并解决；技术替换、业务范围变化、日常 Demo 晋升需要用户确认。其余局部实现决定由编码 Agent 执行并说明，不再要求用户逐项决定内部函数和字段命名。
