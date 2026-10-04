# 采购规划实施现场与后续边界

用户 2026-10-03 已要求实施[场景升级提案](procurement-planning-scenario-proposal-2026-10-03.md)，推进顺序调整为场景与裁判、持久计算、技能学习、持续重规划。本文件保留完整目标，不把组件通过写成项目完成。

| 包 | 内容 | 必须证明的行为 |
| --- | --- | --- |
| T37 | 决策环境和独立裁判 | 多合法解、目标顺序、缺信息与无解区分、小实例穷举、真实 ERP 数据 |
| T38（新设计组件done） | OpenSandbox持久JSON+每步独立Python与通用工具 | 状态跨调用、只读必要中间值、预算变化选择性重算、owner/session 隔离、取消和重启恢复；固定脚本不能包办 Agent 决策；真实 Actor 入口随下一包接入 |
| T39（多单组件，Actor入口尚待后续包） | 完整目标到审批与真实多单 | 查询/计算/方案/缺口/来源，逐单批准，ERP 内容与无额外写入，约束变化使旧批准失效 |
| 后续学习包 | 先验证学习空间，再造 Curator；TRACE Skill Bank | 操作抽象/聚焦拆分、按技能成功失败比较、逐动作回合重编排；冻结基线/原始经验/提炼策略与总结/静态注入消融 |
| 后续持续目标包 | 变化、动态异步会话、受限 refinement | 稳定消息/状态、来源变化定位受影响计算、重审批；在线 refinement 与可执行技能演化分别评估 |

后续包将在上一包实测后登记具体检查，未存在的命令不是通过证据。全部 attempts 保留，旧课程与评测 pilot 不改。训练覆盖价格/交期、预算/分批、来源缺失的单独条件，封存测试重组并加入预算足、明确数量、宽松期限的反事实，检验问询/重试/拒绝是否变成无条件动作。sealed 数据与私有裁判不可暴露给 Actor/Curator。

现有独立评测位于 `/mnt/c/dev/rsi-eval/procurement_eval`，只读核查 SE00 done、SE01–SE06 pending。本轮没有改该工作区；旧任务顺序不能直接驱动新场景学习。新增模型调用仍须按已接受自进化规格先锁具体费用额度；零模型组件和本地服务验证可继续。

用户要求先跳过 T38：现登记 T39 为独立逐单审批/执行组件，完整 Actor 与 kernel 联调仍依赖两包通过。不会以 T39 组件测试代替真实模型规划或学习效果。

T39 多单组件已完成：artifacts/tasks/T39/20261003T095048Z/receipt.json，18+52=70项通过；无模型/无kernel运行。代码orders.py，版本绑定逐单审批、真实双供应商订单、旧grant实时拒绝和不确定响应同operation恢复。完整Actor/Vue批次入口、TRACE、inflight崩溃对账与持续差量采购仍待实现。

T40正在接入独立DeepAgents规划图、公开problem/check/kernel/submit工具及正式chat/resume逐单中断。组件使用明确scripted模型、真实Mongo/MCP/Java核对，未启动kernel或真实模型；完整Prime/学习空间证据仍待T38与具体付费预算。

T40接线组件完成：artifacts/tasks/T40/20261003T100854Z/receipt.json，14+50=64通过。明确scripted模型，仅正式API/真实Mongo/MCP/Java中断恢复证据；独立模型配置与kernel装配存在，但本轮未执行真实模型/kernel，不称Prime或学习空间通过。

## 2026-10-03 用户批准的新 T38（覆盖旧 kernel 约定）
持久计算指Mongo权威版本化JSON数据，每步OpenSandbox内独立Python进程。通用load_state/save_state；Agent自行计算，无固定planner。按owner/thread/session、operation ID/base version与容器generation隔离；临时结果正常退出、JSON校验、进程树清理确认后CAS发布。失败/取消/超时不更新旧状态；无法确认停止则隔离环境。仅复制read_names所需数据，未变化数据复用；函数/模块/DataFrame下次显式重建，不保持对象身份。API/容器重建加载数据，不重放代码；无任意文件/网络副作用回滚保证，订单仍MCP审批。T40 Actor工具/提示与T41轨迹、T43版本变化需按新语义回归；旧交付组件证据不证明新计算机制。旧Jupyter补丁与失败证据保留但不再继续维护。

新T38完整gate253通过，真实数据/进程机制证据见artifacts/tasks/T38/20261003T111735Z/receipt.json与persistent-computation.md。仍无付费Actor轨迹或学习效果证据。
