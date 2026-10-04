# 能力覆盖报告

本文件由 `python scripts/coverage_report.py` 生成，**不要手工编辑**。

三列状态的含义，互不替代：

- **自动**：该需求主任务的确定性 check（unit/integration）在 receipt 里为 passed。
- **真实**：该需求有 live 模式的 required check 且通过——只有 T15/T06/T23 这类需要真实外部服务的任务才有。
  自动通过**不代表**真实服务通过；缺凭据时该列为 blocked，不是 pass。
- **人工**：`state.json` 的 review 字段。**自动通过不代表人工已接受**，所以完成一个任务不等于它被 review 过。

> 任务状态本身不是实现证据：本报告读的是 receipt 的内容（状态、检查项、跳过数），
> 并逐个核对下面列出的文件确实存在。

## R01-R28

| ID | 能力 | 主任务 | 实现文件 | 测试 | 证据 | 自动 | 真实 | 人工 |
|---|---|---|---|---|---|---|---|---|
| R01 | 原技术栈、可重复构建 | T00 | `scripts/gate.py`<br>`scripts/doctor.py`<br>`pyproject.toml`<br>`uv.lock` | `tests/acceptance/test_t00.py` | `artifacts/tasks/T00/20260918T021254Z/receipt.json` | 通过 | — | not_reviewed |
| R02 | 供应商、物料、库存查询 | T01/T02 | `erp/src/main/java/com/rushharness/erp`<br>`src/mcp_server/tools/registry.py` | `tests/acceptance/test_t01.py`<br>`tests/acceptance/test_t02.py` | `artifacts/tasks/T01/20260916T051327Z/receipt.json`<br>`artifacts/tasks/T02/20260916T012144Z/receipt.json` | 通过 | — | not_reviewed |
| R03 | 采购订单创建/修改/查询 | T03 | `erp/src/main/java/com/rushharness/erp/orders/OrdersService.java` | `tests/acceptance/test_t03.py` | `artifacts/tasks/T03/20260916T051329Z/receipt.json` | 通过 | — | not_reviewed |
| R04 | FastMCP八工具网关 | T04 | `src/mcp_server/tools/registry.py`<br>`src/mcp_server/grants.py` | `tests/acceptance/test_t04.py` | `artifacts/tasks/T04/20260916T013441Z/receipt.json` | 通过 | — | not_reviewed |
| R05 | 供应商报价与技能下载源 | T05 | `fixtures/site/server.py`<br>`fixtures/site/skillpack.py` | `tests/acceptance/test_t05.py` | `artifacts/tasks/T05/20260918T021423Z/receipt.json` | 通过 | — | not_reviewed |
| R06 | 模型调用和框架兼容 | T06 | `src/agent/config.py`<br>`src/agent/env_utils.py`<br>`tests/compat/capabilities.py` | `tests/acceptance/test_t06.py` | `artifacts/tasks/T06/20260918T021014Z/receipt.json` | 通过 | 通过 | not_reviewed |
| R07 | checkpoint、展示历史、Store分离 | T07 | `src/agent/persistence/indexes.py`<br>`src/api_view/agent_loader.py` | `tests/acceptance/test_t07.py` | `artifacts/tasks/T07/20260918T021110Z/receipt.json` | 通过 | — | not_reviewed |
| R08 | 沙箱I/O与执行边界 | T08 | `src/agent/backends/custom_opensandbox.py`<br>`src/agent/backends/sandbox_setup.py` | `tests/acceptance/test_t08.py` | `artifacts/tasks/T08/20260916T025704Z/receipt.json` | 通过 | — | not_reviewed |
| R09 | 用户隔离、预热、代理热替换 | T09 | `src/agent/backends/sandbox_proxy.py`<br>`src/agent/backends/sandbox_manager.py` | `tests/acceptance/test_t09.py` | `artifacts/tasks/T09/20260918T021432Z/receipt.json` | 通过 | — | not_reviewed |
| R10 | CompositeBackend三路路由 | T10 | `src/agent/main_agent.py`<br>`src/agent/persistence/namespaces.py` | `tests/acceptance/test_t10.py` | `artifacts/tasks/T10/20260918T023549Z/receipt.json` | 通过 | — | not_reviewed |
| R11 | 技能渐进披露、播种与同步 | T10 | `src/agent/middlewares/skills_sync.py`<br>`src/skills/` | `tests/acceptance/test_t10.py` | `artifacts/tasks/T10/20260918T023549Z/receipt.json` | 通过 | — | not_reviewed |
| R12 | 主子Agent、YAML扩展、上下文隔离 | T11 | `src/agent/subagents/loader.py`<br>`src/agent/subagents/configs/` | `tests/acceptance/test_t11.py` | `artifacts/tasks/T11/20260918T021125Z/receipt.json` | 通过 | — | not_reviewed |
| R13 | write_todos、规划和重规划 | T11 | `src/agent/middleware_config.py`<br>`src/agent/main_agent.py` | `tests/acceptance/test_t11.py` | `artifacts/tasks/T11/20260918T021125Z/receipt.json` | 通过 | — | not_reviewed |
| R14 | 补充信息、审批、Command恢复 | T12 | `src/agent/approval/`<br>`src/agent/tools/hitl_tools.py` | `tests/acceptance/test_t12.py` | `artifacts/tasks/T12/20260918T023651Z/receipt.json` | 通过 | — | not_reviewed |
| R15 | SSE双流、子图事件、历史CRUD | T13 | `src/api_view/stream_adapter.py`<br>`src/api_view/api/chat.py` | `tests/acceptance/test_t13.py` | `artifacts/tasks/T13/20260918T020442Z/receipt.json` | 通过 | — | not_reviewed |
| R16 | Vue实际对话与两类中断UI | T14 | `frontend/src/App.vue`<br>`frontend/src/state/chat.ts`<br>`frontend/src/markdown.ts` | `tests/acceptance/test_t14.py` | `artifacts/tasks/T14/20260918T020655Z/receipt.json` | 通过 | — | not_reviewed |
| R17 | 智谱搜狗搜索 | T15 | `src/agent/tools/web_search.py` | `tests/acceptance/test_t15.py` | `artifacts/tasks/T15/20260918T020716Z/receipt.json` | 通过 | 通过 | not_reviewed |
| R18 | 图表MCP统一入口 | T15 | `src/agent/tools/chart_generator.py`<br>`src/skills/procurement/chart_params.md` | `tests/acceptance/test_t15.py` | `artifacts/tasks/T15/20260918T020716Z/receipt.json` | 通过 | 通过 | not_reviewed |
| R19 | 抓取、HTML转换、分析、报告下载 | T16 | `src/skills/procurement/procurement-analysis/scripts/build_report.py`<br>`src/skills/procurement/web-scraper/scripts/fetch_quotes.py`<br>`src/skills/procurement/web-content-fetcher/scripts/fetch_page.py` | `tests/acceptance/test_t16.py` | `artifacts/tasks/T16/20260918T020455Z/receipt.json` | 通过 | — | not_reviewed |
| R20 | 创建/下载、修复测试、分配、持久化、恢复 | T17 | `src/agent/skills/pipeline.py`<br>`src/agent/skills/store.py`<br>`src/agent/tools/assign_skill.py` | `tests/acceptance/test_t17.py` | `artifacts/tasks/T17/20260918T011551Z/receipt.json` | 通过 | — | not_reviewed |
| R21 | 四偏好、两历史字段、跨会话记忆 | T18 | `src/agent/memory/preferences.py`<br>`src/agent/middlewares/memory_update.py` | `tests/acceptance/test_t18.py` | `artifacts/tasks/T18/20260918T021226Z/receipt.json` | 通过 | — | not_reviewed |
| R22 | 自动/主动摘要、offload、完整历史 | T19 | `src/agent/middlewares/tools_summarization.py` | `tests/acceptance/test_t19.py` | `artifacts/tasks/T19/20260918T021238Z/receipt.json` | 通过 | — | not_reviewed |
| R23 | 全中间件矩阵、熔断、调用限制 | T19 | `src/agent/middleware_config.py`<br>`src/agent/middlewares/sandbox_breaker.py` | `tests/acceptance/test_t19.py` | `artifacts/tasks/T19/20260918T021238Z/receipt.json` | 通过 | — | not_reviewed |
| R24 | AsyncSubAgent独立协议服务 | T20 | `src/agent/async_tasks/service.py`<br>`src/api_view/api/async_tasks.py`<br>`infra/agent-protocol/analyst_graph.py` | `tests/acceptance/test_t20.py` | `artifacts/tasks/T20/20260918T020329Z/receipt.json` | 通过 | — | not_reviewed |
| R25 | 故障恢复与用户隔离集成 | T21 | `tests/acceptance/test_t21.py`<br>`tests/integration/test_recovery.py`<br>`tests/integration/test_isolation.py` | `tests/acceptance/test_t21.py` | `artifacts/tasks/T21/20260918T023358Z/receipt.json` | 通过 | — | not_reviewed |
| R26 | 可运行测试和能力审计 | T22 | `tests/acceptance/test_t22.py`<br>`scripts/coverage_report.py` | `tests/acceptance/test_t22.py` | `artifacts/tasks/T22/20260918T064721Z/receipt.json` | 通过 | — | not_reviewed |
| R27 | 真实模型完整演示 | T23 | `scripts/smoke_agent.py` | `tests/acceptance/test_t23.py` | `artifacts/tasks/T23/20260919T154444Z/receipt.json` | 通过 | 通过 | not_reviewed |
| R28 | 清洁启动和代码学习交付 | T24 | `docs/runtime/runbook.md` | `tests/acceptance/test_t24.py` | `artifacts/tasks/T24/20260921T143918Z/receipt.json` | 通过 | — | not_reviewed |

## V01-V22 场景

每个场景至少被一个任务的 acceptance 覆盖；具体断言在各任务的测试文件里。
T21 负责的是跨模块组合，因此 V04/V07/V09/V10/V11-V16 在它那里另有交叉验证。

| ID | 场景 | 覆盖任务 |
|---|---|---|
| V01 | seed-v1 库存查询 | T01, T02 |
| V02 | 创建 50 件 P001、25.50 | T03 |
| V03 | 无效数量、金额、停用供应商、无供货关系 | T03 |
| V04 | 同 key 并发/重放创建 | T03, T21 |
| V05 | 两次 version=1 修改 | T03 |
| V06 | 缺字段 -> 补充 -> 审批 -> approve | T12 |
| V07 | reject、错用户、过期 interrupt、修改参数复用授权 | T12, T21 |
| V08 | SSE 中文/JSON 跨 chunk、工具乱分片、重复片 | T13 |
| V09 | 审批页刷新/两个 tab 同时点击 | T12, T21 |
| V10 | 写成功但响应丢失，再重启/恢复 | T12, T21 |
| V11 | Mongo 重启、同用户新 thread、另一用户 | T07, T18, T21 |
| V12 | 沙箱两用户并发、同用户同时认领 | T09, T21 |
| V13 | 删除容器、重建 | T09, T21 |
| V14 | 恶意 ZIP、越界文件、宿主执行尝试 | T08, T17, T21 |
| V15 | 新技能坏脚本 -> 修复、Store 半写失败 | T17, T21 |
| V16 | 新技能发布、刷新、重启复用 | T17, T21 |
| V17 | 技能正文较大 | T10 |
| V18 | 人工偏好更新、闲聊、恶意网页偏好指令 | T18 |
| V19 | 摘要、offload、低预算、沙箱故障 | T19 |
| V20 | 报告、图表下载、其他用户获取 | T16 |
| V21 | async 启动/查询/更新/取消 | T20 |
| V22 | 清空演示数据后完整启动 | T24 |

## 已知缺口与待验收项

- **R27**（真实模型完整演示）由 T23 负责，当前**待验收**，未被标记为通过。
- **R28**（清洁启动与学习交付）由 T24 负责，当前**待验收**。
- 需要真实外部服务的 check（T06/T15/T23 的 live 模式）与确定性回归**分开统计**：
  确定性回归通过不等于 live 通过。
- 中间件矩阵契约要求 8 个槽位全部实现，实际以 `middleware_inventory()` 的自述为准。

