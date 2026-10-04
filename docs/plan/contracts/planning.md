# 多约束采购规划契约 v1

## T48 可追溯的非下单决策

`planning_decision` 接受模型声明 `needs_information` 或 `infeasible`、当前 revision、理由、关联物料与公开 source_ref。声明保存在可信 owner/thread 的当前目标，CAS 拒绝过期 revision、执行中操作或已有候选；不提供求解器，不替模型判定声明正确，不授予订单权限。修订清除旧声明，后续输入可以重新规划。控制面独立 judge 判断来源未决/真实无解；正确澄清或拒绝必须无实际订单，不能仅从 API completed 推断。

评测可在真实 ERP HTTP 读取后明示遮蔽指定价格/交期字段来构造缺失来源，必须归档原响应与遮蔽后的部署视图。实际 ERP/MCP 写入保持原样；字段遮蔽不意味着替换 ERP 状态。

用户 2026-10-03 已要求按场景与裁判 → 持久计算 → 技能学习 → 持续重规划实施。旧订单、课程分析和独立评测 protocol-v2 保留；本契约新增规划模式，不把旧最低价脚本当完整求解流程。

## 公共输入与目标

`PlanningProblem`：schema_version=1、goal_id、revision（从1递增）、budget（CNY两位小数字符串，可为0）、currency=CNY、demands、offers。所有 ID 非空；无重复物料需求、无重复 offer_id；金额不接受 float、科学计数或负数。使用严格整数数量/交期，不接受 bool。

`Demand`：part_id、quantity>0、max_lead_days>=0、required、priority>=0（越小越优先）、allow_partial、allow_supplier_split。required 必须全量，不得 allow_partial；optional 不允许部分时只能 0 或全量。每件物料最多目标量，拒绝超买。allow_supplier_split 控制同一物料能否从多个供应商购买，与 optional 部分满足不同。不同物料可以分别向多家供应商购买，按供应商形成多个订单草案。

目标顺序：所有 required 数量与期限为硬约束 → 按 optional 的 priority 升序逐层最大化件数（同层相加）→ 最小化总价。数量无权重，不引入税/运费/收货。相同目标值的不同方案同样被接受，供显示选择的确定性排序不作为评分规则。

`Offer`：offer_id、supplier_id、part_id、supplier_active、part_active、relationship_active、unit_price（正的两位小数字符串或null）、lead_days（非负或null）、source_ref、source_status=verified/missing/conflict。source_ref 是非空的证据定位符（例如实际 ERP HTTP 请求引用或页面原文归档引用），不是来源真实性证明；live 控制面必须保存实际原文。verified 需价格/交期齐全。missing 表示无法确定，conflict 表示未解决的来源矛盾，不能把二者认定为无供货关系或预算不足。已知停用、已知无关系和已知超期的报价即便缺价，也不影响目标，不制造无条件澄清。缺失可替代供应商或低价来源会使最优性未知，即便已有安全方案。

## 公共方案与检查

`Plan`：goal_id、revision、lines=[{offer_id,quantity>0}]。同 offer_id 不得重复；单价/供应商只从对应 Offer 读取，不能由候选偷偷覆盖。公共 `check_plan` 检查所有硬约束，返回 violations、total_cost、各物料 shortage、optional_coverage。合法方案可以非最优。`order_drafts` 只把合法方案按供应商转成 ERP 请求形状；不写订单、不赋予批准权。

revision 或 goal_id 不匹配直接为 stale/foreign，禁止旧批准对应新计划。T37 不接现有批准数据库；后续需把 goal/revision、实际 payload hash、逐单 approval/operation/order 绑定，并从 ERP 独立核对所有实际写入。

## 私有裁判

`scripts/planning/judge.py` 在评测控制面运行，不是 Agent 工具，不同步至 /skills 或 /workspace。独立枚举全部有限分配，独立计算可行性与目标，不调用公共 checker 决定 oracle。结果分 `feasible`、`infeasible`、`unresolved`；unresolved 是可能影响结论的证据不足或矛盾。`grade` 同时保留候选可行性与最优性；只有来源完整、合法且达到最佳目标才 accepted。合法非最优不算非法；同价不同供应商或不同分配可以都 accepted。超出显式枚举状态上限抛错，不声明不可行或最优。不向 Actor 返回私有最优解/枚举路径。

## 修订

`revise_problem` 接受新预算、完整替换某些 Demand、按 offer_id 更新/新增/移除 Offer，返回新问题与 affected_parts。更新必须仍满足契约；goal_id 不变、revision+1。预算/可选优先级变化影响全局分配，所有物料都受影响；局部价格/交期/来源/需求变化先标对应物料，global_allocation_changed=true，因预算耦合最终分配仍需重新检查。没有变化不制造新 revision。此函数计算依赖元数据，不保存运行状态；持久计算由下一包负责。

## 接口影响与后续门槛

新增 Python 数据接口；不改变现有 Java/MCP/SSE JSON。当前登记：T38 持久 kernel、T39 多单审批组件、T40 规划Actor接线；完整真实模型场景、TRACE与持续目标将在开工前分别登记，旧T39–T41提及仅是历史阶段名称。课程 Analyst YAML 与 build_report 保留，规划 Actor 必须使用单独入口。已知完整数据且预算充足不应无条件询问；已证不可行可正确拒绝，来源不足先核对/澄清而不是宣称无解。策略学习评价必须有这些相反条件。

## 2026-10-03 用户授权的执行顺序调整

用户明确要求跳过 T38 推进其他工作；T38 暂停保留全部验收。T39 独立完成逐单审批和执行组件，前置 T37/T12/T35。完整 Actor 与 kernel 场景仍以 T38 + T39 为前置，TRACE 和持续目标不削减；原 T39–T41 是未登记的阶段名称，不是已实现任务。

## T39 逐单审批组件

`PlanningOrders` 的 owner/thread 由可信应用传入，不是模型参数。`create` 保存公共问题；`prepare(Plan)` 只做合法性检查和供应商分组，不解最优方案；同候选重放返回同 interrupt/operation/hash。`planning_goals` 保存当前 revision/problem/proposal、逐单结果及执行租约，唯一键 owner/thread/goal。每个 pending_action 新增可选 `planning_binding={goal_id,revision,proposal_id}`，仅控制面写入；旧课程记录兼容。

每单单独 approve/reject。目标修订或同 revision 更换候选使旧动作不可执行，授权服务与网关执行前均验证；不是仅依赖提示词或请求 note。规划签名grant携带 `approval_ref={thread_id,interrupt_id}`；网关必须通过真实内部 HTTP 端点核验当前动作和租约，缺配置、错误响应和网络故障拒绝写入。批准令牌、operation_id不提供给 Actor。

execute 用目标文档 CAS 获取唯一写租约，修订和候选更换不得穿过执行窗口；无事务依赖。网络/超时/取消或retryable业务错误保留 uncertain 租约，仅允许同单、同operation对账重试；没有自动过期释放。进程在 inflight 中崩溃时保持锁，需后续专门对账恢复，不能假定ERP未写。确定成功保存实际 ERP order_id/envelope；重放已保存结果不再写入。确定拒绝不记作订单。已写订单跨修订保留，已有订单时禁止生成新的全量批次并报 RECONCILIATION_REQUIRED；持续目标包需补实际订单差量对账，不重复采购。本包是后台组件，尚未提供完整Actor入口或Vue批次交互。

检查影响：T12、T07、MCP订单工具、FastAPI内侧审批接线；应用集合14→15。完整场景仍需持久计算和真实模型轨迹，TRACE不以手工候选测试充当经验。

## T40 规划图接线

独立planning Actor使用公开问题/可行性检查、kernel通用工具及提交工具；没有oracle、完整固定planner或order_create直写工具。模型配置来自ModelConfig。规划goal从受信thread配置取得，公开工具schema不允许改owner、thread或批准身份。

planning_submit逐单发出既有hitl_approval形状，带控制面planning_action_id。适配器对规划中断使用既有pending_action ID作为公开interrupt_id，保持原operation/hash/binding；同工具内多个interrupt的框架任务ID会复用，不能拿它区分订单；正式resume批准原动作，工具只读取服务端决定并经T39执行。节点重放按相同顺序消费每单interrupt，不以字符串approve或直接Command(resume)授权。组件测试明确使用scripted模型；真实Prime使用、规划质量和TRACE实验仍需独立live证据。

规划入口：POST /api/planning/{thread_id}/goal 接受budget和Demand列表，身份从demo session取，线程若不存在则创建，其他owner的线程返回404，有旧课程消息的线程拒绝切换。服务端通过真实ERP读物料详情并在目标文档sources归档原JSON（不含凭据/请求头）；GET同路径返回目标和已写订单。对应thread的chat/resume/state由planning_graph_provider选择独立图；未配置规划图时POST返回503。客户端沿用旧审批事件字段，不需识别原始框架任务ID。

## T41 规划轨迹与冻结文字 bank

仅规划装配安装 PlanningTraceMiddleware；课程模式不切换。默认规划runtime使用配置模型、空bank基线与真实Mongo EpisodeStore。没有人工安装的“学习策略”，Curator/训练学习空间/真实模型验收仍未通过。组件API装配可显式提供scripted模型和独立选择器；证据携带scripted-component provenance。

同owner/thread/goal首次运行绑定一个不可变文字bank，后续chat/resume复用，不读取新的活动指针。bank仅冻结文字知识，不是SE02规定的完整环境Snapshot。model provenance改变、跨owner/thread/run或hash损坏拒绝执行。bank仅control-plane可freeze/assign，没有Actor写工具；set_current不是评测晋升，也不自动调用日常assignment。

每个Actor模型动作前，根据当前Actor可见系统/历史及全部planning描述，请配置模型输出有序JSON ID数组；无技能时明确记录空选择、不制造额外模型请求。有技能时选择器和Actor请求均通过正式API同一累计预算callback；顺序注入精确正文，本轮注入不写回checkpoint或下一轮。未知/重复/非法ID拒绝，不回落静态技能。选择、读取正文hash、实际请求、动作/tool_call_id及完整原始工具结果分别留证，adoption=unassessed；不会因选择就宣称采用有效。官方callback记录模型/选择器的调用和父ID、usage；工具原文在SDK外层offload前记录。

一个goal跨多个API run保持一个open、未评分Episode；记录公开输入、resume interrupt_id、审批载荷hash/决定状态、实际订单、资源与原始typed模型/工具事件。completed/executed不产生采购success标签；资源耗尽、环境和未分类错误分开，不自动成为采购策略反例。独立裁判Feedback/完成训练记录接入另包，Actor看不到私有评分或答案。

事件脱敏后按128k字符分块，保留顺序、commit/hash；导出必须核对全部seq/chunk/hash，缺块或记录失败使API显式failed。该组件未提供完整环境Snapshot、Curator、多轮初始化/技能对照更新、作用域内动态子会话或学习增益。影响T40/T07、正式规划chat/resume及默认Demo装配；应用集合18，课程审批语义不变。

## T42 Vue规划入口

工作台“新建采购规划”提供预算/需求量/交期/必需/可选优先级/部分采购/供应商拆分。保存时生成独立thread，调用既有POST goal，只保存真实ERP公开目标，不自动调用chat/model。用户发送消息才进入正式规划图；原逐单审批保持。历史选择或每次chat/resume结束后GET goal读回真实订单；创建/批准不乐观增加订单。未知执行结果与查询失败可见，不展示为采购成功。线程/账户改变丢弃旧异步响应，切账户销毁表单；run期间不切换身份或目标。所有货币作为两位小数字符串提交，数量/交期/优先级整数；服务器仍是权威校验。未提供编辑既有目标revision、完整持续对账或live模型验收。

## T43 累计约束与追加计划（planning schema v2）

PlanningProblem新增commitments，空时保留schema_version=1，非空要求2。每条CommittedLine含order_id/offer_id/supplier_id/part_id/quantity/unit_price/lead_days/source_ref，控制面由实际成功写入及其原批准载荷和原报价快照生成，Actor不能提交这些字段。Plan.lines表示本revision的追加量；空commitments时与原全量语义一致。累计需求/可选覆盖/拆分/部分规则、金额和计划交期把历史承诺与本轮追加一起计算。历史成交价/写入时计划交期不随现价或关系停用而改变；已订超量、旧交期不满足新期限、已花支出超过新总预算均是冲突，不自动取消/退货。目录交期仍是计划依据，不声称实际到货。

PATCH /api/planning/{thread}/goal接受expected_revision、可选budget、局部Demand替换、refresh_part_ids；不接受Offer/commitment/owner/token。保持同一goal/thread，fresh sources只查指定已存在物料，删除已不存在供货关系的当前Offer，其他来源保留；原problem/source/proposal_id归档revision_history，revision_change包含affected_parts与global_allocation_changed。budget/优先级变化影响全局，其余变化标局部物料但最终预算分配仍须整体复验。对账新写订单即使预算未变也推进revision；真正无变化不制造revision。

修订时逐个通过真实ERP /orders?order_id核对已写单归属、ID、原批准明细/币种/成交总额/note/version；外部修改/缺单/未保留原交期证据拒绝修订，不从新报价猜历史。原课程订单接口不变，无取消/收货能力替换。执行中的inflight/uncertain锁阻止修订。目标CAS包含读回前的revision/proposal/orders/execution，期间另一次写入或候选变化使修订冲突。旧T39 revise接口保留为内部不对账修订；有历史订单却无完整commitments时prepare继续RECONCILIATION_REQUIRED，不误称已经对账。

新proposal只有追加量供应商草案；旧批准与旧grant因revision/proposal失效。当前revision又写入订单后，换候选必须再次真实对账修订，不能重复全量购买。原批次同interrupt序列重放语义不变。正式chat遇到过期planning中断时，可信服务端将新的用户消息作为官方Command update并用planning_goal_revised恢复旧工具；旧工具返回STALE_PLAN，然后Actor重新读取新问题，不批准旧单。bank/Episode跨修订仍沿用；订单不变成采购success标签。

私有穷举judge独立累计历史数量/成本/计划期限，枚举剩余量，接受不同合法并列追加方案；已全量承诺物料的无关新缺价不自动判unresolved。仍不向Actor提供私有最优解。影响T37/T39/T40/T41及规划API，新增字段向后兼容空commitments。Vue既有目标展示可读累计目标和实际订单，编辑入口另包；完整持续Actor持久状态选择性重算/真实学习证据仍须T38及预算授权，不以本包组件验收代替。

## 2026-10-03 用户批准的新 T38（覆盖旧 kernel 约定）
持久计算指Mongo权威版本化JSON数据，每步OpenSandbox内独立Python进程。通用load_state/save_state；Agent自行计算，无固定planner。按owner/thread/session、operation ID/base version与容器generation隔离；临时结果正常退出、JSON校验、进程树清理确认后CAS发布。失败/取消/超时不更新旧状态；无法确认停止则隔离环境。仅复制read_names所需数据，未变化数据复用；函数/模块/DataFrame下次显式重建，不保持对象身份。API/容器重建加载数据，不重放代码；无任意文件/网络副作用回滚保证，订单仍MCP审批。T40 Actor工具/提示与T41轨迹、T43版本变化需按新语义回归；旧交付组件证据不证明新计算机制。旧Jupyter补丁与失败证据保留但不再继续维护。
