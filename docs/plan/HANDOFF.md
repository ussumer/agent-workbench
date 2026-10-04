# 当前交接

## 2026-10-04 简历版推进决定

用户明确要求加速产出，T46 按已有真实证据收尾，不再循环补做冻结后独立2500双单冷跑或整体 deadline/取消专项；两项延期限制已写入 T46 任务说明和 state。T46 gate `artifacts/tasks/T46/20261004T025447Z/receipt.json` 为 106 passed/0 failed/0 skipped。下一任务登记 T47，优先运行真实 TRACE 小样本与 fixed/raw/curated 三组消融，记录真实调用、费用、源码 commit、bank hash 和独立 judge；不伪造提升。

## 2026-10-04 T47 已登记

T47 已完成，入口为 `scripts/planning/evolution_pilot.py`，验收为 `tests/acceptance/test_t47.py` 加一轮受 50 CNY/2 CNY 网关限制的真实小样本。所有 attempt 及失败保留。

## 2026-10-04 T47 完成

gate `artifacts/tasks/T47/20261004T043016Z/receipt.json`：11 个组件测试和只读 live 核验通过。真实实验目录为 `/mnt/c/dev/rsi-eval/procurement_eval/runs/planning-session-20261004/attempt-evolution-pilot-040413`，含训练记录、Curator 原始输出/技能 hash、三组 bank、6 条 Actor attempt、ERP/计算/独立 judge 和 manifest；`retry-fixed-no-partial` 是保留原端口失败后的独立环境补跑。结果 fixed 2/2、raw 1/2、curated 2/2；raw 失败为模型调用预算耗尽，fixed 原失败为端口占用，均留在分母。Curator 未读测试答案，训练前基线仍为 `3c36215`；该 pilot 只支持真实管线和描述性对照，不支持学习提升结论。下一步可做简历演示/动态重规划，不再重复本轮模型运行。

## 2026-10-04 T46 新检查点：严格只读核验与零生成运行前冻结

T46仍in_progress，不重跑attempt-7。新gate `artifacts/tasks/T46/20261004T025447Z/receipt.json`：33 unit + 68既有回归 + 4只读live断言 + 1真实prepare断言，106passed/0failed/0skipped。新入口 `scripts/planning/evidence.py` 校验attempt manifest和归档源码，重新独立评分；ERP完整订单集合恰好两单2196.00、每行金额与批准payload/hash一致；137条Episode事件hash/seq/owner/run/bank、两个revision、每次computation operation/result绑定、v1→v2→v3权威JSON、失败不发布和报价/required中间结果保持均核对通过。旧失败全部保留。

新增 `live_baseline.freeze_runtime` 在生成前冻结实际JAR、138 Python依赖、78 Protocol依赖、执行/execd镜像ID、provider GET/models身份和外部评测器源码归档；实际启动JAR hash不符即拒绝。prepare网关硬限制0调用。真实 `attempt-prepare-023631` 和带冻结 `attempt-prepare-frozen-025040` 均prepared/0calls/0保守费用/空ERP，无新增付费Actor尝试。账本仍5.118624CNY保守累计，actual_cost=null。检查点说明 `docs/runtime/planning-baseline-evidence.md`；测试新增hash篡改/foreign owner/run/bank/缺revision/额外ERP单等拒绝反例。

剩余两类必要证据：历史付费attempt缺事前冻结，不能由后来的prepare追认；需要冻结后的独立2500预算两单执行（当前attempt-7只批准第一单后改预算）。入口还缺attempt整体deadline/取消和实际子进程停止验证，须先零模型实现/验证，再执行缺失场景；不能循环重跑已验证预算修订。开发修改测试/评测器先读旧证据；常驻服务热重载可减少启动，但本轮没有实现常驻评测命令，不把建议写成已交付。完整目标active；真实TRACE训练/Curator/固定raw curated消融和组合反事实仍未运行。

下一条命令：`python3 scripts/plan_guard.py check` → `next` → `packet T46`，随后只实现deadline/取消入口及零模型真实停止验证，再决定缺失双单场景的一次受额度保护执行。不要再跑prepare或attempt-7模型。当前本轮自建服务已随prepare退出；用户Mongo27017与外部评测Mongo27028保留。

## 2026-10-04 T46 检查点：消息配对修复、真实预算修订与持久复用

训练前 Git 基线为 `3c36215`，未训练、未运行 Curator。正式 stale-goal 恢复原 `Command(update=用户消息)` 会把 HumanMessage 插入 AI tool call 和 ToolMessage 之间；现先用官方 `interrupt_before=["model"]` 完成过期工具，再输入新消息。T43 消息配对断言及 T40/T41/T43 真实 Mongo/Java/MCP 回归 54 passed，模型明确 scripted-component。改动整理中曾误删正常 astream，attempt 4 零调用失败，已修复并保留；不可将其记成功。

真实 attempts 全部保留于外部 `/mnt/c/dev/rsi-eval/procurement_eval/runs/planning-session-20261004/attempt-2` 至 `attempt-7`。attempt 3 初始最优但修订后 provider400；attempt 5 实际两单完成但收尾 Episode 查询排除 _id 导致导出失败；attempt 6 completed/11 calls，但修订计算未用 load_state，补强断言后失败；attempt 7 completed/12 calls、初始最优2493.50、预算2200后实际两单合计2196.00，revision2计算明确base_version2/read_names=[analysis]/load_state，原单未重复。3 live 断言通过。这些全部是基线，不能当学习收益。

费用授权仍新增累计50CNY/单attempt2CNY。全部新增调用保守计入5.118624CNY（剩44.881376），包括未知usage预留；actual_cost仍null，用户指出其观察账单0.02元，不把保守估算称实际费用。权威会话账本 `/mnt/c/dev/rsi-eval/procurement_eval/runs/planning-session-20261004/budget-ledger.json`；已将旧尝试按evidence/hash导入。driver新增独立lock文件+fsync/rename写入，损坏拒绝而非清空、固定会话路径、限制50/2、usage未知保留、并发预留/结算检查。23零模型unit通过。

T46保持in_progress：最新gate `artifacts/tasks/T46/20261004T021644Z/receipt.json` 23+68+3通过，仅说明已实现断言通过，尚不足以完成全部task说明。Git检查点88f205e曾错误登记done，本次纠正状态并保留提交历史。仍需补齐：执行JAR与实际依赖的事前冻结/完整模型身份preflight证据；完整baseline双单独立场景与ERP内容/无额外订单严格比对；revision复用前后JSON/hash/同Episode与bank绑定强断言；prepare zero-generation验收；attempt整体deadline、取消并确认实际计算停止；外部评测器源码版本/hash绑定。不能以当前较弱live断言或结构checker把上述要求降掉。累计账本改动后尚无新增付费attempt，不要为补已有证据盲目再跑模型。下一次先做零调用补齐与现有attempt只读核验，再决定是否需要唯一新attempt，仍在既有50/2授权内。

## 2026-10-04 训练前 Git 基线已提交

用户明确要求先 commit 训练前代码以便消融对比。T46 in_progress，尚未gate通过；没有训练或Curator。新增费用授权50CNY，单attempt2CNY；首轮缺JAVA_HOME零模型失败保留。第二轮真实Actor7次请求、2次OpenSandbox计算持久化，候选2493.50可行，但评测入口未读SSE event名导致漏审批、后续provider400；零实际订单，不能把result completed当通过。全部证据复制到artifacts/planning/pre-training/并记录hash。已知费用0.468333CNY，未知usage仍预留，累计保守记账0.901188CNY；实际账单null。评测入口正在修复，commit保持T46进行中。下一步先核对Git训练前基线，再修复SSE协议/冻结源码/累计预算/真实ERP证据后执行同额度新attempt，不训练、不删除失败。

## T45 已完成：反馈两阶段归档与正式基线闸门

T45 代码入口 `src/agent/evolution/feedback.py`，测试 `tests/acceptance/test_t45.py`。Feedback 使用严格公开白名单、固定 rule/category/evidence 引用、owner/episode/run fencing、Mongo CAS 与 pending -> 完整 EpisodeStore seq/chunk/hash 核对 -> 发布两阶段协议；缺事件、缺块、hash 损坏或事件写失败保持未评分，不回退并发事件序号。baseline config 只做零模型调用的模型身份、URL、预算和 Mongo/ERP/OpenSandbox/MCP 服务字段检查，不返回凭据。

最终 gate 已通过：`artifacts/tasks/T45/20261003T153638Z/receipt.json`，22 unit + 9 真实 Mongo integration + 46 T44/T41 regression，0 failed/0 skipped；Ruff 与 mypy 亦通过。修复前的 `151334Z` 和修复中间失败 attempt 均保留；最终在隔离 `.venv-linux-t45` 与 `/tmp/jdk21` 中完成真实 gate。

下一条精确命令：先审计真实 Actor 基线与 TRACE/Curator 实验范围；不得把 T44 的条件敏感性或 T45 的组件 gate 写成学习收益。

## 最新交付：T44 学习空间基线完成，下一步反馈持久记录/真实基线

T44 done/not_reviewed，artifacts/tasks/T44/20261003T133318Z/receipt.json：27零模型unit + 学习空间报告命令 + 144 T37/T39/T40/T41/T43规划回归，0 failed/0 skipped，所有artifact hash核对。报告`artifacts/tasks/T44/20261003T133318Z/learning-space.json`：4原子train、4未见组合/control test、5反事实；每组只改预算/来源/交期/分批声明条件，独立judge证明status或best改变；source-missing=unresolved，business-infeasible=infeasible；Feedback白名单含success/constraint_status/environment_status/error_categories/rule_ids/evidence_refs，区分SUBOPTIMAL与硬约束失败，不含offer/order/数量/目标答案。claims明确proves_condition_sensitivity=true、proves_actor_learning_space=false、proves_learning_gain=false、curator_run=false。

入口scripts/planning/learning_space.py、tests/acceptance/test_t44.py、docs/runtime/learning-space-baseline.md。首轮132449失败（126回归，漏T39的18项，门槛144）保留；第二轮133318补T39后通过。T38已253通过登记。下一包可做控制面Feedback与Episode持久归档/真实Actor基线闸门；真实模型调用仍需具体新费用额度，不沿用任何历史额度。

## 最新交付：新 T38 完成，下一包验证学习空间

T38 done/not_reviewed，artifacts/tasks/T38/20261003T111735Z/receipt.json：19协议+24真实Mongo/OpenSandbox+85原沙箱+125规划回归，共253passed/0failed/0skipped，8证据hash核对。上一轮工具观察handle已失效，真实receipt证明gate11:28 UTC终态passed，不重跑。12相关文件Ruff通过，5源码mypy按Linux远端目标通过。首轮unit18/1失败与17真实初轮、42扩展和最终4边界专项XML保留；旧kernel/Jupyter失败保留。

入口src/agent/planning/computation.py、computation_protocol.py、computation_process.py、tools/planning_computation.py，说明docs/runtime/persistent-computation.md。可信scope、Mongo JSON版本/operation CAS、声明read_names、仅success原子发布；Linux subreaper实际停止/reap double-fork/setsid后代，无法确认持久隔离（含API重复重连）；取消先撤销提交资格，generation锁覆盖Mongo发布；API执行中崩溃显式recover、容器换代加载JSON，不重放代码；丢Mongo发布应答先撤销再核对last_commit。Actor默认工具改computation，build_planning_actor保留kernel参数名兼容装配，旧kernel工具不进入Actor。函数/模块/DataFrame对象身份不保持，网络/文件副作用不承诺回滚，订单仍独立MCP+逐单审批。

无付费模型。完整目标active/incomplete：真实Actor自主计算/学习空间与失败成功轨迹、TRACE独立Feedback/Curator/完整环境Snapshot/组合反事实/学习收益、动态异步/refinement尚待。下一任务先登记学习空间基线运行与独立Feedback，具体付费调用需新的费用额度，不沿用历史预算；可先完成零模型的评测接口/冻结环境与真实组件。上轮dev helper启动OpenSandbox控制服务Windows pid57124/18080是否仍在需只读核对；不触碰用户Mongo27017。

## 最新交付：T43 差量重规划完成；用户已授权重定义T38

T43 done/not_reviewed，artifacts/tasks/T43/20261003T104947Z/receipt.json：新增21+原T37/T39/T40/T41回归104，共125passed/0failed/0skipped。10文件Ruff/9源码mypy通过，证据hash核对。入口src/agent/planning/reconciliation.py、models/checker/orders、scripts/planning/judge.py及PATCH规划goal和正式chat stale中断恢复。schema2冻结真实成功订单原成交价/计划交期/数量，Plan表示追加量；真实ERP逐单核验后修订，局部来源刷新并归档，CAS覆盖revision/proposal/orders/execution。旧审批不可用、新审批只追加，原Episode/bank保持；60小实例与独立枚举对照。无付费模型/无T38运行；明确scripted接线，尚不能证明Prime/学习效果。

失败可见：104440完整gateFAILED10failed/8passed（ERP响应没有MCP的ok字段、夹具header/来源线程/cookie错误），原104回归通过；after-erp-contract-fixes17passed/1failed（测试误猜STALE_PLAN HTTP409，既有协议400），正式chat窄1passed；最终104947完整125通过。不削减原业务断言。

用户最新明确授权修改T38：OpenSandbox内每步独立Python执行，持久权威版本化JSON计算数据；每次只加载需要的数据，由Agent自己编写计算代码，成功后校验/发布新版本，无完整固定planner。失败不覆盖旧数据，实际停止进程及子进程，无法确认终止则隔离环境；保留operation/version/owner/session fencing，旧执行不能迟到提交。Mongo权威、sandbox执行副本；不保证回滚代码所有文件/网络副作用，正式下单仍独立MCP+审批。该最新选择替代旧活kernel/对象身份保持/Jupyter通道补丁要求；须先更新计划/契约/受影响Actor与工具、保留旧失败证据，然后按新T38验收，不再做旧daemon诊断。当前state T38仍blocked，下一步登记已接受变更后显式reopen。完整目标active：学习空间仍应先于Curator，TRACE真实学习/组合反事实、动态异步/refinement各另验，付费调用需具体新额度。

## 最新交付：T42 Vue规划目标录入与实际订单展示完成

T42 done/not_reviewed，artifacts/tasks/T42/20261003T103423Z/receipt.json：70Vitest+真实T40API14，共84passed/0failed/0skipped，vue-tsc/vite生产build通过，证据hash复核。Vue测试为mock网络的组件行为；API回归使用真实Mongo/checkpoint/Java/MCP及显式scripted模型，没有完整浏览器或live模型验收。本轮无付费模型/无T38运行。

入口frontend/src/components/PlanningGoal.vue、api/client.ts与App.vue。创建独立thread目标表单支持预算/数量/交期/必需与可选/优先级/部分采购/供应商拆分；不自动调用chat/model。创建后用户发送消息走既有正式规划graph，逐单批准/拒绝卡保持。历史/每次run结束GET服务端目标与实际订单，未写不乐观显示成功，不确定写入提示先核对；刷新失败显示错误并保留上次目标，旧thread迟到响应丢弃，切账户销毁表单，run期间不切身份/目标。App原shallowRef机器非reactive导致UI不更新，本包增加reactive包裹并用集成组件测试验证。

失败保留：103210 Node20.18.1使jsdom无法启动，报告落在frontend/artifacts错误相对位置，gate明确failed；路径修正后103310仍Node20.18.1，7启动errors，gatefailed。WSL直接设置NODE_HOME未传入Windows；最终通过Windows Python进程设置已有Node24.13.0，不改机器环境或依赖。精确复验命令：.venv/Scripts/python.exe -c 'import os,runpy,sys; os.environ["NODE_HOME"]=r"C:\Users\34114\AppData\Local\nvm\v24.13.0"; sys.argv=["scripts/gate.py","T42"]; runpy.run_path("scripts/gate.py",run_name="__main__")'。测试只启停自建Java/MCP/HTTP和独立测试库；Mongo用户服务保留。

完整目标仍active/incomplete，T38按用户要求blocked，禁止自动恢复诊断/gate。尚缺真实Actor持久计算/学习空间、TRACE Curator/独立Feedback/完整环境Snapshot/学习实验、持续差量对账与进程崩溃恢复、动态异步/refinement；后续付费调用必须具体新增预算授权，不能拿组件成绩当真实学习效果。已登记暂无其他pending；下一包应登记独立goal修订/差量对账或Feedback/训练完成接口，先读execution/HANDOFF，check→next→packet，一次一个，不回T38。

## 最新交付：T41 Episode聚合与冻结技能逐回合编排组件完成

T41 done/not_reviewed，artifacts/tasks/T41/20261003T102720Z/receipt.json：19新检查+T40/T07回归38，共57passed/0failed/0skipped。真实Mongo/checkpoint/Java/MCP；模型明确scripted-component，技能正文synthetic fixture，未安装人工学习策略。Ruff相关8文件及mypy6源码通过。首轮102018 gate FAILED8failed/8passed（替身签名、系统消息blocks、错误码），修正后16窄检查通过；raw-offload-component17通过；102551完整18+38通过；最后补转义密钥反例后102720完整19+38通过。所有失败/中间记录保留，SDK never-awaited warnings未隐藏。

代码入口src/agent/evolution/episodes.py与orchestration.py、planning/actor.py、正式chat生命周期、默认规划runtime装配。一个业务goal跨chat/resume/run聚合同一open/unscored Episode；首次固定文字bank与配置模型provenance，晋升/撤回不改变在途目标；每次Actor模型动作前按当前状态及全部描述选择有序ID并读取精确正文，下一回合不累计。空bank基线不制造学习策略。选择器同模型/同累计预算（组件替身明确独立），原始typed消息/完整工具输出、callback父ID/usage、每次审批与实际订单留证；官方SDK offload前保留原文已实测。大事件分块/hash/seq校验，故障即失败，并阻止ToolNode捕获后继续调用下一模型。凭据与转义凭据拒绝/脱敏。

边界：文字bank不等于完整环境Snapshot；API completed/approval executed不当采购成功。独立Feedback/训练完成、Curator层级初始化与成功失败对照、真实Actor/kernel/学习空间/组合反事实/学习效果均另验。无付费模型、无T38服务/诊断/gate，T38仍按用户要求blocked。完整目标仍active。下一包Vue规划目标录入与订单展示，可独立推进；不得自动恢复T38。

## 最新交付：T40 独立规划图与正式逐单审批恢复接线完成

T40 done/not_reviewed，完整组件gate artifacts/tasks/T40/20261003T100854Z/receipt.json：14项新检查+原T39/T13回归50，共64 passed/0 failed/0 skipped。9相关源码/测试Ruff通过，7源码mypy通过。模型明确ScriptedChatModel，仅驱动组件接线；Mongo/checkpoint、MCP、Java及内部审批HTTP真实。没有调用付费模型，没有启动kernel、T38诊断/构建/gate；T38仍blocked（用户要求跳过）。完整目标仍active，Prime实际使用、学习空间、TRACE、持续目标和动态异步尚未完成。

入口：src/agent/planning/actor.py、src/agent/tools/planning.py、src/api_view/api/planning.py；POST /api/planning/{thread}/goal输入budget/demands，服务端查询真实ERP并保存sources原JSON；对应chat/resume/state选独立planning provider。已有课程主子graph/技能保留。模型仅有公开problem/source/check/submit和通用kernel工具，shell/task/直写工具在模型与工具边界拒绝，私有裁判不注入。规划runtime接入tests/live/stack.py默认Demo装配，新增模型不生成调用直到用户使用；本轮未启动完整live stack，不能声称运行演示已通过。Vue规划目标录入尚未做，审批SSE沿用已有形状。

关键失败和原因：首轮100319 gate FAILED，4新检查passed/7初始化error，SDK要求StateBackend实例而不是类；原50回归通过。修正夹具后的component-after-constructor.xml为3 failed/8 passed：真实checkpoint显示LangGraph在同工具多次interrupt复用任务ID，错误别名设计把第二单绑第一单。已经移除别名存储，规划SSE/state公开interrupt_id直接使用原pending_action ID；原官方graph序列/Command(resume)不改。component-after-business-ids.xml为11passed；补未展示单/旧批准复用和身份伪造反例后最终完整64passed。全部失败收据/XML保留，不改订单数、重复写入或恢复断言。

正式API测试证明：两单分别批准各一次Java写入；拒绝第一单零写，拒绝第二单保留第一单；重建graph恢复第二次审批；直接Command(resume=approve)无服务端决定不写；尚未展示第二单/旧第一单审批不能消费当前中断；原审批operation/hash/goal binding保持。测试自建服务、独立库正常清理，未触及用户服务。

下一包先推进TRACE轨迹/逐回合技能选用基础设施与Vue规划输入，均可不碰暂停的T38；不能制造真实训练经历或提前造Curator。实际模型/Prime基线、组合反事实、学习空间与迁移实验仍需T38恢复验收及用户具体新增费用授权，历史额度不沿用。完整持续差量对账和inflight进程崩溃恢复另需真实核验。不得自动回到T38试跑。已登记暂无其他pending包。

## 最新交付：T38 按用户要求暂停，T39 多单组件完成

用户明确要求先跳过 T38 推进进度。T38 保持 blocked/not_reviewed，无通过receipt；未再运行诊断、构建、kernel或T38 gate。代码及所有失败记录保留，未要求前不自动恢复补丁循环。

T39 done/not_reviewed，最终收据 artifacts/tasks/T39/20261003T095048Z/receipt.json：18项新检查和原T12/T07回归52项，共70 passed/0 failed/0 skipped。初轮094828收据68通过保留；检查明确旧grant在真实内部HTTP得到STALE_PLAN、网关缺验证配置拒绝后，产生最终收据。真实Mongo独立库、独立MCP进程、Java/H2、真实HTTP内侧验证；双供应商逐单写入，直接读ERP核对订单内容/数量，重放无额外订单。测试自建服务和独立测试库均正常清理，未触及用户服务。无付费模型调用。

代码入口 src/agent/planning/orders.py；pending_actions可选planning_binding；新planning_goals唯一owner/thread/goal文档保存revision/proposal、执行租约和结果；规划签名grant可选approval_ref，MCP执行前调用MCP_APPROVAL_VERIFY_URL并使用INTERNAL_SERVICE_TOKEN复核。默认FastAPI装配已安装版本guard，旧课程grant仍仅验签，不能误称原路径已有HTTP复核。10个组件/审批/协议/测试文件Ruff通过，7源码mypy通过。web_main原有未使用resources赋值的Ruff F841仍存在，本轮没有改其无关资源语义。

重要边界：这是真实多单审批执行组件，不是完整Actor、Vue批次界面或TRACE学习。网络/超时/取消/上游retryable结果保留uncertain锁，只准同operation对账重试；inflight中进程崩溃不会自动放锁，专门恢复尚待后续包。已写订单跨修订保留，新的全量批次报RECONCILIATION_REQUIRED，持续目标需差量对账。完整Actor/kernel场景仍须T38通过，不以手工候选检查替代模型规划；后续预算仍需具体授权。下一工作优先登记不依赖T38验收的Actor工具/入口接线或TRACE机制组件，完整联调/真实学习另验，不能回到T38试跑。已登记任务中无其他pending包。

## 用户授权跳过 T38，推进独立多单审批组件

T38 改为 blocked（用户要求暂停让出活动位，不是完成），所有失败与代码保留；不再启动诊断、构建或完整gate。新增 T39：依赖已通过的 T37/T12/T35，完成版本绑定的逐单审批和真实 Mongo/MCP/Java 多单组件。完整 Actor/kernel 联调仍依赖 T38，TRACE 与持续目标未删除。无付费模型调用。

## 最新处置：停止补丁循环，隔离候选并做一次路径对照

默认 infra/sandbox/sandbox.toml 已恢复官方 opensandbox/execd:v1.0.22；未经稳定验收的v8补丁保留在独立 sandbox.t38-candidate.toml，不删除源码、不伪造通过。原三小时工作未交付通过验收的T38，关于根因已解决的判断撤回。

只执行一次 scripts/diagnose_t38_routes.py，无完整gate重跑、无新daemon构建、无模型调用。证据 artifacts/tasks/T38/route-diagnostic/20261003T091415Z/result.json、control.log、probe-source.py/SHA256；外层日志bounded-route-diagnostic.log。一个官方原版容器，三个独立context、相同两条代码：Windows官方SDK、容器内HTTP→execd、容器内Tornado→Jupyter均分别得到711/712与真实终止信号；各路径7.72/1.83/1.48秒（不含创建启动）。这些是路径诊断，不是T38验收或稳定性证明；未复现偶发故障，不能认定WSL网络正常或有错。观察时Docker8GiB/32CPU，现存容器低CPU、未显示明显内存压力；单次快照不能排除历史压力。

诊断仅有一次矩阵，没有重试失败请求。诊断容器206faf07-0f10-45ca-ace2-3a8b47f2ce6d及自建控制服务均由finally关闭，未触及其他服务。T38仍in_progress/not_reviewed，无通过receipt。默认runtime不再自动加载实验patch。今后若继续，只从官方最小路径逐层接回proxy/Mongo/取消找最小失败；未得到因果证据不继续改daemon，不自动重跑整套。上轮失败现场与历史在下方保留。

## 最新停止现场：用户要求停止重复试跑（2026-10-03）

T38仍in_progress/not_reviewed，无通过收据；本轮停止诊断与测试，不自动恢复同一路径试跑。最新完整 artifacts/tasks/T38/20261003T090118Z/receipt.json FAILED：30unit通过；19kernel中17passed/2failed，均shell/iopub readiness超时；原回归被本次用户停止请求中断，exit4294967295、缺JUnit，不是通过。gate session8854已经退出1。停止精确本轮测试PIDs63084/48392，让runner保留真实失败收据。前一轮v8真实19passed只是局部证据，不能声称问题已稳定解决；之前关于有效根因的表述过早。

应用测试与控制服务一直是Windows原生.venv/Scripts/python.exe，WSL是启动shell。错误由容器execd返回，内侧execd↔Jupyter使用同容器127.0.0.1；不是已证实的WSL到宿主断连。较早Docker镜像下载IPv6失败属于另一链路。WSL/Docker底层因素未排除，但当前根因未定，不能因此替换技术栈。今后诊断应先设计单变量对照和明确观测点，不再先改daemon再重跑整套。

当前源码是v8通道补丁：16原生race tests通过；官方Tornado同格式20次、直接官方Go20次、热启动debug SDK8次诊断通过，均不代替业务gate。当前补丁镜像tag rush-harness/execd:1.0.22-kernel1，binary df137a7d5e611b32d4154babd2f5bad47d440ef90c18c6c67e93e2ac6cf5b767；代码和全部失败记录保留，没有回滚用户修改，没有付费模型调用。本任务control session99835/PID14884已停止；c620诊断daemon PID509已停止。Mongo用户服务27017未动；失败数据库/残留测试容器保留供调查，不bulk删除。

## 2026-10-03 T38 持久 Python 工作区（in_progress）

T37完成，T38仍未通过完整gate，review=not_reviewed，无付费模型调用。入口 src/agent/planning/kernel.py、kernel_protocol.py、src/agent/tools/planning_kernel.py。extension0.1.2/SDK0.1.16锁定；可信scope、Mongo三集合、JSON显式检查点、跨进程重连、generation/lease fencing、输出offload、shell/kernel同owner队列、取消monitor均已实现。官方镜像入口显式保留；Windows环境缺文件已uv sync --frozen --reinstall修复，不更换锁版。

完整gate071215/073404均FAILED：unit26或30通过、85原回归通过，kernel超时缺JUnit。原callback提前退出/延迟/Connection:close实验均无效并已删除，失败XML/log保留。最新native-fresh-channel-kernel同样300s超时无报告；实际容器c6201...binary与v3 SHA53e3...一致，镜像tag竞态已排除。sandbox内Tornado诊断新连接令先前被卡住的请求执行，随后五次诊断正常；见fresh-channel-container.log、tornado-reconnect-diagnostic.log。

固定官方execd源码tag docker/execd/v1.0.22=4a9db411879601610843af9c8e03563694325b2a，source SHA934b517e10e20defd4d3bada8f07082632db3a5f701ed9302b27977c8d3de605。patch在infra/sandbox/patches/execd-v1.0.22-jupyter-readers.patch：固定reader连接/parentID筛选/query与header session一致，最新v4每context持有一个串行通道，断连清引用、删除context关闭。build_kernel_execd.py验证源码、Go1.25.9、patch/test不变，原生execute/auth/session-race拒绝零/失败/skipped，固定官方base digest编译打包。v4原生13passed，binary e5ebbaf11c8c79940147d8c2d432bd31cd0547d555ed739bd7490d01708beee3，image cd335f2508d85745b6d80adc6b604d1f31d35d4bb9ed331565074771d48a6c81。完整provenance在native-builds最新目录与reproducible-native-build-v4.log；v2构建中改patch属无效provenance，v2-invalid.md保留。原native baseline foreign-parent真FAIL；更早广包exit0含2上游skip不作为强制通过。Docker compiler拉取IPv6失败/原cwd错误均保留。

最新Ruff与五源码mypy通过。正在运行19项真实Mongo/OpenSandbox验收：session16746，日志native-persistent-channel-kernel.log、目标XML同名；已9dots，先poll，不盲重跑。当前本任务control session29395/PID63216/18080；旧51244已停止，Mongo27017用户服务不动。旧失败库/测试容器保留，清理仅exact本任务资源。Windows应用命令require_escalated；不要Linuxuv覆盖Windows.venv。

下一步：19真实checks通过后完整 .venv/Scripts/python.exe scripts/gate.py T38，同attempt所有checks通过/hash依赖核验才done；未过则保留失败定位。build复现命令：GOCACHE=/tmp/t38-go-cache GOPATH=/tmp/t38-go-path python3 scripts/build_kernel_execd.py --go /tmp/t38-go-sdk/go/bin/go --source /tmp/t38-opensandbox-pinned.tar.gz。宿主只编译基础设施，Actor仍只OpenSandbox。运行/版本/契约已记录通道patch。后续真实Actor多单审批、TRACE学习、动态重规划仍未实现，付费调用须具体额度。

最新补充：v4完整真实test在第10项超时无JUnit；v5单shell-readiness 15passed/4failed，v6重发相同探针也15/4，触发Duplicate Signature，v7独立签名+双通道14/5，均失败保留。镜像中除被官方跳过的python3，仅python匹配，已排除map无序候选。官方Tornado发送Go相同格式20/20、直接官方Go客户端固定python20/20，以及在旧失败c620容器热重启元数据debug daemon后的官方SDK8/8诊断通过；不当作任务gate。诊断只在ignored artifacts代码；最终patch无调试日志。c620容器原daemon PID13已停止，/tmp/t38-diag-execd由session99124运行；精确容器属于本任务，完成后清理。

v8新增等待服务端启动IOPub idle才发握手探针，已16native-race通过，binary df137a7d5e611b32d4154babd2f5bad47d440ef90c18c6c67e93e2ac6cf5b767，image4b5a8d5d6e26a599a875c984ca4144733aea2626b14c13c05c5307364bfaa6ce。build日志v8/provenance保留。真实19项test session52103已19passed/0failed/0skipped，78.69秒，native-startup-readiness-kernel.log/XML；控制session99835/PID14884，18080，旧53352已停。当前正在最终完整gate，final-startup-gate.log记录日志；收据待核验前state仍in_progress。此前失败不覆盖。

## 2026-10-03 采购规划场景 T37

用户已授权按场景与裁判 → 持久计算 → 技能学习 → 持续重规划推进，最新请求取代旧学习文档的“不自动继续代码”现场限制。完整范围见 docs/runtime/procurement-planning-implementation.md；不新增模型付费调用，训练额度尚未锁定。

T37 done，review=not_reviewed。公共数据/校验在 src/agent/planning/；独立穷举裁判在 scripts/planning/judge.py，不注册 Actor 工具/同步进沙箱。fixtures/planning/ 有提案目标与8种公开单独/组合/反事实条件，非封存测试或已证学习收益。真实ERP读取不回退seed；允许部分量与允许供应商拆分分开；必需量/交期硬约束、可选优先级覆盖、成本依次比较。未知来源用乐观界与已核实可行域区分 unresolved/infeasible，合法方案与最优性分开，支持同目标不同解。修订输入标影响范围，所有旧revision方案拒绝；尚未接真实批准失效。

完整 gate `.venv/Scripts/python.exe scripts/gate.py T37`（Windows锁定uv环境）生成 artifacts/tasks/T37/20261003T061537Z/receipt.json，53 unit +2真实Java/H2 passed/0 failed/0 skipped，hash已核对；改变独立seed的交期/价格导致不同目标，实际订单从Java查询。80生成小实例用独立budget DP核对，Ruff及7源码mypy通过。061028失败（UTF-8读取和测试DP空价格）与061318中间通过、component-utf8-fix.xml保留。首个直接pytest未运行因缺iniconfig，gate锁定环境补齐71个既定包；未更改uv.lock。WSL内uv默认cache只读、Windows进程互操作受限，实际应用检查通过require_escalated调用已锁定Windows Python；不要用WSL uv替换Windows .venv。

下一包T38已登记pending（验收目标测试尚未创建，不计通过）：持久Python工作区；读取 execution/HANDOFF，check → next → packet。已安装 opensandbox=0.1.16 的低层 code_interpreting API支持context；PyPI与官方wheel预核查确认 opensandbox-code-interpreter=0.1.2兼容>=0.1.6,<0.2，Python包名code_interpreter，CodeInterpreterSync.create(sandbox)可包装既有SandboxSync。最新版1.1.0要求SDK>=1.1，不升级。wheel只下载/tmp，扩展尚未正式安装/锁版；先核实签名/锁版与真实镜像kernel能力，不用shell每次启动Python冒充持久状态。接入owner/thread/session、同owner执行队列、Mongo运行登记、取消/重连/重建协议，再接规划Actor。多单审批完整任务、TRACE回合编排/成功失败比较、冻结组合迁移、动态异步、在线refinement/可执行技能演化仍未实现。T37没有启动常驻服务，两个Java测试进程已由fixture关闭。

## 2026-10-02 分章学习文档

T36 done，review=not_reviewed。用户已切换为面试项目学习，明确不希望逐题模拟；当前产出为docs/learning/README.md、01–07七章和PROGRESS.md，全部学习状态待确认。后续按用户指定part集中解释与补充，不自动继续代码修正或逐题面试。docs/plan/review.md已加入口；未改应用代码。artifacts/tasks/T36/20261002T142011Z/receipt.json command passed，检查69个本地引用和必要章节结构，hash已核对；141940因WSL没有python失败保留，改用现有python3。此gate仅文档，不代表应用验收、页面渲染或用户理解。后续代码遗留事项见T35记录，但不是本次学习请求自动启动的任务。

## 2026-10-01 修正工作

T35 done，review=not_reviewed。artifacts/tasks/T35/20261001T111034Z/receipt.json：归档8、T19 28、装配35、聊天预算记忆45，共116 passed/0 failed/0 skipped；hash核对，补充initialization-dependency.xml T34 7 passed。自动/主动同步异步官方SDK摘要模型生成前，独立Mongo查询完整原文与todos；写入失败不生成摘要/压缩事件。UUID追加、typed消息完整工具参数/配对/artifact、protected state、owner/thread/namespace/scope及application_run_id已接入，主子分别用实际模型。应用运行ID避开SDK通用run_id重入语义。baseline两项真实覆盖/丢字段失败及sdk-archive-first/checkpoint/planning中间失败XML保留；中间6项失败主要为todos OmitFromInput及run_id重入夹具问题，不能记为6项生产缺陷复现。最终测试使用生产build_main_agent，todos由真实write_todos写入。Ruff六文件通过，归档/工具摘要/聊天三源码mypy通过；初轮更广mypy的main_agent后端名义类型、YAML缺存根、loader TypedDict展开旧问题未修，不能称全仓静态通过。SDK异步callback/Starlette警告保留。控制服务session11620/PID55396暂留。后续优先处理不可覆盖评测attempt与冻结评测，再处理真实异步分析及全仓静态门槛；自进化晋升/回滚闭环仍未完成。

T34 done，review=not_reviewed。artifacts/tasks/T34/20261001T105230Z/receipt.json：初始化7、原T13 32、预算记忆13，共52 passed/0 failed/0 skipped；hash已核对。baseline七项真实行为失败与中间修正通过XML保留。初始化/恢复原问题/偏好/终态检查纳入deadline，取消不阻塞，迟到结果不能进astream；先存状态并释放registry再一次done，失败收尾/重放不重调提供器。Ruff两文件/mypy聊天源码通过；SDK callback警告未隐藏。下一包接自动及主动摘要前Mongo历史归档；控制服务session11620/PID55396保留。

T33 done，review=not_reviewed。artifacts/tasks/T33/20261001T101948Z/receipt.json：新9、T19 28、pool36、原T08 24，共97 passed/0 failed/0 skipped，全部同一完整attempt，hash已核对。真实调用熔断与epoch保护、锁内单次故障探测/恢复、按数据库稳定marker隔离warm池清理；原业务断言不改。091644恢复失败、093041/094537端点失败、100024跨池误删失败及预算依赖4/2均保留；补充预算依赖6 passed。相关Ruff九文件/mypy六源码通过；SDK异步callback never-awaited warnings未隐藏。控制服务session11620/PID55396保留。下一包优先修复graph_provider在保护区外导致初始化失败没有done、Mongo仍running及初始化不计超时的边界，再接摘要归档。

T32 done，review=not_reviewed。artifacts/tasks/T32/20261001T090628Z/receipt.json：共享预算6/0/0、原 T19 28/0/0、聊天+记忆39/0/0，共73项。每次 API run 独立 callback，主子整体累计；Mongo 线程原子扣额；异步等待总 timeout；首次耗尽阻止所有后续模型/工具调用并 failed，不记成功采购历史。runs.budget_usage 有可读计数和失败原因。子代理继承官方局部 error guards 与 offload。Ruff 相关10文件通过，mypy 四源码通过；API 两个返回注解及恢复 decisions 类型检查修正，协议不变。基线5 fail/1 pass、085049/090032中间通过收据保留，停止边界修改后090628完整复跑。默认80/120/900与已批准实现同步，未增加额度。熔断、摘要归档、attempt评测、异步分析和全局静态门槛仍待后续；控制服务 session11620 / PID55396 保留。

T31 done，review=not_reviewed。artifacts/tasks/T31/20261001T082559Z/receipt.json：新9/0/0、原发布45/0/0、故障恢复/隔离45/0/0（T21 当前20、recovery10、isolation15），Ruff 与两源码 mypy 通过。持久预占版本，reserved/failed 隐藏；读回 manifest 和文件后 token 条件转 persisted；缺失/预占版本不能分配；失败编号和旧孤儿不复用。起始 revision 单次 CAS、UUID staging/download 已接入。baseline 8 fail 和 081643 failed 均保留，恢复最低33未降低。原 T17 两处先准备合法版本的夹具、一个 UUID staging 目录接口断言修订，原业务/缓存/隔离断言保留。并发相同内容可以保留两个候选，但旧 revision 仅一个赢家，顺序重放选当前同内容版本。完整 lineage/晋升回滚仍未实现。控制服务 session11620 / PID55396 / 18080 暂留后续验收。

T30 done，review=not_reviewed。artifacts/tasks/T30/20261001T075907Z/receipt.json：新技能验证 9/0/0、原 T17 45/0/0，真实执行/清理均通过；相关 Ruff 与两源码 mypy 通过，mypy 初轮推断错误后仅补充字典类型注解。独立输出目录、相对路径证据、非空产物、合法示例已接入；reorder-cost-summary 独立 5 个服务端业务案例，不依赖候选自身 example。指导技能只记录结构/行为未验证，保留发布能力。074015 缺陷复现、075308 缺服务、075448 清理失联失败均保留。测试控制服务当前前台 session 11620 / Windows PID 55396 / 18080，后续回归暂留；后台启动器方式已弃用，40524/15372 已退出，上一轮遗留测试容器已清理。预算/熔断/归档、版本并发、评测 attempts、异步学习器、完整静态门槛仍待修正。

T29 done，review=not_reviewed。artifacts/tasks/T29/20261001T072700Z/receipt.json：真实 API 记忆 7/0/0、原 T18 26/0/0、原 T13 32/0/0；相关 Ruff 通过。用户原文偏好在调用前写入，主子实时注入；每 run 独立 callback 收集真实委派 ERP 成功证据，完成后更新历史。偏好/历史分键，避免自动历史覆盖新显式偏好；记忆保存失败返回 MEMORY_PERSISTENCE_FAILED/failed。失败收据 071602（夹具库名过长）、071936（测试 MCP 参数误用）保留。审批恢复读 checkpoint 最近用户消息，当前 turn 直接使用用户原文。尚未保证并发历史无损合并，预算/熔断/归档另包。

T25 done，review=not_reviewed。真实 gate：artifacts/tasks/T25/20261001T022622Z/receipt.json（acceptance 6/0/0、plan-contract 43/0/0）。dev 显式 preserve_data=True；Mongo 原文、ERP 订单及幂等状态在异常退出和重启后保留。首次 OpenSandbox 未启动的失败收据 20261001T022427Z 保留。补充 T24 文档/启动器 13 passed，默认会话启停未重复运行；本次相关 Ruff 通过。测试独立 UUID 数据库已清理。本次启动的本地 OpenSandbox 控制服务 pid 15372 暂留用于后续回归，最终应停止。旧 T00–T24 收据仅保留为历史，旧“只剩人工审阅”结论被评审否定。

T26 done：artifacts/tasks/T26/20261001T023408Z/receipt.json，2/0/0。/artifacts/ 限定根目录，src/agent/artifacts 已可追踪但尚未提交。两个失败 attempt 保留。

T27 done：artifacts/tasks/T27/20261001T024032Z/receipt.json（错误协议 6/0/0、原 T17 45/0/0）。内部校验异常统一 dict，对外 JSON 协议保持；修复前失败收据保留。下一任务修复 F03 技能发现/作用域/缓存。

T28 done：artifacts/tasks/T28/20261001T065359Z/receipt.json（发现+原 T11 35/0/0、原 T10 24/0/0）。接入官方技能源、scope 过滤、同步恢复后刷新；真实 Mongo checkpoint 同线程下一轮与 config-only 身份已验证，移除技能副本后从 Store 恢复并发现；chart_params 已真实同步且纳入 revision。新 runtime_context.runtime_owner 可供其他运行钩子统一解析身份；相关 Ruff 通过。失败/中间通过 attempt 保留。

## 状态

- 2026-09-21：**25 个任务全部 done，无 blocked**。`python scripts/plan_guard.py check` 报
  `25 tasks; 25 done`，`next` 报 `No eligible pending task. Review blocked tasks or final acceptance.`
- 全部 gate 均已复跑通过（含 T06 的 live 能力矩阵、T00 的 java/frontend 构建）。
  T23 最终 `acceptance [live] 41/0/0`，T24 最终 `acceptance [integration] 14/0/0` + java + frontend。
- **T21 的故障联调审计没有发现需要修的缺陷**：`src/` 下无模块级可变状态，MCP 身份用 `ContextVar` + try/finally reset，未绑定时抛错而不默认。已用测试钉住（详见下文）。
- 三个外部凭据已全部到位并**实测可用**：模型、智谱搜索（余额已充）、ModelScope 图表（18 个工具）。
  自检命令 `python scripts/check_external.py` —— 注意 `capability_report()` **只判断配置项是否存在，
  不判断是否有效**，所以两者都要跑。
- **T23、T24 均已完成**：T23 `artifacts/tasks/T23/20260919T154444Z/receipt.json`（live 41 例），
  T24 `artifacts/tasks/T24/20260921T143918Z/receipt.json`（14 例 + java + frontend）。
- **剩下的事只有一件，而且不是我能做的**：`state.json` 里所有 `review` 仍是 `not_reviewed`。
  自动检查与真实检查全绿**不等于**用户已审阅——那一列只能由人填。演示轮次证据在
  `artifacts/live/r1/`（24 次试验、23 次成功、每个场景 ≥2）。
- **覆盖报告在 `docs/runtime/coverage-report.md`**，由 `python scripts/coverage_report.py` 生成（不要手工编辑）。它把状态分成三列且互不替代：自动 / 真实 / 人工。
- 中间件矩阵 **8 个槽位全部实现**，`middleware_inventory()` 会自己反映实现状态与真实 hook。
- 本地服务：MongoDB 由 compose 常驻（27017）。OpenSandbox 控制服务（18080）**现在由
  `python scripts/dev.py up` 自动拉起**（起不来会打印可粘贴的命令），`down` 只停本次会话启动的那个；
  单跑验收测试时仍需自己起（`tests/fixtures/sandbox_service.py` 的 `START_COMMAND`）。
  **按需由测试拉起、跑完即停**：Agent Protocol 服务（8123）、报价站（8088）、Java ERP 与 MCP 网关（随机端口）。
- 沙箱容器残留：0（每次验收后核对；T16 单跑沙箱用例后立即计数为 0）。
- `.env` 已创建：模型三件套、智谱、ModelScope 均已验证可用。
- 依赖族已由 T06 从 resolved 升为 **verified**（见 `docs/runtime/versions.md`）。

## 已完成任务

| 任务 | receipt | 检查结果 | 关键交付 |
|---|---|---|---|
| T00 | `artifacts/tasks/T00/20260918T021254Z/receipt.json` | acceptance 24 + java-build + frontend-build | 骨架、`uv.lock`、`package-lock.json`、`mvnw`、`gate.py`、`doctor.py` |
| T01 | `artifacts/tasks/T01/20260916T051327Z/receipt.json` | acceptance 30 | `seed-v1.json`、`expected-v1.json`、6 schema、24 契约样例 |
| T02 | `artifacts/tasks/T02/20260916T012144Z/receipt.json` | acceptance 21 + java-build | Java 目录/库存接口、服务令牌、分页与错误信封 |
| T03 | `artifacts/tasks/T03/20260916T051329Z/receipt.json` | acceptance 22 + java-build | 订单事务、乐观锁、幂等账本（另 36 JUnit 含并发） |
| T04 | `artifacts/tasks/T04/20260916T013441Z/receipt.json` | acceptance 26 | FastMCP 八工具 + HMAC 授权票据（另 23 契约用例） |
| T05 | `artifacts/tasks/T05/20260918T021423Z/receipt.json` | acceptance 18 | 报价站（两种 DOM）+ 跨平台可复现技能 ZIP |
| T07 | `artifacts/tasks/T07/20260918T021110Z/receipt.json` | acceptance 24 | 三类持久化分离 + 用户隔离（另 5 集成用例） |
| T08 | `artifacts/tasks/T08/20260916T025704Z/receipt.json` | acceptance 24 | OpenSandbox 执行环境 + DeepAgents 后端适配（另 12 集成用例） |
| T09 | `artifacts/tasks/T09/20260918T021432Z/receipt.json` | acceptance 28 | 沙箱池、稳定代理、登记重连与恢复（另 8 生命周期 + 12 后端集成用例） |
| T06 | `artifacts/tasks/T06/20260918T021014Z/receipt.json` | acceptance 18（live） | CAP-01..08 实测矩阵、`src/agent/{config,env_utils}.py`、签名与真实流样例 fixture |
| T10 | `artifacts/tasks/T10/20260918T023549Z/receipt.json` | acceptance 24 | 三路虚拟文件系统、所有者作用域强制、`src/skills/` 五个预置技能、增量同步与 Store 恢复 |
| T11 | `artifacts/tasks/T11/20260918T021125Z/receipt.json` | acceptance 28 | 主 Agent 装配、YAML 子 Agent 与权限快照、规划持久化、委派上下文隔离、写工具无授权拒绝 |
| T12 | `artifacts/tasks/T12/20260918T023651Z/receipt.json` | acceptance 28 | 两层 HITL、冻结载荷与稳定操作 ID、执行闸门、内部校验端点、双击与重放防护 |
| T13 | `artifacts/tasks/T13/20260918T020442Z/receipt.json` | acceptance 32 | SSE 事件归一化、chat/resume/state/history/delete/cancel 接口、受管理 run registry、历史与 checkpoint 分离 |
| T14 | `artifacts/tasks/T14/20260918T020655Z/receipt.json` | acceptance 19 + frontend-build | Vue 工作台、POST+fetch SSE 分帧、UI 状态机、安全 Markdown、审批/补充交互（另 46 个 Vitest 用例） |
| T18 | `artifacts/tasks/T18/20260918T021226Z/receipt.json` | acceptance 26 | 四个显式偏好 + 两个自动历史字段的封闭 schema、跨会话/跨用户持久化、上下文注入到主子 Agent、自动历史无法触碰显式偏好 |
| T19 | `artifacts/tasks/T19/20260918T021238Z/receipt.json` | acceptance 28 | 大结果落文件（失败则保留原文）、摘要前全量归档、todos/中断幸存检查、业务错误不触发沙箱熔断、半开单次探测、主子共享预算 |
| T15 | `artifacts/tasks/T15/20260918T020716Z/receipt.json` | acceptance 24（live） | 真实智谱搜索（失败不转空成功）、单一 `chart_generator` 罩住 18 个远端工具、按图表族构造数据、三张真图落盘 |
| T16 | `artifacts/tasks/T16/20260918T020455Z/receipt.json` | acceptance 38 | 预置抓取分析技能补全、HTML→Markdown、两种 DOM 比价 2553.00、报告/CSV/图表三产件、受归属保护下载 |
| T17 | `artifacts/tasks/T17/20260918T011551Z/receipt.json` | acceptance 45 | `assign_skill` 工具、两条来源（generated/package）、沙箱冒烟双向验证、ZIP 穿越/符号链接拒绝、版本 manifest 与读回校验、Mongo 条件更新发布指针、assignment revision 短路与容器重建恢复 |
| T20 | `artifacts/tasks/T20/20260918T020329Z/receipt.json` | acceptance 32 | 独立 Agent Protocol 服务上真实后台 run、只读分析图、四个业务端点与 owner 映射、取消终态、`lost` 与 503 可见、跨进程沙箱白名单端点、前端后台任务组件（另 10 个 Vitest 用例） |
| T21 | `artifacts/tasks/T21/20260918T023358Z/receipt.json` | acceptance 23 | 故障联调：响应丢失/并发重试/票据绑定/两标签页、容器副本消失后从 Store 恢复且可执行、半写不可分配、恶意 ZIP 零版本、独立进程读回、owner 隔离与共享状态审计（另 `tests/integration/test_recovery.py` 10 例、`test_isolation.py` 12 例） |
| T22 | `artifacts/tasks/T22/20260918T064721Z/receipt.json` | acceptance 33 + java-build + frontend-build | 覆盖报告与生成器、R01-R28/V01-V22 追踪、`gate --all` 防自递归守卫、证据级审计（零 skip/零零收集/最小例数）、fake 与未实现标记扫描（AST 而非 grep） |

## 关键代码阅读入口（按依赖顺序）

1. `scripts/gate.py` — 验收执行器：参数数组、PATH 解析、JUnit 解析、attempt 目录、证据 hash、缺配置阻塞。
2. `erp/src/main/java/com/rushharness/erp/config/SeedLoader.java` — 只在空库播种，种子来自共享 fixture。
3. `erp/src/main/java/com/rushharness/erp/orders/OrdersService.java` — 校验/事务/版本/幂等重放，金额一律 BigDecimal。
4. `src/mcp_server/tools/registry.py` — 冻结请求体规范与工具面启动校验；`grants.py` — 授权票据签发与校验。
5. `src/api_view/web_config.py` — Mongo 客户端生命周期、checkpointer/store 的职责分离。
6. `src/agent/persistence/namespaces.py` — 用户 namespace 与 skills key 前缀隔离。
7. `src/agent/backends/custom_opensandbox.py` — DeepAgents 后端：显式实现 `execute`/`upload_files`/`download_files`/`id` 及其异步入口。
8. `src/agent/backends/sandbox_setup.py` — 沙箱运行时配置与工作区/只读规则准备。
9. `infra/sandbox/sandbox.toml` + `infra/sandbox/README.md` — 控制服务配置与拓扑说明。
10. `src/agent/backends/sandbox_proxy.py` — 稳定句柄：20 个协议方法显式委托、`replace_backend`、`begin_replacement`/`abort_replacement`、按 owner 的 shell 串行锁。
11. `src/agent/backends/sandbox_manager.py` — `SandboxStatus` 状态机与 `ALLOWED_TRANSITIONS`、`get_or_create` 的四级优先、`recover` 的“登记成功后再发布”、`cleanup_orphans` 的按标记跨进程回收。
12. `src/agent/middlewares/sandbox_health.py` — `before_agent` 探测与一次性恢复，自身不持有池。
13. `src/agent/config.py` — `ModelConfig`（无默认模型 ID，缺失即抛 `MissingConfiguration`）、`ServiceAddresses`、`capability_report()`；`redacted()` 是输出模型配置的唯一入口。
14. `src/agent/env_utils.py` — `.env` 解析（真实环境优先）、按名称判定的密钥识别、`redact`/`safe_headers`、能力分组表。
15. `tests/compat/capabilities.py` — CAP-01..08 八个实验，每个返回脱敏的 `CapabilityRecord`（含 checks 与 evidence）。
16. `tests/compat/streaming.py` — v2 信封捕获；`iter_v2_parts(..., subgraphs=True)` 是能否按 `ns` 归因的关键开关。
17. `tests/compat/signatures.py` — 已安装符号签名与 v2 词汇表，落盘为 `tests/fixtures/t06-signatures.json`。
18. `src/agent/main_agent.py` — `build_virtual_backend()` 三路路由；`OwnerScopedStoreBackend.resolve()` 是越权判定的唯一入口。T11/T12 追加：`build_main_agent()`（装配 + 工具分流 + `interrupt_on` 汇总）、`guard_write_tools()`（同步路径的写闸门）。
19. `src/agent/middlewares/skills_sync.py` — `load_manifest()` 校验 frontmatter/slug；`sync()` 的 `full_upload` 三类触发。
20. `src/agent/middlewares/user_skills_restore.py` — `verify_manifest()` 先验后写；`atomic_replace()` 先 staging 再 rename。
21. `src/agent/subagents/loader.py` — **权限判定的唯一入口**：`resolve_tool_patterns()` 按整名匹配、`build_config()` 强制快照一致、只读 scope 二次校验、写工具必须带 `interrupt_on`。
22. `src/agent/subagents/configs/procurement_{analyst,order}.yaml` — 两个子 Agent 的声明；order 的 `interrupt_on` 只给 approve/reject，出现 `edit` 直接启动失败；analyst 另持两个报告工具（T16）。
23. `src/agent/middleware_config.py` — 中间件栈的诚实清单：契约 8 个槽位 + 框架待办中间件，未实现的标 `owner_task` 与 `implemented=false`；`declared_hooks()` 从类上读回真实 hook。
24. `src/agent/memory/prompts.py` + `AGENTS.md` — `DELEGATION_RULES` 是主 Agent 的职责声明；`build_subagent_context()` 决定子 Agent 只收到任务与偏好而非整段历史。
25. `src/agent/tools/hitl_tools.py` — **第一层人工介入**：`missing_fields()` 是"缺什么"的唯一定义；`request_order_info()` 循环中断直到字段齐全，绝不猜测。
26. `src/agent/approval/models.py` — `PendingAction` 与状态机；`freeze_payload()`/`canonical_bytes()` 保证「用户批准的字节」与「网关发送的字节」同一来源。
27. `src/agent/approval/store.py` — **并发保证在查询里**：`transition(expect=..., target=...)` 的条件更新是「双击只成功一次」和「只有一个执行者」的实现，不是策略描述。
28. `src/agent/approval/service.py` — `record()` 冻结动作、`_decide()` 幂等决策、`authorize()` 校验 hash 并签发授权、`verify_internal()` 供网关复核。
29. `src/agent/approval/middleware.py` — **第二层人工介入**：`awrap_tool_call` 在写工具执行前拦截，未授权直接返回拒绝信封，已授权才带票据走网关。
30. `src/api_view/internal/approval.py` — 内部校验端点，独立 service token，只回 operation_id 与状态。
31. `scripts/smoke_agent.py` — 真实模型的端到端只读烟测（非 gate），用回调捕获**子代理内部**的工具调用。
32. `src/api_view/stream_adapter.py` — **浏览器看到什么全在这里决定**：`consume()` 归一化 v2 part，`text_of()` 处理块列表，`describe_interrupt()` 用结构而非文字区分两层中断，`finish()` 保证每 run 恰好一个 `done` 且不把"没有更多 token"当成功。
33. `src/api_view/run_registry.py` — run 的所有权：`open()` 拒绝同 thread 第二个 run，`RunHandle.drain()` 是唯一消费者入口，`shutdown()` 会等活跃 run 收尾而不是直接丢。
34. `src/api_view/api/chat.py` — `_run_turn()` 是唯一接触框架迭代器的地方；`_message_seq()` 决定展示顺序（按 run 位置，不是按角色）；写入 `done` 前先落库并释放 run。
35. `src/api_view/api/history.py` — `delete_thread` 只删会话与 checkpoint；`_purge_checkpoints()` 用安装版 checkpointer 自己的 API。（T16 删掉了这里的占位产件路由。）
36. `src/api_view/api/deps.py` — `require_owner()` 从服务端 Cookie 取身份，不从 body 或路径取；`WebContext` 是路由能看到的全部依赖。T16 追加 `ArtifactService`，并修好了从未生效的 `X-Demo-User` 头（见偏离 15）。
37. `src/api_view/web_main.py` — `create_app()`：`owns_resources` 决定谁负责关 Mongo（调用方给的 context 由调用方关，否则会测试间互相踩）。
38. `frontend/src/api/sse.ts` — 客户端分帧。`SseParser.push()` 跨 chunk 保留缓冲；`streamSse()` 用流式 `TextDecoder` 处理被切断的多字节字符；注释行是心跳。
39. `frontend/src/state/chat.ts` — UI 状态机。`applyDone()` 是唯一能终止 run 的入口；`connectionEndedWithoutDone()` 把断线记为失败而不是完成；`resetForAccountSwitch()` 是换账号时必须清干净的全部状态。
40. `frontend/src/markdown.ts` — 先转义再格式化，所以 `v-html` 是安全的；`safeUrl()` 只放行 http(s)。
41. `frontend/src/App.vue` — `consume()` 消费整流并在没有 `done` 时调 `connectionEndedWithoutDone()`；`reconcile()` 是断线/冲突后**唯一**的恢复动作（回查状态，绝不盲重发）。
42. `tests/acceptance/test_t14.py` — `read_frames()` 按字节手工分帧，验证的正是浏览器会收到的东西；构建产物断言证明打包出来的是真实界面而不是占位页。
43. `src/agent/memory/preferences.py` — **封闭 schema**：`PREFERENCE_SPECS` 是四个显式偏好的唯一定义（含允许值）；`UserPreferences` 把显式偏好与自动历史放在**两个独立字典**里，`record_*` 系列根本无法触及显式偏好。`render_markdown()` 是派生的，不是存储的第二份真相。
44. `src/agent/middlewares/context_injection.py` — `_inject()` 每次模型调用**重建**偏好块（图可缓存，偏好属于单个 owner）；`merge_system_prompt()` 替换而非堆叠。`evaluate_run()` 是"这轮算不算成功采购"的唯一定义。
45. `src/agent/middlewares/memory_update.py` — `extract_supplier_ids()` 只从**成功结果**里取 `supplier_id`（参数是模型要的，结果是 ERP 确认的）。
46. `src/agent/middlewares/tools_summarization.py` — `_maybe_offload()` 在**写文件失败时保留原文**（指向一个读不到的路径比长消息更糟）；`archive_thread_history()` 必须在任何摘要之前调用；`compaction_preserves()` 是"压缩有没有弄丢 todos/中断"的显式检查；`RunBudget.__post_init__` 用注入的时钟取起始点。
47. `src/agent/middlewares/sandbox_breaker.py` — `classify()` 是三分类的唯一入口：只有 `SANDBOX_*` 进熔断计数，业务码另行计数。`allows()` 在半开状态**消耗**一次探测额度，所以并发调用不会一起打过去。
48. `src/agent/tools/web_search.py` — `describe_error()` 把 401/429+1113/403 翻译成用户该做什么；`_parse()` 把「缺 `search_result` 字段」当作接口变更而不是「没搜到」；`MAX_COUNT` 是我方上限，因为**智谱忽略 `count`**。
49. `src/agent/tools/chart_generator.py` — **按图表族构造数据的地方**：`CATEGORY_TYPES` 用 `{category,value}`、`TIME_TYPES` 用 `{time,value}`。远近端 schema 不表达这个区别，所以它只能活在这里。`decode_asset()` 按 base64 解码后的 magic 判断格式，不信任 `mimeType`。T16 追加 `on_asset` 挂钩（见下）。
50. `src/agent/tools/__init__.py` — `LOCAL_TOOL_NAMES` 是 Agent 层工具的唯一登记处；`REPORT_TOOL_NAMES`（T16 新增：`chart_generator`、`download_sandbox_file`）由分析子代理声明，因此被 T11 的分流规则从主 Agent 摘除，主 Agent 只留 `web_search`。
51. `src/skills/procurement/procurement-analysis/scripts/build_report.py` — **所有金额在这里算**：`load_quote_file()` 拒绝缺 `source_url` 的报价文件，`build_report()` 是同物料同币种取最低价（同价按 `supplier_id` 升序）的唯一定义，缺报价只进 `warnings` 并从合计排除。`main()` 把汇总以 JSON 打到 stdout。
52. `src/skills/procurement/web-content-fetcher/scripts/fetch_page.py` — `MarkdownExtractor` 是 HTML→Markdown 的全部；`convert()` 把来源行写在文件开头（**由发起请求的进程写，不是模型补写**），截断时在行边界切并留标记。
53. `src/skills/procurement/web-scraper/scripts/fetch_quotes.py` — `QuotePageParser` 只认 `data-*` 属性，所以表格版与列表版共用一条解析路径；退出码 3/4/5 区分「连不上」「结构变了」「没价格」。
54. `src/agent/artifacts/models.py` — `safe_filename()` 是文件名净化的唯一入口（防 header 注入与路径穿越）；`public()` 决定客户端能看到什么，**不含宿主路径**。
55. `src/agent/artifacts/store.py` — **先写字节再写行**：进程死在两步之间时留下的是没人引用的 blob（垃圾，可回收），反过来会留下一个指向不存在文件的登记行。`has_content()` 用投影查询，不为一个 yes/no 拉整张图。
56. `src/agent/artifacts/service.py` — `register()` 是产件的唯一创建点（摘要与归属在这里定，不在两个生产者里各算一遍）；`resolve_scope()` 是 owner/thread 的唯一定义；`chart_hook()` 是图表的产件入口。
57. `src/agent/tools/download_sandbox_file.py` — **路径边界在这里**：`check_path()` 先 `posixpath.normpath` 再比前缀，所以 `/workspace/../etc/passwd` 会被拒。工具用恰好 `RunnableConfig` 的注解拿到注入的 owner，`config` 因此不出现在 schema 里。
58. `src/api_view/api/artifacts.py` — 下载路由：元数据带 `download_ready`，内容带 `X-Content-Sha256` 与 RFC 5987 的 `filename*`；登记行在而字节没了返回 **410**，不是 200+空文件。
59. `src/agent/skills/pipeline.py` — **技能发布的唯一流水线**：`collect_package()` 是「已批准来源」allow-list 的落点（按主机名，不是 `host:port`）；`_expand_archive()` 先归一化再判越界，并拒符号链接；`validate_bundle()` 要求 frontmatter 的 `name` 与请求的 slug 一致；`run_smoke()` 双向执行并记录；`_persist()` 写入后**逐文件读回核对 sha256**，对不上就不分配；`_install_execution_copy()` 才把副本放进 `/skills/users/{scope}/{slug}/`。
60. `src/agent/skills/store.py` — **文件放 Store、版本行与指针放 Mongo**：`assign()` 是发布指针的条件更新（`expected_revision=None` 走 insert + 唯一索引，不是 upsert）；`content_digest()` 让「内容相同返回原版本」成为查表；`serialise_manifest()` 是 manifest 的**唯一**序列化，保证记录在版本行上的摘要就是写下去的那份字节。
61. `src/agent/tools/assign_skill.py` — `publish_skill()` 是核心（不依赖框架），`repair_hook` 是中断的插槽，所以修复循环可以直接测；`SCOPE_INTERRUPT` 在缺 scope 时提问，绝不默认全员发布。
62. `src/agent/middlewares/user_skills_restore.py` — T17 追加：`assignments_revision()`（从分配**派生**，没有第二个计数器）、`USER_SKILLS_REVISION_MARKER`（在容器里，所以重建容器必然触发全量恢复）、`StoreAssignmentReader(pointers=...)` 读真实指针。
63. `src/agent/async_tasks/service.py` — **SDK 适配的唯一实现**：`SdkProtocolClient` 包 `langgraph_sdk`，不发明任何协议；`_refresh()` 把 404 与网络失败分开（前者 `lost`，后者保留最后状态，**都不能变成 completed**）；`update()` 用 `handling="followup_run"` 如实记录"没有改写原输入"。`ASYNC_ANALYST_GRAPH_ID` 是图 id 的唯一定义。
64. `src/agent/async_tasks/store.py` — 本地映射与**状态机**：`TERMINAL` 与 `transition(expect=...)` 是「取消是终态、后续轮询不能复活」的实现；`(owner, request_id)` 唯一索引是「双击不分析两次」的实现；`AsyncStatus.LOST` 存在的理由写在类注释里。
65. `src/api_view/api/async_tasks.py` — 契约固定的四个端点 + 列表。`_require_parent()` 让「往别人的会话里塞任务」变成 404；503/404/409 分别对应服务不可达、没有这个任务、任务已终结。
66. `src/api_view/internal/sandbox.py` — **跨进程沙箱面**：`OPERATIONS` 是白名单（write/execute/download），不是 manager 透传；`check_path()` 先归一化再比前缀；下载路径**整批先校验再动手**，越界是 400 而不是逐文件的 `file_not_found`（后者会让越界看起来像普通缺文件）。
67. `src/api_view/internal/analysis.py` — 后台服务的只读数据入口：`READ_ONLY_TOOLS` 白名单、`WRITE_TOOLS` 显式列出以便被拒；`_bounded_arguments()` 只放行分页与查询键，`x-actor-id` 在这里从可信 owner 注入，请求里带的一律丢弃。
68. `infra/agent-protocol/analyst_graph.py` — 独立 venv 里的只读分析图：`READ_TOOLS`/`WRITE_TOOLS` 是它的完整工具面（后者为空），`build()` 是 `langgraph.json` 引用的入口。它只依赖 langgraph/langchain_core/httpx，因为那个环境里没有模型客户端。
69. `src/agent/subagents/async_analyst.py` — `build_async_analyst_spec()` 产出框架的 `AsyncSubAgent` 形状；`graph_id` 就是"这是异步子代理"的判别式，五个工具由框架生成。
70. `frontend/src/state/async_tasks.ts` + `components/AsyncTasks.vue` — **前端不自己判定成功**：`isTerminal()` 信任服务端 `terminal` 标志；轮询失败时**不修改**任务状态并显示"不代表已完成"；`lost` 与 `failed` 是两条不同的文案。
71. `tests/acceptance/test_t21.py` — 跨模块故障：`_grant_for()` 用**与网关同一个 canonical builder** 造票据（所以"票据绑定被批准的字节"是可断言的），`_operate()` 走真实 MCP 客户端；并发重试与"取消后并发轮询"各跑三次并比较结果集。
72. `tests/integration/test_isolation.py` — 隔离的**分层**断言：`assert_key_allowed`（键命名空间）、`OwnerScopedStoreBackend`（虚拟挂载）、`memory_key`/`NamespaceViolation`（owner 形状）、`mcp_server.context`（调用者身份）。分层是为了让回归指向具体哪一层破了。
73. `src/mcp_server/context.py` — **没有全局"当前用户"**：`caller_scope()` 是 `ContextVar` + try/finally reset，`current_caller()` 未绑定时抛 `MissingCallerIdentity`。T21 的审计把它当作正确样板钉住（并发四路、异常路径、未绑定三种情况）。
74. `scripts/coverage_report.py` — **覆盖报告的唯一生成器**：`IMPLEMENTATION_FILES` 与 `SCENARIO_OWNERS` 是两张人工整理、可审阅的映射表（实现文件不可从目录名派生，场景归属不可从散文猜——`V11-V16` 这种区间写法会让正则漏掉中间四项）；`build()` 会**拒绝打印任何不存在的路径**，并对未知任务 id 直接报错。待验收需求的交付物按设计不校验存在性。
75. `scripts/gate.py` — `assert_no_self_invocation()`（`--all` 的防自递归守卫，症状是挂起所以值得显式拒绝）与 `all_mode_targets()`（从 manifest 取目标，**完全不碰 state.json**）。`--all` 本身是既有实现，T22 把它从"事实"变成"被守卫约束且被断言的事实"。
76. `tests/acceptance/test_t22.py` — 审计套件：对着 **receipt 内容**断言（零 skip、零零收集、达到声明的最小例数、记录了 source_revision），而不是看任务状态；`_skip_sites()` 用 AST 找真正的装饰器与调用（T00 的套件里有 `"@pytest.mark.skip(...)"` 字符串作为测试数据，正则会被它骗到）。

## 已实测的关键行为

- 预警集合 P001/P003/P004、建议量 42/15/30 与 `expected-v1.json` 一致；换种子后预警随之变化；同一库重启不重置。
- 订单 50×25.50 = 1275.00，改 60 件 = 1530.00 且 version=2，库存仍为 8；两线程同 key 只产生一单。
- MCP 工具面恰好八个，schema 不含 actor/token/operation_id；无授权写被拒，改参数/换用户/过期/篡改签名均被拒。
- 报价站最低价合计 2553.00；下载的 ZIP 解压后真实运行脚本得 1533.00；宿主机与 Linux 容器产出的 ZIP 哈希一致。
- Mongo checkpoint 由独立进程读回；display message 重复投递不重复；同 request_id 不同正文冲突。
- 沙箱内真实执行命令、读写/上传下载文件；**超时明确报 124**；宿主哨兵文件不变；`/var/run/docker.sock` 不可见；容器内可抓报价站。
- 池：预热 1 个并在认领后补充；同一用户跨 thread 复用同一 proxy 对象；不同用户不同容器且互相读不到文件；预热认领在并发下不重复。
- 恢复：容器被 `docker rm -f` 之后 health 变 False，`recover` 生成新容器且 **proxy 对象身份不变**、generation +1、规则文件重新上传；3 线程同时发现故障只重建一次。
- 崩溃/重启：`detach()` 后新 manager 通过 Mongo 登记重连同一沙箱；遗留的预热容器由 `cleanup_orphans()` 按项目标记回收。
- 虚拟三路：写 `/memories/` 与 `/persisted-skills/` 落在 Store，写 `/workspace/` 落在容器；容器内 `ls /memories` 报 No such file。
- 作用域：owner B 看不到 owner A 的技能条目，显式引用 `users/demo-a/...` 得到 `permission_denied`。
- 子 Agent 权限：分析 Agent 恰好六个 ERP 读工具；`part` 这类子串**匹配不到任何工具**（按整名匹配）；`order_*` 匹配 3 个订单工具。
- 规划：`write_todos` 结果真实落在 `state["todos"]`；**不加 `TodoListMiddleware` 时该工具根本不存在**。
- 委派隔离：子 Agent 首轮上下文恒为 2 条（system + task），父 Agent 的无关文本不出现在其中。
- 真实只读查询：子 Agent 内 `inventory_warning` 的真实返回与直连网关结果**逐条一致**（P001/P003/P004）。
- 真实模型烟测（`deepseek-chat`）：3~4 条 todo + `task` 委派 + 子代理内 6 次 MCP 调用，未下单。
- 补充层：缺 `unit_price` 被点名报出；"25.500"、`P0X1`、`quantity=0` 都算缺失；补充不完整会**再次中断**；散文回答不会被解析成字段。
- 审批层：approve 前**零写**、reject 后**零写**；批准后参数变更报 `PARAMETERS_CHANGED`；同一批准重复提交**只产生一单**（ERP 操作 ID 对账：两次返回同一 `order_id`）。
- 决策幂等：同一 `request_id` 重放返回原决策且 operation_id 不变；不同 `request_id`（第二个标签页）报 `ALREADY_DECIDED`；已 reject 的动作不能再 approve。
- 票据防护：伪造签名报 `INVALID_GRANT`；把 demo-a 的票据拿去用 demo-b 身份调用报 `GRANT_MISMATCH` 且两边订单数都不变。
- 内部端点：无 token/错 token 一律 403；hash 不符 409 `GRANT_MISMATCH`；中断不存在 404；缺字段 400。响应只含 operation_id 与状态。
- 不泄漏：写给模型的拒绝消息里**不含** operation_id、票据前缀或 grant secret。
- 持久性：换一个 service 实例（同一 Mongo）仍能读到已批准的动作与冻结字节。
- SSE：真实 `create_deep_agent` 图上跑通 `stream_mode=["messages","values"] + subgraphs + v2`，事件顺序为 `run_started → token/tool_*/todos → done`，`done` 恰好一次，`seq` 严格递增。
- 分片：工具参数按 tool_call_id 拼接后再解析；半个 JSON 不报错（`tool_start` + 一次 `tool_args`）。
- 归属：`task` 调用参数拼完后，`tools:<task-id>` 命名空间被解析成 `procurement-analyst`；未知 task 报 `sub-agent` 而不是拿 id 冒充名字。
- 终态：出错时 `error` 在 `done` 之前且 `done.status=failed`；中断时 `done.status=interrupted`；无终止信号一律 `failed`，从不报 `completed`。
- 幂等：同 `request_id` 重发返回原 run（不产生事件流、不启动新 run）；同 ID 不同正文 409；同 thread 第二个 run 409。
- 隔离：`demo-b` 对 `demo-a` 的 thread 在 state/history/delete/cancel 全部 404，且自己的 history 为空。
- 删除：`deleted` 给出四类计数（threads/display_messages/runs/checkpoints），`kept` 明确列出技能、偏好、订单。
- 历史顺序：4 轮对话的展示顺序是 user/assistant 交替，不是"所有提问在前"。
- **T16 抓取**：S001 表格版与 S002 列表版各按自己的 `render_style` 解析；每个报价带 `source_url` 与 `quoted_at`；无价格页退出码 5 且 stderr 指名 URL 与物料，**不留下半成品文件**；连不上退出码 3。
- **T16 比价**：总量 **2553.00**，三行逐项匹配（P001/S002/24.00/1008.00、P003/S001/68.00/1020.00、P004/S002/17.50/525.00）；P001 取 S002 的 24.00 而非 S001 的 25.50；用 `Decimal` 从报告自身重算得到同一数字。
- **T16 缺口**：预警里有 P005 而无任何页面报价时，它只出现在 warnings 与 Markdown 的缺口一节，**不出现在 CSV、也不计入合计**；报缺 `source_url` 的报价文件直接退出码 4；同一页对同一物料报两个不同价也退出码 4（不取平均、不任选）。
- **T16 排除**：把一份真页面改标成 S003 并把价格**减半**后，比价结果与合计完全不变，且 S003 被排除这件事写在报告里；从未被抓取的排除项也会被记录。
- **T16 沙箱内**：报告在真容器里抓取并产出，报告里的来源地址是 `host.docker.internal:<port>`（**只有容器内可达**），这本身就是从 HTTP 抓取的证据。
- **T16 产件**：沙箱文件经工具转存后下载，`sha256(下载内容)` 与登记值一致，`X-Content-Sha256` 头也一致，且沙箱里原文件与下载内容同哈希；文件名 `补货比价报告.md` 经 `filename*=UTF-8''` 正确传输。
- **T16 缺产物**：删掉 blob 后元数据仍 200 但 `download_ready=false`，下载路由返回 **410**；跨用户在元数据与内容两个路由都是 404，列表按 thread 隔离为 0。
- **T16 越界**：`/workspace/../etc/passwd`、`/workspace-other/x`、`/etc/passwd` 全部 `path_not_allowed`；相对路径 `invalid_path`；文件不存在 `file_not_found` 且**不登记任何产件**；没有作用域时 `SCOPE_MISSING`。
- **T16 图表**：真图经 `on_asset` 登记为产件，下载后是 `\x89PNG` 开头且 hash 与响应里的一致；没有作用域时图**照常返回**但不登记（不因为拒绝归档而打断图表本身）。
- **T17 发布**：技能包从资源站下载后，在沙箱内真实执行 `summarise.py` 得 **1533.00**；同一技能从 `generated`（沙箱内生成目录）与 `package`（压缩包）两条来源都能发布；发布后 `/skills/users/main/reorder-cost-summary/` 里 `SKILL.md`/`examples`/`scripts` 齐全，就地重跑仍得 1533.00。
- **T17 冒烟双向**：示例输入退出码 0（stdout 即 `1533.00`），坏输入退出码 2（`cannot read input: Expecting value`）——**只测成功路径不算测过**。
- **T17 去重与版本**：内容不变重复发布返回**原版本**且版本行数仍为 1；内容变化新建版本（1.0.1）且旧版本保留。
- **T17 拒绝**：`../../escape.py`、绝对路径、ZIP 符号链接、超大包、超多文件、无 frontmatter、缺 description、坏 slug、frontmatter 名与 slug 不一致、声明的入口不存在、未批准主机、坏 ZIP、未知 source_type —— 全部各有独立错误码，且**不产生版本、不产生分配**。
- **T17 失败不发布**：冒烟一直失败时正好给两次修复机会、留下 3 份记录（初始 + 两次），最终 `SMOKE_FAILED`，`/skills/users/main/<slug>` **不存在**；修复一次后成功发布的记录里 `repairs=1`。
- **T17 半写**：从 Store 删掉一个被 manifest 声明的文件后，恢复报 `failed` 且**沙箱里原有的副本一字未改**；只有全部成功才写 revision marker（否则下次会跳过这次没写成的文件）。
- **T17 指针条件更新**：用过期 revision 更新返回 None；`expected_revision=None` 在指针已存在时也返回 None（第一次发布用 insert + 唯一索引，不是 upsert）。
- **T17 恢复短路**：同 generation 第二次恢复返回 `up_to_date` 且 revision 与容器内 marker 一致；删掉 marker 与 `/skills/users` 后（等价于容器重建）下一次强制全量恢复。
- **T17 作用域**：`None`/`""`/`"   "` 一律 `SCOPE_REQUIRED`；`everyone`/`all`/`*`/`analyst`/`Main` 一律 `SCOPE_UNKNOWN`；三个合法 scope 正常通过。
- **T17 隔离**：owner B 的分配扫描为空、`restored == []`；撤回一个 scope 的分配不影响另一个。**注意**：harness 的沙箱是两个 owner 共用一个，所以"容器里没有那个文件"在这里不成立，隔离是在 Store 键空间上断言的（容器级隔离由 T08/T09 用各自沙箱钉住）。
- **T20 真后台**：任务拿到的是**真实 Protocol thread/run id**（UUID，且能用 SDK `runs.get` 读回同一条 run）；服务是独立进程、独立 venv、绑定 8123；`langgraph.json` 现在注册两个图（`echo` 与 `procurement-analyst`），都按 `graph_id` 发现。
- **T20 状态跟随**：`queued → completed` 与 Protocol 的 `success` 一致；契约的状态词表之外不出现别的东西。
- **T20 幂等**：同 `request_id` 重放返回原任务且**不再新建 Protocol thread**（对比 thread 数量）。
- **T20 归属**：owner B 对 owner A 的任务在 status/cancel/update 全是 404；列表按父会话隔离；往别人的父会话里启动任务也是 404（因为那等于在确认该会话存在）。
- **T20 取消是终态**：取消后等 1 秒再查仍是 `cancelled`，不会被后来的轮询复活。
- **T20 更新**：同一 async thread 上起**后续 run**，`run_id` 变化、状态切到新 run，并记录 `handling="followup_run"` 与「SDK 不支持改写已运行的输入」；同 `request_id` 重放 `accepted=false` 且不新增记录；对已终结的任务返回 409。
- **T20 失败可见**：启动时服务不可达 → 503 且**不留下任务**；轮询时服务不认识这个 run → `lost`（不是 `completed`）；轮询时网络失败 → 保留最后已知状态并记下原因。
- **T20 只读是两处强制**：分析图声明的 `WRITE_TOOLS == ()`；主进程的 `/internal/analysis/read` 对 `order_create` 独立返回 403，对未知工具 400。
- **T20 跨进程沙箱**：后台进程经 `/internal/sandbox/operations` 在**同一个用户租约**里执行命令（`uname -s` 返回容器自己的 Linux）、写文件、读回，字节一致；越界路径（`/etc/passwd`、`/workspace/../etc/passwd`、相对路径、`/workspace-other/x`）一律 400；未知操作 400；无 token/错 token 一律 403。
- **T20 前端**：56 个 Vitest 用例通过（新增 10 个），其中钉住"轮询失败不改状态"、"`lost` 与 `failed` 文案不同"、"未终结才显示取消按钮"、"`terminal` 标志优先于状态词"。
- **T21 写幂等**：同 `operation_id` 重试取回**同一张订单**；同一 `operation_id` 换 owner 得到**两张不同订单**（账本按 owner 记账，作用域过宽会让一个人的重试消掉另一个人的单）；4 线程同 key 并发、**重复三轮**每轮都只产生 1 张订单。
- **T21 票据绑定**：批准 50 件后把参数改成 9999 件会被拒——票据绑的是与网关同一个 canonical builder 算出的字节摘要。
- **T21 两标签页**：并发 approve 恰好一个成功、一个拿到拒绝；中断终态为 approved。
- **T21 技能存活**：删掉 `/skills/users` 与 revision marker（等价于容器重建）后从 Store 恢复，且恢复出来的脚本**真的跑出 1533.00**；删掉被 manifest 声明的文件后 `verify_manifest` 抛错且版本不可分配；恶意 ZIP 不产生任何版本。
- **T21 跨进程可读**：另一个 Python 解释器读到同一份技能版本行、assignment 与文件字节，且 `content_digest` 与原值一致。
- **T21 隔离分层**：键命名空间（`assert_key_allowed`）、虚拟挂载（owner 作用域）、`grep` 不跨 owner、两个 owner 并发写同一键不串、`/workspace-other/x` 不被 `/skills` 误放行。
- **T21 共享状态审计**：`src/` 下**没有**模块级可变容器；MCP 身份四路并发互不干扰；异常路径也会 reset；未绑定时抛错而不是默认成某人。
- **T21 故障复现稳定性**：并发写重试与"取消后并发轮询"各跑 **3 次**，每轮结果集完全一致（不靠重试消除失败样本）。
- **T22 证据级回归**：26 个 done 任务的 receipt 全部 `passed`，**零 skip、零零收集**，每个测试类 check 都达到自己声明的最小例数，且都记录了 `source_revision`。这些是对着 **receipt 内容**断言的，不是看任务状态。
- **T22 覆盖追踪**：R01-R28 与 V01-V22 全部出现在报告里；每个 R 的实现文件与测试、每个 V 的归属任务都被逐个校验存在。R27/R28 明确标为**待验收**且该行不含「通过」。
- **T22 gate --all**：`assert_no_self_invocation()` 对真实清单通过；喂一个把 argv 改成 `python scripts/gate.py T00` 的篡改清单会**抛错拒绝**；`--all` 分支的源码里没有任何读取 state.json 的调用。
- **T22 审计的两次自我修正**：第一版用正则找 skip，被 T00 里那段**字符串数据**骗到；第二版用 AST 修好后，又在"固定演示回复"上被 `scanned` 含 `canned` 骗到；补词边界后，第三次它**抓到了自己的源码**（定义了模式的文件必然含有该模式），最终加了一条显式且窄的自我排除。

## 重要偏离与纠错（需用户知晓）

1. **OpenSandbox 客户端换包**：T00 锁定的 `opensandbox-sdk` 与本服务端完全不兼容（认证头、生命周期路径、文件/命令端点三处都不同，且无可用版本组合）。T08 已改正为 **`opensandbox` 0.1.16**，并同步更新 `pyproject.toml`、T00 的锁定导入断言与 `docs/runtime/versions.md`。
2. ~~新增错误码 `INACTIVE_PART`（422）~~ **已按用户决定并入 `UNSUPPORTED_PART`**（2026-09-16）：停用物料与「无供货关系」对外共用一个错误码，消息仍区分两种原因。
3. **Mongo Store 包名**：`langgraph-store-mongodb` 0.4.0，不在 `langchain-mongodb` 内（T00 已核实）。
4. **沙箱控制服务无 api_key**：上游 SDK/服务端认证头不一致所致，仅因绑定 127.0.0.1 且显式 `OPENSANDBOX_INSECURE_SERVER=YES` 才可接受。详 `infra/sandbox/README.md`。
5. **本机环境适配**（非方案变更）：`JAVA_HOME` 重新解析、`.venv` 用具体解释器创建、pytest 临时目录 symlink 容忍、`./mvnw` → `mvnw.cmd`。详 `docs/runtime/versions.md`。
6. **主 Agent 不持有 ERP 工具**（T11，**设计选择**）：架构写的是「主 Agent 管理任务清单、分派、汇总」，但只写进提示词**不足以**约束模型——真实 `deepseek-chat` 会自己查完直接回答，不写 todo 也不委派。已改为结构性约束：**子 Agent 声明的工具自动从主 Agent 摘除**。若希望主 Agent 能直接查询，需显式传 `delegated_tools` 覆盖。
7. **待办清单必须自行装配**：`create_deep_agent` 0.7.14 的基座中间件**不含** `TodoListMiddleware`。
8. **审批绑定到 interrupt 而非 tool_call_id**（T12，**被框架形状推翻的假设**）：框架的 HITL 中断**不携带 `tool_call_id`**（只有 name/args/description），且 `interrupt_id` 只在 thread 内唯一。改为 `(owner_user_id, thread_id, interrupt_id)` 唯一，恢复运行时由 API 注入 `approved_interrupt_id`。这也保住了「同一 thread 内两笔内容相同的独立采购必须可区分」——按 payload hash 匹配做不到。
9. **修正 T07 遗留的错误索引**：`pending_actions` 上的 `uk_pending_actions_interrupt_call (interrupt_id, tool_call_id)` 在真实形状下让**每一条**待审动作互相冲突。已改为 `uk_pending_actions_interrupt`，并新增 `drop_undeclared_application_indexes()` 清理旧库中的过期索引（唯一索引不匹配数据模型时不只是冗余，它会**拒绝合法写入**）。
10. **写操作只能走异步路径**：`WriteApprovalMiddleware.wrap_tool_call`（同步）一律拒绝，因为签发的写需要 HTTP 调用而本项目没有同步 MCP 客户端。这是有意为之，不是遗漏。
11. **SSE 的 `source` 依赖 `task` 调用的参数**（T13）：框架给的 `ns` 是 `tools:<task-id>`，不是子 Agent 名。名字要从 `task` 调用的 JSON 参数里取，而参数是**单字符分片**——所以在参数拼完之前，子代理的事件只能归到 `sub-agent`。若 UI 需要更早的归属，需要另找来源，不能靠猜文本。
12. **新增 `src/api_view/run_registry.py` 与 `src/api_view/api/deps.py`**（T13，超出任务文件边界的两处小补充）：前者是"run 不能是悬空 task"的落点，后者是路由共享的身份依赖；都不适合塞进已列出的文件里。
13. **前端单测需要 Node ≥ 20.19，本机默认是 20.18.1**（T14，**环境缺口，需你知晓**）：差别只在 `require(esm)` 能力，而 jsdom 依赖链依赖它，所以在默认 Node 下 vitest **一个用例都跑不起来**（`npm run build` 不受影响）。已用本机自带的 `~/.workbuddy/binaries/node/versions/22.22.2/node.exe` 跑通 46 个用例。`package.json` 的 `engines` 早已声明 `^20.19.0 || >=22.12.0`，所以这不是代码问题，建议把 PATH 上的 node 升到合规版本。
14. **T14 未引入 Playwright**（T14，**范围取舍**）：任务正文提到用 Playwright 驱动浏览器并出 1440×900 / 390×844 截图，但 `tasks.json` 里 T14 的 required checks 只有 pytest acceptance 与 `npm run build`，且仓库的 `package-lock.json` 里没有 Playwright。考虑到浏览器二进制体积与本机此前出现过的内存耗尽，改为：DOM 级行为断言放在 Vitest（`@vue/test-utils` + jsdom，46 例，覆盖按钮禁用、审批面板、错误提示、Markdown 转义），接口级端到端放在 pytest（19 例，对真实后端逐字节分帧）。**没有产出截图证据**。若你要求截图，需要先把 Playwright 加进依赖并下载浏览器，我可以补。
15. **修好了从未生效的 `X-Demo-User` 头**（T16，**既有 bug，非本任务引入**）：`require_owner()` 把 `demo_user` 声明成 FastAPI 的 `Header(...)` 参数，但它是被路由体**当普通函数调用**的（`owner = require_owner(request)`），不是 `Depends`，所以参数从不注入——拿到的是 `Header(None)` 对象本身，truthy，于是无 cookie 的请求报 `unknown demo user Header(None)` 而走不到"先建会话"的提示。**此前没有任何测试发过这个头**（都用 `POST /api/demo/session` 种 cookie），所以这条路径一直坏着。已改为从 `request.headers` 读。
16. **`chart_generator` 归分析子代理**（T16，**工具归属变更**）：报告与图表是同一份交付物，图表数据由 `build_report.py` 产出，让主 Agent 再算一遍等于把数字在 prose 里传一遍。因此 `REPORT_TOOL_NAMES = (chart_generator, download_sandbox_file)` 由 `procurement-analyst` 声明，按 T11 的分流规则从主 Agent 摘除，主 Agent 现在只持 `web_search`。T11 里用 `chart_generator` 当"无人声明所以留在主 Agent"的例子已改用合成工具名。
17. **产件只导出 `/workspace` 下的文件**（T16，**安全边界**）：`download_sandbox_file` 只接受 `/workspace` 前缀，且**先归一化再判前缀**。没有这条边界，一个被说服"总结一下凭据文件"的 Agent 就有了带下载链接的可用外泄路径。
18. **`require_owner` 的注解写法有硬约束**（T16，**框架行为，易踩**）：工具的 `config` 参数注解必须**恰好是 `RunnableConfig`**。写成 `RunnableConfig | None = None` 时框架**不注入**，`config` 会出现在工具 schema 里，等于要求模型提供 owner。`download_sandbox_file` 里已注明原因与实测依据。
19. **T16 未接 SSE 的 `artifact` 事件**（T16，**已知缺口**）：`stream_adapter.artifact()` 仍然没有调用点。产件信息目前通过工具返回值（`artifact_id` / `download_url`）与子代理的 `artifact_ids` 传给模型，由它写进回答。若 UI 想在生成过程中就显示下载链接，需要把工具结果流接进 adapter。

20. **发布指针从 Store 移到 MongoDB**（T17，**被「条件更新」这条要求推着走**）：`storage-sandbox.md` 本来就点名了 `skill_versions`/`skill_assignments` 两个集合，而 T10 的读取器是从 Store 键前缀扫指针的（那时没有并发要求）。T17 需要真正的 compare-and-swap，而 langgraph Store 只有 `put`/`search`，**没有 CAS**——先读后写会通过功能测试却输在并发上。现在指针的权威在 Mongo，`StoreAssignmentReader` 通过可选的 `pointers=` 读它；不传时仍走 Store 扫描，所以 T10 的既有用例不受影响。技能**文件**仍在 Store（契约要求的键形状不变）。
21. **`require_owner` 之外的又一处「注解对了但没人注入」**（T17 复核）：`assign_skill` 与 `download_sandbox_file` 的 `config` 注解必须恰好是 `RunnableConfig`。T16 已记录，这里再次确认——写成 `RunnableConfig | None` 时 `config` 会进入工具 schema，等于让模型提供 owner。
22. **修正 HANDOFF 里一个指向不存在目录的收据路径**（T17，**仅文档，不影响计划状态**）：本表 T18 一行的 receipt 单元格写着 `artifacts/tasks/T18/20260917T174106Z/receipt.json`，该目录**不存在**；已改为真实的 `20260917T084827Z`。核对过全部 20 个 done 任务，只有这一处是错的。
    - **同时更正一条我写错的结论**：我一度记成"`state.json` 里的收据路径是错的、`plan_guard check` 不校验收据是否存在"。两处都**不成立**——错的只有 HANDOFF 的表格文本，`state.json` 一直是对的；而 `plan_guard.receipt_errors()` 对不存在的收据会抛 `FileNotFoundError` 并在 `except (OSError, ...)` 里被捕获成 `invalid receipt`，实测 `plan_guard check` 会失败。是我把自己那个一次性同步脚本的输出方向读反了（它打印的是 `HANDOFF 的旧值 -> state.json 的值`，不是反过来）。**不需要给 plan_guard 加校验，它已经有。**
23. **`assign_skill` 归主 Agent**（T17，**设计选择**）：契约说「创建/下载/分配/清理均通过主 Agent 的 skill-management 能力实施」。一个能发布技能的子代理等于能拓宽自己的工具集。

24. **后台分析图里没有模型**（T20，**由环境决定，需你知晓**）：独立服务环境只有 `langgraph` / `langchain-core` / `httpx`，**没有任何模型客户端**。所以 `analyst_graph.py` 是真实的 LangGraph、真实的 Protocol 调用、真实的只读数据，但"分析"是确定性管线而不是模型推理。要让它真的由模型决定看什么，需要往 `scripts/provision_agent_protocol.py` 的 `SERVICE_REQUIREMENTS` 加 `langchain-openai` 并重跑 provisioning（纯 Python 依赖，与 grpcio 无冲突），**我没有做这一步**，因为那会改动已锁定的独立环境，应该先让你知道。契约要求的是"独立服务 + 正式 SDK + 只读"，这三条都满足。
25. **`/internal/sandbox/operations` 的 `operation_id` 只做透传与日志，没有做去重**（T20，**已知缺口**）：契约在请求字段里列了它。当前后台图的三个操作天然幂等（覆盖写、只读执行、读回），所以没有重复副作用；但一个会重试的调用方需要真正的去重键，那要一张表。
26. **T06 的一条断言曾把"当时缺凭据"写死**（T20 复验时发现并修正）：它要求 `search` 与 `chart` 的 `satisfied is False`，这在写成时是对的，智谱与 ModelScope 凭据配好之后就永远是错的——**一个因为项目推进而失败的测试，量的是日历不是代码**。已改为断言报告结构，并用空环境强制出"未满足时点名变量"的分支。T06 因此从 18 例变 19 例。
27. **T20 新增两个内部端点**（T20，超出任务文件边界的小补充）：`src/api_view/internal/sandbox.py` 与 `analysis.py`。契约点名了 `/internal/sandbox/operations`；`/internal/analysis/read` 是把只读 ERP 访问交给后台进程的最小面，避免它直连网关持有凭据。

28. **T21 没有改任何 `src/` 代码**（T21，**审计结论**）：任务说"只做发现问题的最小修复"，而第 3 步的共享状态审计**没有发现问题**。这不是省略——`src/` 下无模块级可变容器，`mcp_server/context.py` 用的是 `ContextVar` + try/finally reset 且未绑定时抛错，这三点都已被测试钉住（并发四路、异常路径、未绑定）。为避免"看起来没干活"，把结论写在这里。
29. **纠正一条我自己记错的契约**（T21）：我先前的侦察笔记说 `_require_user` 会拒绝 `.`，**实际不会**——它只拒空/空白与含 `/` 的 id。没有据此补一个演示不到的保护（owner 来自服务端固定列表 `DEMO_USERS`，永远不来自请求），而是把测试改成真实契约并写明原因。

30. **T22 没有重跑全部 25 个任务的 gate**（T22，**范围说明**）：T22 的 acceptance 是**审计**——它打开 receipt 逐条核对（状态、跳过数、最小例数、source_revision），而不是把每个 task 再跑一遍。理由有二：单个 gate 全跑一遍要几十分钟，而且"再跑一遍"并不比重开收据更能证明什么。`gate --all` 的入口已经就绪、其性质也已被断言，需要时可以直接跑。
31. **`tests/unit` 从来不存在**（T22，**纠正一处措辞**）：任务步骤写"运行全部 Python unit/contract/integration"，但 "unit" 在本仓库是 **check 模式**而不是目录；实际套件是 `acceptance` / `contract` / `integration`，另有 `tests/compat`（CAP 实验库）与 `tests/plan`（计划校验）。没有为了对齐措辞去建一个空目录，而是在测试里写明这个区别并断言真实布局。

32. **搜索引擎换档**（T23 期间，2026-09-19，**用户要求降本**）：`search_pro_sogou`（0.05 元/次）→ **`search_std`（0.01 元/次）**。`contracts/external.md`、`dependencies.md` 与代码三处**同时**改（后者写明"不暗换引擎"）。实测 6 条真实 query 里 1 条在 std 上拿不到可引用链接，演示 query 已换成实测出链接的那条；`assets.json` 现在记录引擎名。顺带纠正 T15 表里"`count` 不生效"——那是 sogou 的行为，std 认它。详见 `docs/runtime/versions.md` 的「搜索引擎更换」。

33. **T23 修掉三个"看起来能跑"的真缺陷**（2026-09-19，**需你知晓**）：① 记忆键不接受前导斜杠，而框架传的正是带斜杠的键 → 模型按文档写 `/memories/preferences.md` 会让整个运行以 `NAMESPACEVIOLATION` 结束；② 试验判定"通过"时看不见失败的运行 → 有两次崩溃被算成了成功，**这是假通过**；③ 轮次目录会把别的轮次的试验混进来一起算 → 实测出现过一次假失败，同一个机制也能造假通过。三处都已修，细节见「T23 发现并修复的三个真缺陷」。改动前的 24 次轮次保留在 `artifacts/live/r1-prefix-20260919/`。

34. **前端缺 `@types/node`，`npm run build` 在干净环境上根本跑不过**（2026-09-20，T23 完成、复跑 T22 时发现）：`frontend/vite.config.ts` 用 `process.env.VITE_BACKEND` 决定 dev server 的代理目标（**这一处是必需的**：夹具站点绑定动态端口，写死地址会让截图拍到另一个后端），而 `@types/node` **既没装也没写进 `package.json`**，于是 `vue-tsc --noEmit` 报 `TS2591: Cannot find name 'process'`，`npm run build` 退出码 2 —— gate 把它读成 `blocked`。那时 T22 是**失败**的（`acceptance` 2 例 + `frontend-build` blocked）。已加 `@types/node@^22.12.0` 到 devDependencies 并重装（`package-lock.json` 随之更新）。复跑：**T00 / T14 / T22 全部 PASSED**。顺带把 T22 那两条守卫更新到仍然成立的前提：R27 随 T23 完成已转为「通过 | 通过 | not_reviewed」，`PENDING_REQUIREMENTS` 收窄为 `("R28",)` 并改钉 `T24` 的状态——守卫保留，只是换了还没交付的那个需求。

## 未验证 / 阻塞项

- **模型凭据已就绪并已验证**：`.env` 已按 `.env.example` 生成。真实调用返回中文正确应答，provider 回报 `model_name=deepseek-flash`。密钥值未写入任何文档、receipt 或索引。
- **T11 的模型行为只在一个模型上验证过**：结构性摘除工具把失败模式从「静默绕过」降级为「无工具可用」，但不保证换模型后行为一致。
- **T12 的两层中断只在脚本化模型上验证过**：真实模型是否会正确调用 `request_order_info`（而不是自己编一个数量）尚未实测；`order_create` 的审批中断是框架行为，与模型无关。
- **T16 的报告流程没有在真实模型上端到端跑过**：脚本层与沙箱层都是实测的（38 例），但"真实模型按 SKILL.md 依次调用三个脚本、再调图表、再转存产件"这条完整链路没有用真实模型验证。脚本分工是结构性的（金额只能来自脚本输出），但模型是否会**照做**尚未实测。
- **T16 的 `--skip-supplier` 依赖调用方传对**：脚本会记录排除项，但不会自己去查哪家供应商已停用——停用信息在 ERP 里，不在报价页上。
- **T17 的修复循环只在代码层验证过**：`publish_skill` 的 `repair_hook` 在测试里是"直接改文件"的桩。真实链路是"工具中断 → 用户答复 → 模型改文件 → 再次调用"，这条在图上没跑过（中断载荷里有 staging 路径与失败记录，足以让模型定位问题）。
- **T17 的并发只验证了条件更新本身**：`assign()` 的 CAS 语义用「过期 revision 返回 None」钉住了，但没有起两个进程真的去抢同一 slug。

## 下一步

**T23（真实模型演示与资产验证）已完成。** 依赖 T22 与 T15，都已完成。gate 只有一个 required check：`acceptance`（≥24 例），**模式是 live**；实测 `tests=41 failed=0 skipped=0`，收据 `artifacts/tasks/T23/20260919T154444Z/receipt.json`。

### T23 已完成的部分

- **Playwright 已进锁定清单**（用户 2026-09-18 决定）：`pyproject.toml` 的两处 dev 清单都加了 `"playwright>=1.63,<2.0"`，`uv lock` 重新解析（140 个包，新增 `playwright 1.63.0` + `pyee 13.0.1`），**T00 复跑通过**（锁定断言 + java/frontend 构建）。浏览器二进制不在包里，需要一次性 `python -m playwright install chromium`；已验证 `1440×900` 与 `390×844` 两个视口都能出图。
- **`tests/live/scenarios.py`** —— D01-D08 的可执行定义：8 个场景、24 次试验（`TRIALS_PER_SCENARIO=3`）、`REQUIRED_SUCCESSES=22`、`PER_SCENARIO_MINIMUM=2`、28 条不变量。合同数字从 `fixtures/expected-v1.json` **交叉核对**而不是重述；`problems()` 是自检，已抓到并修正一处真问题（D08 标了 interactive 却没有决策记录——取消就是决策）。自动化决策的操作者记在 `TEST_ACTOR` 上，满足 demo.md「须明确标测试操作者」。

### T23 已完成：全栈装配跑通（并因此发现两个真缺陷）

**`tests/live/stack.py` 已写好并实测通过**。真模型 `deepseek-chat` → 真 ERP（MCP）→ 真沙箱（`execute`/`read_file`/`download_sandbox_file`）→ `write_todos`，**D01 的答案完全正确**（BRAKE-01 42 / CHAIN-01 15 / SPARK-01 30），trace 里有 `inventory_warning` + `part_query`×3 的真实调用。

关键设计（写在这里免得下一轮重新踩）：

- **`graph_provider` 在事件循环内被调用**（`chat.py::_run_turn`），而装配需要 `MultiServerMCPClient.get_tools()`（coroutine）。所以**两个 owner 的图在 app 启动前就建好**，不要在 provider 里 `asyncio.run`。
- **工具面有两半**：MCP 工具（按 owner 固定 `x-actor-id` 头）+ **本地工具**（`request_order_info`、`chart_generator`、`web_search`、`download_sandbox_file`、`assign_skill`）。只传 MCP 那一半会被子代理加载器拒掉——它拒绝授予目录里没有的工具。
- `build_middlewares()` 返回的是**契约声明的槽位**，其中两个不是中间件：`conversation_summary` 贡献的是一个 `compact_conversation` **工具**，`sandbox_breaker` 贡献的是一个**注册表**。交给 `create_deep_agent` 之前要按 `isinstance(item, AgentMiddleware)` 过滤——框架会对每个元素读 `m.name`。

### 由 T23 发现并修复的两个真缺陷（前 22 个任务都没碰到）

1. **三个自研中间件不是 `AgentMiddleware` 子类**（`SkillsSyncMiddleware`、`UserSkillsRestoreMiddleware`、`MemoryUpdateMiddleware`）。它们按鸭子类型实现 hook，而 `create_deep_agent` 会读 `m.name`（基类上的属性，返回类名）——不继承就 `AttributeError`。T11 只把框架自己的 `TodoListMiddleware` 交给过 `create_deep_agent`，所以这个问题直到 T23 把**完整自研栈**交给它才暴露。已改为继承。
2. **`ToolsSummarizationMiddleware._maybe_offload()` 假定工具结果一定是带 `.content` 的消息**。框架自己的文件系统工具返回 `Command`（状态更新），没有"内容"可转存——直接读到 `AttributeError`，崩在工具节点中间。已改为**类型判断后直通**。找到它的方式正是"跑真实工具集"，脚本化模型与桩工具都碰不到。

两个修复复验通过：**T10 / T11 / T18 / T19 全部 PASSED**（改动了 4 个源文件）。

### T23 已完成：trial runner 跑通（D01 真跑真对账）

**`scripts/demo.py` 已可用**。`python scripts/demo.py --only D01 --trials 1 --round probe` → **D01 #1: ok**（57 秒），对账读的是 ERP 自己的返回（直连网关，不复用模型说的任何东西），三个物料的建议量与 `demo.md` 的 D01 预期逐项一致。

设计要点（免得重新踩）：

- **每次试验先重置该 owner 的状态**（Mongo 各集合 + Store 的 `users/{owner}` 前缀 + `/skills/users`），这是 `demo.md` 「24 次必须彼此独立」的落地。**但不要 `manager.recycle(owner)`** —— 预建图持有那个容器，销毁它会让第一次工具调用撞上连接重置（我第一次就是这么挂的）。
- **对账用增量而不是绝对状态**：ERP 是整轮共享的，D03 要求"某张订单变成 version=2"，跑到第三次时已经有好几张。运行前拍快照、运行后比差异。
- **ERP 调用要用它自己的 fixture**：路径是 `/api/erp/v1/orders`，幂等键头是 **`X-Operation-Id`**，头集合由 `erp.headers(actor=...)` 给出。我第一版按 `/orders` 调，拿到的是 500/内部错误。
- 夹具：`_prepare()` 给 D03 建好那张 50 件的订单（自动化轮次里没有"刚才"）。

### T23 未解决的发现：真实模型不按预期调用订单工具

**这是 T12 的 HANDOFF 早已标记为"未实测"的风险，现在被证实是真的**（原文：「真实模型是否会正确调用 `request_order_info`（而不是自己编一个数量）尚未实测」）。

实测（`artifacts/live/probe*/D02-1`、`D03-1` 的 trace 可复核）：

| 现象 | 证据 |
|---|---|
| D02：子代理**真的干了活**（`supplier_query`、`part_by_supplier`、沙箱读写、`download_sandbox_file`），但**从没调用 `request_order_info` 或 `order_create`** | 22 次工具调用里 `task` 之后没有任何订单工具；0 中断；回答是散文式的"暂时无法下单，请确认" |
| D03：找到了订单（`order_search_details`），但**没调 `order_update`** | 同样 0 中断，用文字问"请确认是否真的要改" |
| **加强提示词后 D03 反而更差**：0 次委派、4.58 秒直接作答 | `probe2/D03-1` |

诊断：订单子代理**用散文征求批准，而不是用工具**。提示词里已经写了"不要自己征求批准""缺少信息就调用 `request_order_info`"，但模型仍然选择在回答里问。加粗语气后没有改善，说明**只靠提示词约束不住**——这与 T11 发现的"分工不能只靠提示词"是同一类问题的第二个实例。

**已按这两个方向修，并已实测解决**：
- 主 Agent 的 `DELEGATION_RULES` 第 4 条原本写着「用户没有明确要求下单时，只给建议」「下单要用户点头」——**真正诱发散文式确认的就是它**。已改为：下单/改单交给 `procurement-order` 就算完成主 Agent 的部分，审批由系统在工具**执行前**自动发起；信息不全**不是**先回来问用户的理由（子代理会用 `request_order_info` 中断提问）。
- 订单子代理的 `description` 改成「把下单或改单的意图变成一次订单操作……审批由系统自动发起，不需要先征求同意，也不需要用户先把话说全」。

改完之后 **D02 与 D03 都真跑真过**（D03 的轨迹：`task → order_search_details/part_query/supplier_query → task → order_update(60件, expected_version=1)`，第一次拒绝、第二次批准，`version` 变成 2）。

### T23 发现并修复的缺陷（都藏在"看起来能跑"之下）

1. **记忆键不接受前导斜杠，而框架就是这么传的**：记忆挂载在 `/memories/`，`CompositeBackend` 会把挂载前缀剥掉、把 **`/preferences.md`** 交给后端；而 `memory_key()` 拒绝任何以 `/` 开头的键。于是模型按文档写 `/memories/preferences.md` 时，整个运行以 `NAMESPACEVIOLATION` 结束（D06 #1/#2，7 秒就死）。已改为**归一化**（剥掉前导斜杠，保留"不得穿越""不得为空"两条真正有分量的检查）——「键必须以相对路径给出」这条约束**在契约和测试里都找不到出处**，是自己发明的。
2. **判定"通过"时看不见失败的运行**：`ok` 当时等价于"没有记录任何失败"，而**一个崩掉的运行不会记录任何失败**——D06 #1/#2 因此被判通过，真正满足那三条期望的是记忆中间件**另一条**自动写入。已改为按**每次运行**记录 `done` 状态，任何一次 `failed` 都判该试验失败（`interrupted` 不算：停在审批或补充上是正常的结束方式）。
3. **轮次目录会把不同轮次混着算**：`_summarise` 刻意从目录读（为了让一轮能跨多条命令跑完），但目录里没有"哪些试验属于本轮"的标记。实测：`probe9` 是昨天建的目录，它昨天那个 D03-1 被判成了这一轮的**失败**。这次是假失败，同一个机制也能造出**假通过**。已加 `round.json` 的 `run_id`：每次试验带 `round_run_id`，汇总只算本轮，其余列进 `ignored_from_other_runs`（不静默跳过）。

4. **试验之间没有清沙箱，`demo.md` 的「24 次彼此独立」因此只做了一半**：`_reset_owner` 重置了 Mongo、Store 与 `/skills/users`，但**没清 `/workspace`** —— 报告、scratch、暂存的技能包都留在容器里。实测后果：D07 #1/#2 开局就读到 D05 留下的报告（正文写着「报价抓取全部失败，无金额」），于是两轮都在诊断这个网络故障，**从没创建技能**，双双判失败。残留让后面的场景看起来像"模型不听话"，而模型其实在认真处理它看到的东西。已改为每轮清掉 `/workspace` 下除 `rules`（中间件写入、prompt 要读）之外的一切。那两次失败试验保留在 `artifacts/live/r1/replaced/D07-{1,2}-sandbox-leftovers/`。

5. **对账漏了 D05/D07 的期望 —— 第三处假通过，且是最值钱的一处**：`_reconcile` 里 D05 只查「有没有新产件」＋「trace 里有没有比价相关调用」，D07 只查「有没有已分配的技能」。两个场景自己声明的数字与不变量**一条都没被查**。后果：D05 三次全过，而它产出的报告正文写着「报价抓取全部失败，无金额」、图表标题是「无报价，无法计算补货成本」——**这一轮根本没抓到报价，判的却是通过**。D07 同理：它声明了「固定样例算出 1533.00」「使用技能必须留下读 SKILL.md 与执行脚本的轨迹」「未提供 scope 要询问」，一条都没断言。`scenarios.py` 的 28 条不变量只兑现了一部分，而默认口径「22/24 且每个场景 ≥2」在 D05/D07 上因此是**不可信**的。**这是下一步最该补的一处**：把 D05 的推荐映射与合计、D07 的样例金额与使用轨迹写进 `_reconcile`，再重跑这两个场景。

6. **夹具站点起在随机端口，而所有技能文档都写 8088（已修）**：`running_stack` 用 `site_service.running_site(host="0.0.0.0")` 但**不传 port** → 随机端口；而 `supplier-price-urls`／`web-scraper` 告诉 agent 去 `host.docker.internal:8088` 抓页面（`config.py` 的默认值就是 8088，`infra/sandbox/README.md` 也记着这个端口下沙箱可达）。实测 8088 上**没有任何服务在听**。后果：D05 抓不到报价，产出标题为「无报价，无法计算补货成本」的报告——而旧对账只查"有没有产件"，于是判通过。已改为**按 `FIXTURES_BASE_URL` 的端口起站点**，占用则启动失败（不再静默退回随机端口，那正是坏掉的方式）。修完 D05 三次真通过。

7. **D07 的两次失败是装置属性，不是模型不听话**（**需你决定**）：
   - **`model_calls_per_run = 40` 把它掐断了**：D07 #1 做了 140 次工具调用仍在推进，报 `Model call limits exceeded: run limit (40/40)`。这个 40/200 是 `tools_summarization.py` 里的**代码默认值，契约没有钉死**。抬高一个成本护栏是你的决定，我没有擅自改。
   - **脚本化的客户答不了模型的设计提问**：D07 #2 停下来问"新技能复用现有口径还是另立一套？"，而自动化客户端的台词是固定的（下一步是"用固定样例验证这个技能。"），于是卡住不动。

   另外 D07 的"固定样例"（`fixtures/skills/reorder-cost-summary-v1/examples/input.json`，42×24.00+30×17.50=1533.00）此前**从没被放进沙箱**，模型无从算出那个数；已加进 `_prepare`（放到 `/workspace/samples/reorder-input.json`）。之后 D07 #3 真通过：分配 scope 正确、读过 SKILL.md、执行过脚本、工具返回里有 1533.00。

8. **被审批拦下的写不在工具回调里，只在中断里**（读 trace 时容易误判）：D04 的 `trace` 只有 `write_todos/task/supplier_query/part_query`，看起来"从没创建订单"，而实际是中断里 `tool_name=order_create`、参数正确，ERP 也恰好新增了一张订单。原因：被 HITL 拦下的调用**不会进入工具函数体**，因此不触发 `on_tool_start`。`test_t23.py` 已改为**同时看 trace 与中断候选里的 `tool_name`**，否则会把一次"被正确门禁的创建"读成"没调用"。

9. **`test_t23.py` 当前 40 通过 / 2 失败**，两个失败是同一件事：**D07 只有 1/3**，低于下限 2。其余全部真实通过（八个场景的证据、事件流可重放、三类图表 PNG + 摘要、搜索记下了服务它的引擎、桌面与移动两视口无重叠、无失败运行、无混轮次试验）。

### D07 专项：从 1/3 到 2/3（改的是装置，不是期望）

按你的决定做了四件事：

- **模型调用预算 40 → 80**（`BudgetConfig.model_calls_per_run`）。
- **工具调用预算 60 → 120**（`tool_calls_per_run`）。**这条是我按同样的道理一起抬的，你可以否决**：D07 #1 此前是死在 `Tool call limit exceeded` 上，和模型预算是同一类、同一个用例；依据与那条一样（D07 链路读得多——先在两棵技能树里 `ls`/`read_file` 很久才动手写）。
- **给 D07 加一轮回答台词**："按纯计算来就好，复用现有技能的口径，不要另立一套。"（#2 曾停下来问这个问题而客户端答不上来。）
- **固定样例在对话里点名**：第二／第三轮改成"用 `/workspace/samples/reorder-input.json` 这份固定样例验证这个技能"。此前只说"固定样例"，有一次它拿**自己技能包里**的 `examples/input.json` 去验，算出来当然不是 1533.00。

改完之后的实测（`artifacts/live/r1/D07-*`）：**1/3**。#2 真通过（481 秒、194 次工具调用、`read_skill` 与 `ran_script` 齐全）。三次的失败各不相同，都是 D07 这条链本身长而严：

| 试验 | 卡在哪 |
|---|---|
| #1 | 没走到分配（91 次调用后仍未委派出去） |
| #3 | 分配了、跑了脚本，但**没读自己那个技能的 SKILL.md** —— 契约明写"只重读描述不算使用"的正面要求是两者都要有 |

**最后一处根因（修完即 2/3）**：D07 的 `setup` 写着「**重启 API 并重建沙箱**后仍需可用」，第四轮是「重启之后再用一次这个技能」——而 **runner 从来没有执行过这一步**。于是那一轮的前提是假的：沙箱里还是模型几分钟前自己写的文件，既不需要恢复、也没有理由重读 SKILL.md，而它却被拿"读 SKILL.md 的轨迹"来判。

已加 `Scenario.rebuild_before_turn`（D07 = 4）与 `_rebuild_sandbox()`：在那一轮之前清掉 `/skills/users` 与 `/skills/.user-skills-revision`。**不回收容器**（图在开跑前就建好、持有它的代理，`_reset_owner` 里记过这个坑），而删掉的那个标记正是 `UserSkillsRestoreMiddleware` 自己文档里写的"重建过的容器没有任何标记，那正是必须全部重放的信号"——从中间件的角度看这就是同一个事件，"重启 API"那一半也一并覆盖了（进程内的 generation 缓存不足以跳过容器已不承认的恢复）。

补上之后 **D07 = 2/3 且整轮通过**（23/24），而 #1 仍然失败：**改的是装置，不是期望**，这条用例仍在真正区分。

前两条写进了 `docs/runtime/versions.md` 的「T23 实测结论」。

**没有为此伪造通过**：D02/D03 的失败原样留在 `artifacts/live/probe*/`，`summary.json` 里 `passed_overall: false`。

改动过的源文件已复验：**T11 / T12 PASSED**。

### T23 剩下的事 —— 三件都已完成

1. **24 次完整轮次**：`artifacts/live/r1/` —— 24 次试验、**23 次成功**（要求 22），八个场景每个 ≥2，`passed_overall` 为真、`ignored_from_other_runs` 为空。改动前那一轮整套挪到了 `r1-prefix-20260919/`，另有一次因进程竞态作废的留在 `r1-racing-discard/`，被替换掉的失败试验留在 `r1/replaced/`。
2. **Playwright 演示**：桌面 1440×900 与移动 390×844 两视口都出图且无重叠（`overflow_x` 为 0），记录在 `r1/screenshots.json`。顺带修掉一处竞态：第一个视口撞上前端 dev server 首次编译，`_sign_in` 因为没找到登录闸门而**静默跳过**，于是把空页面拍下来报成"没有渲染出工作区"——一个由竞态凭空造出来的布局失败。
3. **`tests/acceptance/test_t23.py`**：41 例，对着 `artifacts/live/<round>/` 断言（不重跑模型），含一条真实搜索的 live 冒烟与一条"证据里的引擎必须等于代码里的引擎且写进了契约"的检查。写它的过程中也纠正了我自己一处写窄的断言：被审批拦下的写**不会进入工具函数体**，因此不触发工具回调、只出现在中断里（见第 8 条）。

### T24 已完成（25 / 25，计划里所有任务都 done）

**gate 三项全过**：`acceptance 14 例`（要求 ≥8）、`java-build`、`frontend-build`；收据 `artifacts/tasks/T24/20260921T143918Z/receipt.json`。`plan_guard check` 现在报 `25 tasks; 25 done`，`next` 报 "No eligible pending task. Review blocked tasks or final acceptance."

R28 交付齐了：`README.md`（改写为安装→启动→烟测）、`docs/runtime/runbook.md`、`walkthrough.md`（下单 / 报告 / 技能恢复三条链，引用真实文件与函数）、`scripts/dev.py`、`scripts/seed_demo.py`、`tests/acceptance/test_t24.py`。

`test_t24.py` 的断言分两层：**文档里出现的每条 `python scripts/x.py sub` 与每个仓库路径都必须真实存在**（文档指错地方正是这块交付物最容易烂掉的方式），外加一条**不可跳过的真实启停循环** `up → status → smoke → down → up`，并在第二次 `up` 后断言数据库名没变——这才是"数据默认保留"的证据。

写作时改正了自己一处写窄的断言：原先要求子命令以 `add_parser("x")` 字面量声明，而 `plan_guard.py` 用列表声明，于是一个**能用的命令被判成了缺失**。

**仍未发生的事**：`review` 全部是 `not_reviewed`。所有应用任务的自动与真实检查全绿，**不等于**用户已经审阅过——这一列只能由人填。下面这段是这段工作的实测记录，保留。

**已完成且实测过的部分**

- `scripts/dev.py`：`doctor` / `up` / `status` / `logs` / `smoke` / `seed` / `down` / `reset`。`up` 复用**已验收的装配路径** `tests.live.stack.running_stack`（没有第二套启动逻辑），只是把运行目录换成 `artifacts/dev/`、数据库换成**按名字**的 `rush_harness_test_dev`——所以**重启保留数据**，"数据默认保留"因此是默认行为而不是一句承诺。
- 为此给 `running_stack` 加了两个可选参数 `run_dir` / `database_name`，**不传就是原来的临时目录与唯一库名**，所有既有测试的隔离性没变。
- `scripts/seed_demo.py`：把 ERP 的数据读回来，和 `scenarios.by_id("D01").numbers`（其数字来自 `fixtures/expected-v1.json`）逐项核对。不是打印"已 seed"。
- **实测记录**（2026-09-21）：`up` → 自行拉起沙箱控制服务（pid 60788）→ 栈就绪 `http://127.0.0.1:12851` → `status` 四个服务全 `ok` → `smoke` 948 帧 `done=completed`、走的是委派路径 → `down` 干净停止、**残留进程 0**。
- `up` 会先做前置检查：沙箱控制服务没起就**自己拉起来**（`OPENSANDBOX_INSECURE_SERVER=YES`，命令串取自夹具的 `START_COMMAND`，不重述）；起不来就打印可粘贴的命令，而不是抛四层深的 traceback。`down` 只停**本次会话启动的**那个（`artifacts/dev/sandbox.pid`），别人起的服务不动。

**写这个脚本时我自己犯的两个错，值得记下来免得复现**：① 第一版把子进程输出丢进 `DEVNULL`，于是启动失败只报"exit 1"——**正是 T24 要消灭的那种失败**，已改为落 `artifacts/dev/logs/serve.log` 并在失败时打印尾部；② 第一版在 `smoke` 里**又写了一遍 SSE 解析**，帧格式解析错，把一次真的调用了工具的运行报成"没有工具调用"。已改为复用 `scripts/demo.py` 的 `read_frames` / `trace_of`。**线上看不到子代理的调用**（子代理跑在 `task` 里，见 `api_view/stream_adapter`），所以烟测的措辞是"走了委派路径"，不是"读到了 ERP"——后者由 `artifacts/live/` 的轮次负责。

**还没做**：`docs/runtime/runbook.md`（服务、配置、端口、日志、停止/重置，全部换成上面这些**实测过**的命令）、`walkthrough.md`（创建订单 / 报告 / 技能恢复三条链，引用真实文件与函数）、README 改写成安装→启动→烟测、`tests/acceptance/test_t24.py`（≥8 例，需覆盖启停/重启/reset 且不能跳过真实服务测试）。

### 装配契约（已确认，省掉重新摸一遍）

`build_middlewares(context)` 的 8 个槽位要求的上下文键**精确如下**（缺一个就抛 `KeyError`，不会静默跳过）：

| 槽位 | 需要的键 | 工厂 |
|---|---|---|
| `todo_list` | — | `TodoListMiddleware()`（框架侧，必须自己加） |
| `sandbox_health` | `manager`、`owner_resolver` | `SandboxHealthMiddleware(manager=..., owner_resolver=...)` |
| `context_injection` | `store_provider` | `ContextInjectionMiddleware(store_provider=lambda owner: ...)` |
| `skills_sync` | `backend_provider` | `SkillsSyncMiddleware(backend_provider=lambda: ...)` |
| `user_skills_restore` | `backend_provider`、`skill_reader` | `UserSkillsRestoreMiddleware(backend_provider=..., reader=...)` |
| `conversation_summary` | `model`、`backend`、`workspace_writer` | `[build_compaction_tool(model, backend), ToolsSummarizationMiddleware(writer=...)]`（注意展开成**两个**） |
| `memory_update` | `store_provider` | `MemoryUpdateMiddleware(store_provider=...)`；不挂 hook，由 run 层调 `apply()` |
| `sandbox_breaker` | 可选 `breaker_registry` | `BreakerRegistry()`（按用户一个） |
| `model_tool_limits` | 可选 `budget_config` | `build_budget_middlewares(BudgetConfig(...))` → **两个**中间件，**同一批实例要同时给子代理**，共享预算就是这样实现的 |

其它已确认的签名：

```
build_virtual_backend(*, sandbox_backend, store, owner_user_id) -> CompositeBackend
build_main_agent(*, model, tools, write_tools, backend, owner_user_id, subagents,
                 delegated_tools=None, preferences=None, skills=(), middleware=(),
                 middleware_inventory=None, subagent_models=None, subagent_middleware=(),
                 checkpointer=None, store=None, system_prompt=None, authorizer=None)
WriteApprovalMiddleware(*, service: ApprovalService, channel: MCPWriteChannel, write_tools=(...))
MCPWriteChannel     ← **是个 Protocol，需要自己实现**（T23 要做的第一件事）
UserScopedStore(store, user_id)          ← store_provider 就用它
StoreAssignmentReader(store, *, namespace=("skills",), pointers=None)
sandbox_service.running_pool(settings, *, warm_pool_size=1, ...) -> (SandboxManager, CountingFactory)
PersistenceSettings(mongo_uri, database, checkpoint_collection, checkpoint_writes_collection, store_collection)
```

沙箱池返回的 manager 就是 `sandbox_health` 要的那个；`manager.get_or_create(owner)` 给的是 `SandboxBackendProxy`，它同时是 `backend_provider` 的返回值和 `build_virtual_backend` 的 `sandbox_backend`。

### 两个外部见证与一条提醒

- 智谱搜索：`src/agent/tools/web_search.py`，注意**它忽略 `count`**，上限是我方 `MAX_COUNT`。
- ModelScope 图表：`src/agent/tools/chart_generator.py`，18 个远端工具罩在 `chart_generator` 一个入口后面。
- **live 与确定性回归分开统计**：T22 的覆盖报告已经把这两列分开；R27 现在是**待验收**，T23 通过后才会变。不要为了让报告好看而提前改状态。

### T20 的落地情况（供 T21 参考，已不是"待做"）

**已就绪并被 T06/T20 实测：** 独立服务环境 `.venv-agent-protocol/`、`running_service()` fixture、`AsyncSubAgent`（`graph_id` 是判别式）、框架生成的 launch/check/update/cancel/list 五个工具、`langgraph-sdk 0.4.4`。

**T20 新写并已验证：** 只读分析图（`infra/agent-protocol/analyst_graph.py`，已注册进 `langgraph.json`）、`/internal/sandbox/operations`、`/internal/analysis/read`、`src/agent/async_tasks/`、四个业务端点、`async_analyst.py` 声明、前端后台任务组件。

**仍然没有的：** 后台图里的模型（见偏离 24）、`operation_id` 的去重（见偏离 25）。

### T20 侦察留下的环境事实（仍有参考价值）

**已经存在、可直接用（不要重造）：**

| 项 | 位置 | 说明 |
|---|---|---|
| 独立服务环境 | `.venv-agent-protocol/` | `langgraph-cli 0.4.31` + `langgraph-api 0.14.1` + `langgraph-runtime-inmem 0.34.1`；**故意不进 `uv.lock`**，因为 `langgraph-api` 锁 `grpcio<1.82` 而 `opensandbox-server` 要 `>=1.83`。理由写在 `pyproject.toml:61-66` 与 `scripts/provision_agent_protocol.py:31-38` |
| 启动 fixture | `tests/fixtures/agent_protocol_service.py` | `running_service(port=8123, reuse_running=True)` 已会按需起停；`client()`/`async_client()`/`echo_assistant_id()`/`diagnostic()` 齐全 |
| 服务配置 | `infra/agent-protocol/langgraph.json` | **目前只注册一个 `echo` 图**（`graphs: {"echo": "./infra/agent-protocol/graph.py:build"}`）。T20 要把只读采购分析图加进去 |
| 框架侧异步子代理 | `.venv/.../deepagents/middleware/async_subagents.py` | `AsyncSubAgent = {name, description, graph_id, url?, headers?}`；**`graph_id` 字段就是判别式**（`deepagents/graph.py:663-668` 按它分流），给出后才会装 `AsyncSubAgentMiddleware`（`graph.py:893-896`） |
| 框架侧工具 | 同上 `:815-837` | launch / check / update / cancel / list **五个工具由框架生成**，不要自建 |
| 客户端 | 主 venv 的 `langgraph-sdk 0.4.4` | `get_client` / `get_sync_client` |
| 已验证样例 | `tests/compat/capabilities.py:816-821`（CAP-08）、`tests/acceptance/test_t06.py:275-284` | 独立服务 + SDK + `AsyncSubAgent(url=...)` 全部跑通过 |

**不存在、T20 要新写：**

1. **只读采购分析 graph** —— `infra/agent-protocol/` 里现在只有 echo。注意服务跑在**独立 venv**，那里**没有本项目依赖**（opensandbox 等），所以图要么保持薄（只用 langgraph/langchain-core + 走 HTTP），要么在 `langgraph.json` 的 `dependencies` 里声明并重跑 provisioning。
2. **`/internal/sandbox/operations`** —— 契约 `storage-sandbox.md:60` 要求，**全仓库代码零匹配**。`sandbox_manager.py`/`sandbox_proxy.py` 通读确认**没有任何 HTTP/FastAPI/端口代码**，目前纯粹是进程内对象。这是 T20 最大的一块：主进程要暴露一个白名单操作端点（内部 token + owner 校验），异步进程要有一个实现 `SandboxBackendProtocol` 的 HTTP 适配器。
3. **`src/agent/subagents/async_analyst.py`**、**`src/agent/tools/async_tasks.py`**、**`src/api_view/api/async_tasks.py`** —— 三者都不存在。
4. **owner ↔ parent_thread/async_thread/async_run 的本地映射** —— 不存在。契约 `external.md:45` 要求记录 owner、parent_thread_id、async_thread_id、async_run_id、status、artifact_ids。
5. **前端异步状态组件** —— `frontend/src/` 里与后台任务相关的代码**完全不存在**（搜 `asyncTasks`/`后台` 只命中 CSS 的 `background`）。`state/chat.ts` 的 `KNOWN_EVENTS` 也没有异步事件。

**业务端点（契约 `external.md:47` 固定，不要自创）：**

```
POST /api/async-tasks                     {parent_thread_id, request_id, instruction} -> {task_id, status}
GET  /api/async-tasks/{task_id}
POST /api/async-tasks/{task_id}/update    {request_id, instruction}
POST /api/async-tasks/{task_id}/cancel    {request_id}
```

这层只做**归属、幂等与官方 SDK 适配**，不自创替代 Agent Protocol。状态取值固定 `queued/running/completed/failed/cancelled`。

**两处容易踩空的地方：**

- **T13 并没有用这个服务** —— `tests/acceptance/test_t13.py` 全文没有 8123/agent_protocol 引用（它的真实图用例走进程内 `create_deep_agent`）。真正消费 fixture 的是 T06。HANDOFF 早前那句"T13 按需拉起 8123"与代码实况不符，已改正。
- **不能靠进程内锁** —— 契约明说 Agent Protocol 是独立进程，不共享 Python 内存锁。主进程是**唯一**的沙箱分配与租约持有者；异步进程不得直接认领/重建容器。

## 需要用户决策/配置

1. 第 6 条（主 Agent 不持有 ERP 工具）、第 10 条（写操作只走异步）、第 16 条（图表归分析子代理）、第 17 条（产件只导出 `/workspace`）、第 20 条（发布指针移到 Mongo）、第 23 条（`assign_skill` 归主 Agent）都是主动设计选择，如与你的预期不符请告知。
4. **第 24 条需要你决定**：后台分析图目前没有模型（独立服务环境里没有模型客户端）。要变成"模型决定看什么"，需要往 provisioning 的固定清单里加 `langchain-openai` 并重跑一次 `python scripts/provision_agent_protocol.py`。我没有擅自改动已锁定的独立环境——你说一声我就补。
2. 第 15 条修好了一个既有 bug：`X-Demo-User` 头此前完全不可用。此前没有测试覆盖它，若你有依赖它的脚本，现在才真正能用。
3. 第 22 条只是 HANDOFF 表格里的一处笔误（收据路径写错），**不是代码或计划状态的问题**，`plan_guard` 的相关校验本来就存在且有效。这一条我先前描述错了，已在第 22 条里更正，无需你做任何事。

## 后续交接模板

每次任务完成/暂停时替换当前现场，保留必要历史链接：当前 task/status；已完成步骤；文件入口；
真实检查命令和 receipt；未过断言及根因；服务 PID/端口或容器；下一条命令；是否需用户配置/决策。

## 2026-10-04 Linux 评测环境修复

WSL/Linux 原先误用了 Windows `.venv-agent-protocol`，导致 Agent Protocol 将 `/mnt/c/...` 配置路径交给 Windows launcher 并退出。现已增加 `.venv-agent-protocol-linux`，锁定 `infra/agent-protocol/requirements-linux.lock`，fixture/provision 脚本按操作系统选择环境和 launcher；Windows 环境保留不动。外部评测 worker 同步使用 Linux launcher 与 `.venv/bin/opensandbox-server`。

真实零模型检查已通过：

```text
/mnt/c/dev/rsi-eval/procurement_eval/runs/doctor-0634547ea8ab4d8d8ef4706165d9641e/doctor.json
```

`ready=true`，模型身份 `deepseek-flash`、provider `/models`、Mongo、Java、OpenSandbox、Agent Protocol 全部为 true，`model_calls=0`。随后 `prepare` 通过：真实 ERP/MCP/报价站/OpenSandbox/Agent Protocol 均启动；两个沙箱无宿主挂载且宿主探针退出 0；ERP 前后订单均为空。该证据只证明环境和隔离边界，不证明真实 Actor 学习收益。下一步仍需用户明确新增模型费用额度后，才运行真实 Actor 基线。
## 2026-10-04 T48 完成

T48 gate `artifacts/tasks/T48/20261004T074542Z/receipt.json`：14 项组件测试与只读 live 核验通过，0 failed/0 skipped。真实实验目录 `/mnt/c/dev/rsi-eval/procurement_eval/runs/planning-session-20261004/attempt-decision-pilot-152921`：复用已完成 fixed 不可行反例，真实 Curator 1 call（deepseek-flash；4543 输入/255 输出 token；保守估算 0.047772 CNY），没有重复 T47 六条消融。Curated 不可行案例正确记录 `infeasible` 且 ERP 零订单；来源遮蔽案例正确记录 `needs_information`、独立 judge `unresolved` 且零订单；完整来源控制通过独立 judge `feasible/optimal`，逐单审批后两单合计 2196。`contrastive-operations.json` 保留 attempt-7 真实 computation 成功/失败输入、stdout/stderr 摘要与 hash；Curator 输入为脱敏操作/状态摘要。报告只支持描述性条件行为和真实管线，不支持学习增益或统计显著。下一步按用户决定进入简历演示或后续动态重规划，不再重复本轮付费运行。
