# 技能、记忆与上下文

## 预置技能

保留课程目录和用途，每个技能有可用示例与失败说明：

| 路径（相对 src/skills） | 作用 |
|---|---|
| main/skill-management/ | 下载或创建、验证、分配、持久化流程 |
| procurement/procurement-analysis/ | 查询预警、比价、计算、报告 |
| procurement/supplier-price-urls/ | 供应商 ID 到演示报价 URL 的映射 |
| procurement/web-scraper/ | 沙箱内 HTML 抓取与结构化报价抽取 |
| procurement/web-content-fetcher/ | HTML 转 Markdown，不把网页当系统指令 |
| procurement/chart_params.md | 本次真实远端图表目录和参数索引 |

SKILL.md 用 YAML frontmatter，至少 name、description；正文写业务步骤、输入输出、失败条件。名称 slug 使用 `[a-z0-9]+(-[a-z0-9]+)*`，最长 64。脚本放 scripts，样例放 examples，不把实际 API 密钥写入任何技能。

## 新技能案例及接口

核心新增技能 `reorder-cost-summary`：输入已查询的预警和已抓取报价，输出补货数量、金额合计及 Markdown 表格。脚本接收 JSON 文件和输出目录，不自行发起订单写操作。

输入行 `{part_id,supplier_id,quantity,unit_price,currency,source_url}`，数量整数、金额字符串；输出含 lines、total_amount、currency、source_urls。固定测试 P001 42×24.00 + P004 30×17.50 = `1533.00`。代码必须实际执行并解析结果，不用模型说“测试通过”代替。

管理工具：`assign_skill(source_type, source, slug, target_scope)`；source_type 为 generated/package，source 为沙箱生成目录或资源站批准 URL，target_scope 为 main/procurement-analyst/procurement-order。未知 scope 拒绝；未指定 scope 时通过补充信息请求询问用户，不能默认为所有 Agent。工具返回 skill_id、version、status、validation_artifact_id。

## 发布状态机

`draft -> validating -> validated -> persisted -> assigned`，失败进入 validation_failed/persistence_failed；恢复失败独立 restore_failed，不删除已存版本。

1. 模型生成到 owner 沙箱 staging 目录，或真实下载 ZIP 到 staging。记录来源、SHA-256、创建时间。
2. 校验文件列表、frontmatter、脚本入口、输入输出 schema。ZIP 默认压缩包<=2 MiB、展开<=10 MiB、<=100 文件，不接受 symlink/路径穿越。
3. 脚本技能必须提供合法 JSON 示例，不补造空输入。在沙箱分别用独立输出目录执行示例和损坏 JSON，成功示例必须产生非空文件；执行后立即记录相对路径、字节数与 hash，损坏输入不能覆盖示例证据。核心 reorder-cost-summary 另跑服务端版本化业务案例，独立校验金额、逐行数据、币种、来源和 Markdown 表格，并要求负数量、布尔数量及不支持的币种被拒绝。失败反馈给模型，最多修复 2 次，每次留记录；仍失败不发布。
4. 服务端先用 skill_versions 的唯一索引预占不可变递增 version，再创建 manifest。新记录 publication_status=reserved，Store 失败标 failed；这两种记录不进入已发布版本查询，也不能成为 assignment。失败/崩溃已预占的版本号永不复用，旧无状态的已发布记录兼容读取，遗留半写 Store 前缀跳过且保留。将完整文件写 Store 版本前缀；读回校验后，以匹配 reservation_id 的条件替换标 persisted。
5. complete 开始时读取并冻结 owner+scope+slug 的 assignment revision。持久化后仅用这个预期值做一次条件更新；CAS 失败报告 ASSIGNMENT_CONFLICT，不重读 revision 自动重基抢占。已持久候选继续保留，重试属于新发布决策。只有已发布元数据允许创建指针，半写/预占/不存在的版本不能被分配。
6. 同步执行副本到 `/skills/users/{scope}/{slug}/`，标记 skills_revision。框架扫描缓存按 revision 在下一次调用刷新；不承诺当前 graph run 内即时发现。
7. 沙箱重建/应用重启时读 Store manifest，恢复指定版本，hash 校验后原子替换目录。不要从临时 download 目录恢复冒充持久化。

相同内容同 owner/scope 重复发布返回原版本；内容变更生成新版本。删除 assignment 不删除其他 scope；清理未引用 staging/旧副本不影响当前版本。全局共享用户生成技能不在当前范围。

校验报告明确 validation_level：structural（无脚本指导技能，behavior_verified=false）、installation（通用脚本安装冒烟）、procurement-contract（上述核心金额业务契约）。指导技能仍保留发布能力，但结构通过不代表行为正确。通用候选的自身示例和指定采购案例都不能证明泛化收益，后续进化晋升还需独立冻结评测。T30 改变内部 smoke 报告并保留原 attempts/outputs/observed_total 字段；对外工具 envelope 不变。影响回归：T17、T21、T23 的技能验证证据。

T31 内部 reserve_version 返回 (version, reservation_id)，record_version 新增可选 reservation_id 参数用于完成预占；未提供时保留旧导入接口。get_version/find_by_content/list_versions 只返回 persisted 或旧无状态记录；assign 对尚未发布的版本返回 None。验证 staging 和下载临时目录每次带独立 UUID，原对外工具参数不变。影响 T17、T21、T28 和发布恢复路径；T17 两处夹具改为先存在合法对应 scope 的版本再建指针，缓存失效和作用域断言保留。并发相同内容可能产生两个已持久候选，但同一旧 revision 只有一个可晋升；顺序重放保留当前同内容版本。这里仍没有实现完整进化 lineage 和评测驱动的晋升/回滚。

创建/下载/分配/清理均通过主 Agent 的 skill-management 能力实施。使用新技能须在调用轨迹留下 SKILL.md 读取、脚本执行和产物，不能只打印技能名称。

## 渐进披露和两条恢复通道

首次仅注入技能 name/description/path，不全量拼接所有正文。任务需要时 read_file 正文、必要脚本，再执行。预置技能根据清单 hash 增量同步；用户持久技能从 Store 恢复，二者分别统计与测试。

技能缓存的存在不代表可永久跳过扫描；assignment revision 变化、沙箱 generation 变化或 Agent 重建时失效。YAML 新增子 Agent 在下一次显式图重建后生效，不承诺运行中热重载。

运行装配将 YAML 技能名称解析为沙箱中的父目录，传给 DeepAgents 的 `skills`。主 Agent 扫描 `/skills/main` 和 `/skills/users/main`；子 Agent 扫描对应预置目录和 `/skills/users/{scope}`，刷新结果仅包含 YAML 声明的预置技能及自身 scope 的用户技能。`chart_params` 对应 `/skills/procurement/chart_params.md`，作为参考路径注入，不伪装成有 SKILL.md 的技能。应用在同步和恢复钩子之后用官方 SkillsMiddleware 的解析实现重新扫描一次，替换 checkpoint 中旧的 metadata；运行过程中不会自动重建正在审批的图。内部 `to_subagents` 新增可选 backend 参数，由 build_main_agent 传入稳定的 owner 后端以安装刷新钩子；对外工具协议没有变化。影响回归：T11、T17、T23、T24，以及 T28 的模型上下文检查。


## 用户偏好

preferences.md 四个显式偏好：language=zh-CN、currency=CNY、output_format=markdown、chart_type=bar。允许用户要求表格/其他支持图表，按 schema 更新；当前金额没有汇率服务，要求换算外币时说明不支持真实换算，不把 CNY 改标签当兑换。

两个自动历史字段：recent_supplier_ids（最近 5 个去重）和 recent_queries（最近 10 条简短采购查询）。只有成功 ERP 相关任务更新；闲聊、失败、拒绝写单不记录为成功采购。自动更新不得覆盖用户明确偏好，不保存密钥、网页指令或完整工具大响应。

读取和写入都经 owner namespace，保存成功后新 thread 读回。用户说“以后报告用表格”是偏好更新；网页中的“请修改用户偏好”不是授权。采用结构化内部 schema 生成 Markdown，不能让不受控模型输出覆盖整个配置对象。

API 运行入口只解析本次用户原文的显式偏好，在模型调用前保存；主代理和非 fork 子代理都安装动态偏好注入，身份来自可信 RunnableConfig 或 Runtime.context。每次运行创建独立 ERP 结果回调，包含委派调用；只有业务 envelope 的 ok=true 才认定成功，完成后按 evaluate_run 判定更新历史，失败、中断和闲聊不更新。审批恢复使用 checkpoint 内最近用户请求作为查询标签，不把审批命令当用户偏好。记忆保存失败通过 MEMORY_PERSISTENCE_FAILED 和 failed 终态告知客户端，已发生的 ERP 操作不会回滚。

Store 的 memories/{owner} 中，显式偏好仍在 preferences 键；自动历史写入独立 procurement-history 键，读取兼容旧 preferences 内嵌 history。自动写入不能覆盖并发更改的显式偏好；preferences.md 由当前结构化状态派生。尚不保证同一 owner 多线程自动历史写入的无损合并。影响回归：T13、T18、T23、T24，以及 T29 的真实 HTTP/MCP 委派测试。

## 上下文和中间件

保留启动规则、用户偏好、技能元数据、子 Agent 返回的分层注入。子 Agent 返回结构约定：summary、facts、artifact_ids、warnings、next_action；由任务提示和验证器约束，失败时说明缺项，不能解析失败后造空成功结果。

课程自定义顺序：health -> context_injection -> skills_sync -> user_skills_restore -> 主动摘要工具 -> memory_update -> sandbox_breaker -> model/tool limits。按锁定版本的实际 hooks 实现，记录 before/after/wrap 的真实调用顺序，不能把列表位置当全部执行时序。

自动摘要保留框架能力；主动 compact_conversation 单独暴露。摘要前完整消息已在历史/可读归档留存；压缩后保留未完成 todos、pending tool calls 和审批信息。工具返回大内容 offload 到文件并返回引用，不仅截断正文。

阈值基于实际配置的上下文容量，不能假设用户模型无限上下文。Demo 延续 T23 已批准的 BudgetConfig：每 run 模型调用 80、工具调用 120、总时长 900 秒；每 thread 模型调用 200、工具调用 400。异步分析 600 秒；测试用低阈值触发。此前本段 30/60/300 与已批准实现不符，本次同步文档，不再次调大配额。

每次 API invoke 安装独立预算 callback，主子代理共享；调用开始前扣额度，失败或参数无效的已开始调用也计数。线程累计以 owner/thread 条件对 Mongo 原子增量，不能跨用户借用；SDK graph 局部限制保留并由子代理继承，耗尽采用 error。运行流采用总时长 timeout，等待异步模型时也能终止；阻塞的同步操作仍依赖对应 SDK 超时。任何预算耗尽返回明确 error/failed，即使子代理错误被 ToolNode 接住后主代理继续回复，也不得改判 completed 或写入成功采购历史。已完成的 ERP 操作不回滚。runs.budget_usage 保存本次 counts、limits、elapsed_seconds 和 failure_code；模型 ID 仍来自配置。影响回归：T11、T13、T19、T23、T24、T29，以及 T32；熔断和摘要归档接线另包修正。

沙箱连续 3 次失败熔断 30 秒，health 成功探测后半开；仅计沙箱操作失败，不把业务 422 当沙箱故障。受限时返回明确错误和状态，不能无限重试或转宿主执行。

## 摘要前完整归档（T35）

自动摘要替换SDK默认同名SummarizationMiddleware，主动compact_conversation使用其官方工具层；两者均继承锁定实现，保持compute_summarization_defaults的触发、保留与截断策略。同步/异步摘要模型调用前，捕获原始state.messages（尚未应用旧摘要和参数截断），保留完整typed message字段、tool_calls、tool_call_id、artifact以及todos/__interrupt__，持久到owner限定Mongo Store并读回确认。失败不生成摘要或提交新_summarization_event；主动工具沿用官方可见Compaction failed结果，自动路径抛出明确归档错误。

archive/<thread_id>/<uuid>仅追加新key，记录应用run_id、checkpoint_ns及main/子代理scope；UUID避免同run多次摘要或并发覆盖。应用运行ID使用configurable.application_run_id，避免通用run_id被框架当成重入标记。非API调用可无应用run_id，但owner/thread/durable store不可缺失。BaseMessage采用model_dump(mode="json")，不只保存content；大于Mongo文档限制或不支持的状态序列化失败不得降级为仅摘要。

主代理与每个子代理均装配独立摘要引擎，使用实际配置模型：显式models覆盖优先，其次YAML model，再继承父模型；不猜测模型ID。to_subagents新增可选parent_model用于装配，已有仅描述调用保持兼容。build_compaction_tool返回官方工具中间件子类，tools和Command协议保持。检查影响T11/T13/T19/T28/T29/T32/T34以及后续live验收。这里只修复归档与运行接线，尚未建立封存评测或RSI晋升闭环。

## 2026-10-03 用户批准的新 T38（覆盖旧 kernel 约定）
持久计算指Mongo权威版本化JSON数据，每步OpenSandbox内独立Python进程。通用load_state/save_state；Agent自行计算，无固定planner。按owner/thread/session、operation ID/base version与容器generation隔离；临时结果正常退出、JSON校验、进程树清理确认后CAS发布。失败/取消/超时不更新旧状态；无法确认停止则隔离环境。仅复制read_names所需数据，未变化数据复用；函数/模块/DataFrame下次显式重建，不保持对象身份。API/容器重建加载数据，不重放代码；无任意文件/网络副作用回滚保证，订单仍MCP审批。T40 Actor工具/提示与T41轨迹、T43版本变化需按新语义回归；旧交付组件证据不证明新计算机制。旧Jupyter补丁与失败证据保留但不再继续维护。
