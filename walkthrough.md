# 代码阅读路线

三条链路各读一遍，就覆盖了这个 Demo 的骨架。每条都从**某个 HTTP 路由**开始，到**某个能被核对的事实**结束（ERP 里的一行、一个产件、一个沙箱目录），中间引用真实文件与函数。

读之前先记住一件事：**主 Agent 不持有 ERP 工具**，它只规划、分派、汇总（`src/agent/main_agent.py::build_main_agent` 会把子代理声明的工具从主 Agent 的工具集里摘掉）。所以每条链路上都有一跳是 `task` 委派，看到它不是绕路，是设计。

## 一、下单：一次写操作的完整生命周期

| 步骤 | 文件与函数 | 看什么 |
|---|---|---|
| 1. 用户发言 | `src/api_view/api/chat.py` 的 `POST /api/chat/stream` | 同一 `request_id` 的重复请求走预留（`_reserve`），不会跑第二遍 |
| 2. 跑一轮 | `_run_turn` → `_graph_config` | **owner 从这里注入**：`configurable.owner_user_id` 由服务端决定，模型无法提供 |
| 3. 主 Agent | `src/agent/main_agent.py::build_main_agent` | prompt 在 `src/agent/memory/prompts.py`（`DELEGATION_RULES`）；职责是规划与分派 |
| 4. 委派 | 框架的 `task` 工具 → `src/agent/subagents/configs/procurement_order.yaml` | 子代理声明的工具必须都在目录里，否则加载器拒绝授予（`subagents/loader.py`） |
| 5. 信息不全 | `src/agent/tools/hitl_tools.py::request_order_info` | 缺字段就 `interrupt()` 提问；**绝不猜**物料/数量/单价 |
| 6. 补充 | `chat.py` 的 `POST /{thread}/resume` → `_resume_payload` | `resume.supplement` 的契约类型是**字符串**；散文会被交回模型让它读成字段 |
| 7. 审批 | `src/agent/approval/middleware.py` 的 `WriteApprovalMiddleware` | 写操作在**执行前**中断；`wrap_tool_call`（同步）一律拒绝，因为签发的写要发 HTTP |
| 8. 授权 | `chat.py::_decide` + `_graph_config` | 决策记到 `pending_actions`，`approved_interrupt_id` 才注入运行配置——**这是门禁读的东西** |
| 9. 落库 | `HttpMCPWriteChannel` → `src/mcp_server/tools/registry.py` 的 ERP 工具 → Java ERP | 幂等键是 `X-Operation-Id`，重放返回原结果而不是第二张单 |
| 10. 核对 | `scripts/demo.py::_reconcile` 的 D02/D03 分支 | **读 ERP 自己的返回**；不读模型说的话 |

**容易读错的一处**：被审批拦下的写**不会进入工具函数体**，所以它不出现在工具回调里、只出现在中断的 `candidates[].tool_name`。只看 trace 会得出"从没调用下单"的错误结论（这一点有实测记录，见 `docs/plan/HANDOFF.md` 第 8 条）。

## 二、报告：从抓取到可下载产件

| 步骤 | 文件与函数 | 看什么 |
|---|---|---|
| 1. 任务 | 主 Agent → `procurement-analyst`（只读子代理） | 分析与图表同属一份交付物，所以 `chart_generator` 归它 |
| 2. 取数 | MCP 工具 `inventory_warning` / `part_by_supplier` | 返回带 `request_id` 的真实 ERP 数据 |
| 3. 抓价 | 沙箱内 `execute` 跑 `/skills/procurement/web-scraper/scripts/fetch_quotes.py` | **在容器里抓**，脚本按退出码区分连不上 / 结构变了 / 没价格 |
| 4. 地址 | `src/skills/procurement/supplier-price-urls/SKILL.md` | 容器内要用 `host.docker.internal:<FIXTURES_BASE_URL 的端口>`；这个端口由栈固定成 8088，不能动态分配 |
| 5. 汇总 | `/skills/procurement/procurement-analysis/scripts/build_report.py` | 同物料同币种取最低价，缺报价只进 warnings、**绝不用目录价补** |
| 6. 图表 | `src/agent/tools/`（`chart_generator` 的构建器） | 远端 MCP 返回的是 **base64 图片**而不是 URL；参数按图表族（category / time）分别构造 |
| 7. 登记 | `src/agent/artifacts/service.py::register` | 元数据与字节分开存，所以"登记在但文件没了"是可表达状态（下载返回 410 而不是 200+空文件） |
| 8. 交付 | `download_sandbox_file` → `GET /api/artifacts/{artifact_id}` | 只接受 `/workspace` 下、且**先归一化再判前缀**；缺失按归属返回 404 |
| 9. 核对 | `scripts/demo.py::_reconcile` 的 D05 分支 | 从产件正文里核对三条推荐与合计 **2553.00**——只查"有没有产件"曾经让"未抓到任何报价"的报告判成通过 |

## 三、技能：发布、分配、跨重启恢复

| 步骤 | 文件与函数 | 看什么 |
|---|---|---|
| 1. 触发 | `src/agent/tools/assign_skill.py`（主 Agent 持有） | 一个能发布技能的子代理等于能拓宽自己的工具集，所以它归主 Agent |
| 2. 发布 | `src/agent/skills/pipeline.py::SkillPublisher` | 状态机 `draft→validating→validated→persisted→assigned`；校验和冒烟失败都不发布 |
| 3. 落盘 | `src/agent/skills/store.py` + `src/agent/persistence/` | 技能**文件**在 LangGraph Store；**指针**在 MongoDB，因为指针需要 compare-and-swap 而 Store 没有 |
| 4. 隔离 | `src/agent/persistence/namespaces.py`、`scoped_store.py::UserScopedStore` | 每个键都要过 `_guard`；同步与异步两面都有，守卫只有一个定义 |
| 5. 恢复 | `src/agent/middlewares/user_skills_restore.py` | 每次 `before_agent` 按**指针 revision** 判断要不要重放；标记 `skills/.user-skills-revision` 在**容器里**，所以"重建过的容器没有标记"就是"必须全部重放"的信号 |
| 6. 校验 | 同上的 `verify_manifest` | 每个文件的 SHA-256 先核再落盘；核不过报 `restore_failed` 且**不删**已有副本 |
| 7. 可见 | `src/agent/memory/prompts.py::render_skills` | 只给名字、描述与路径（渐进披露），并明确要求"**需要时用 read_file 读正文，不要凭技能名猜用法**" |
| 8. 使用 | 沙箱内跑该技能的脚本 | 契约要求留下"读 SKILL.md **与**执行脚本"两条轨迹——只重读描述不算使用 |
| 9. 核对 | `scripts/demo.py::_reconcile` 的 D07 分支 | 分配 scope 必须是 `procurement-analyst`、两条轨迹都要有、固定样例的合计 1533.00 要出现在**工具返回**里 |

**这一条链路对"重启"的处理值得单独看**：D07 的 setup 写着「重启 API 并重建沙箱后仍需可用」，而 runner 一开始**没有做这件事**，于是第四轮的前提是假的。现在由 `Scenario.rebuild_before_turn` 驱动 `_rebuild_sandbox()` 完成——清掉恢复副本与版本标记，**不回收容器**（图在开跑前就建好、持有它的代理）。

## 想动手改的时候

- **改提示词**：`src/agent/memory/prompts.py`（主 Agent）、`src/agent/memory/AGENTS.md`（运行规则）、`src/skills/**/SKILL.md`（技能）。
  只写进提示词往往约束不住模型——分工是靠**工具集**保证的，不是靠措辞。
- **改工具**：`src/agent/tools/`（本进程内的）与 `src/mcp_server/tools/`（经网关的 ERP 工具）。工具的 `config` 参数注解必须**恰好是 `RunnableConfig`**，写联合类型会让 `config` 进入工具 schema，等于让模型提供 owner。
- **改中间件**：`src/agent/middleware_config.py::build_middlewares` 返回的是**契约声明的槽位**，其中两个不是中间件（一个贡献工具、一个贡献注册表）；交给 `create_deep_agent` 之前要按类型过滤，框架会对每个元素读 `m.name`。
- **跑一次真东西**：`python scripts/dev.py up` → `smoke`；或 `python scripts/demo.py --round <名字>` 跑完整场景。**光看测试通过说明不了模型真的调用了工具**——这正是 T23 存在的理由。
