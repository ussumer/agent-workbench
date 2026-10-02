# 当前交接

## 状态

- 2026-09-16：**T00-T05 完成 → 里程碑 M0 达成**；另完成 **T07、T08、T09**。共 9 个任务 done，T06 blocked，T10-T24 pending。
- 已启动的本地服务：MongoDB（compose，27017）、OpenSandbox 控制服务（18080）、报价站（8080 之外，按需由测试拉起）。
- 沙箱容器残留：0（每个验收用例结束都做过核对）。
- `.env` 已创建：模型三件套已填并验证可用；智谱、ModelScope 仍缺凭据。
- T06 进行中（配置模块完成、模型已验证；CAP 矩阵与验收用例待写），**未登记 done**。

## 已完成任务

| 任务 | receipt | 检查结果 | 关键交付 |
|---|---|---|---|
| T00 | `artifacts/tasks/T00/20260916T030004Z/receipt.json` | acceptance 24 + java-build + frontend-build | 骨架、`uv.lock`、`package-lock.json`、`mvnw`、`gate.py`、`doctor.py` |
| T01 | `artifacts/tasks/T01/20260916T011614Z/receipt.json` | acceptance 30 | `seed-v1.json`、`expected-v1.json`、6 schema、24 契约样例 |
| T02 | `artifacts/tasks/T02/20260916T012144Z/receipt.json` | acceptance 21 + java-build | Java 目录/库存接口、服务令牌、分页与错误信封 |
| T03 | `artifacts/tasks/T03/20260916T012747Z/receipt.json` | acceptance 22 + java-build | 订单事务、乐观锁、幂等账本（另 36 JUnit 含并发） |
| T04 | `artifacts/tasks/T04/20260916T013441Z/receipt.json` | acceptance 26 | FastMCP 八工具 + HMAC 授权票据（另 23 契约用例） |
| T05 | `artifacts/tasks/T05/20260916T014708Z/receipt.json` | acceptance 18 | 报价站（两种 DOM）+ 跨平台可复现技能 ZIP |
| T07 | `artifacts/tasks/T07/20260916T015112Z/receipt.json` | acceptance 24 | 三类持久化分离 + 用户隔离（另 5 集成用例） |
| T08 | `artifacts/tasks/T08/20260916T025704Z/receipt.json` | acceptance 24 | OpenSandbox 执行环境 + DeepAgents 后端适配（另 12 集成用例） |
| T09 | `artifacts/tasks/T09/20260916T041041Z/receipt.json` | acceptance 28 | 沙箱池、稳定代理、登记重连与恢复（另 8 生命周期 + 12 后端集成用例） |

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

## 已实测的关键行为

- 预警集合 P001/P003/P004、建议量 42/15/30 与 `expected-v1.json` 一致；换种子后预警随之变化；同一库重启不重置。
- 订单 50×25.50 = 1275.00，改 60 件 = 1530.00 且 version=2，库存仍为 8；两线程同 key 只产生一单。
- MCP 工具面恰好八个，schema 不含 actor/token/operation_id；无授权写被拒，改参数/换用户/过期/篡改签名均被拒。
- 报价站最低价合计 2553.00；下载的 ZIP 解压后真实运行脚本得 1533.00；宿主机与 Linux 容器产出的 ZIP 哈希一致。
- Mongo checkpoint 由独立进程读回；display message 重复投递不重复；同 request_id 不同正文冲突。
- 沙箱内真实执行命令、读写/上传下载文件；**超时明确报 124**；宿主哨兵文件不变；`/var/run/docker.sock` 不可见；容器内可抓报价站。
- 池：预热 1 个并在认领后补充；同一用户跨 thread 复用同一 proxy 对象；不同用户不同容器且互相读不到文件；预热认领在并发下不重复。
- 恢复：容器被 `docker rm -f` 之后 health 变 False，`recover` 生成新容器且 **proxy 对象身份不变**、generation +1、规则文件重新上传；3 线程同时发现故障只重建一次（新建容器数 == 1）。
- 崩溃/重启：`detach()` 后新 manager 通过 Mongo 登记重连同一沙箱；上个进程遗留的预热容器由 `cleanup_orphans()` 按项目标记回收。
- 失败隔离：recovery 失败时 registry 置 FAILED、proxy 不发布半初始化容器、旧的（已死）世代保留；被 replacement 拦下的调用不产生任何副作用文件。
- 泄漏核对：验收套件跑完 `docker ps -a` 中本项目沙箱容器为 **0**。

## 重要偏离与纠错（需用户知晓）

1. **OpenSandbox 客户端换包**：T00 锁定的 `opensandbox-sdk` 与本服务端完全不兼容（认证头、生命周期路径、文件/命令端点三处都不同，且无可用版本组合）。T08 已改正为 **`opensandbox` 0.1.16**，并同步更新 `pyproject.toml`、T00 的锁定导入断言与 `docs/runtime/versions.md`。T00 gate 已复跑通过。
2. ~~新增错误码 `INACTIVE_PART`（422）~~ **已按用户决定并入 `UNSUPPORTED_PART`**（2026-09-16）：停用物料与「无供货关系」对外共用一个错误码，消息仍区分两种原因。改动落在 `OrdersService`、`OrderApiTest`、`test_t01.py`、`test_t03.py`、`fixtures/json-cases.json`。
3. **Mongo Store 包名**：`langgraph-store-mongodb` 0.4.0，不在 `langchain-mongodb` 内（T00 已核实）。
4. **沙箱控制服务无 api_key**：上游 SDK/服务端认证头不一致所致，仅因绑定 127.0.0.1 且显式 `OPENSANDBOX_INSECURE_SERVER=YES` 才可接受。详 `infra/sandbox/README.md`。
5. **本机环境适配**（非方案变更）：`JAVA_HOME` 重新解析、`.venv` 用具体解释器创建、pytest 临时目录 symlink 容忍、`./mvnw` → `mvnw.cmd`。详 `docs/runtime/versions.md`。

## 未验证 / 阻塞项

- **模型凭据已就绪并已验证**：`.env` 已按 `.env.example` 生成，填入 `MODEL_API_KEY` / `MODEL_BASE_URL=https://api.deepseek.com/v1` / `MODEL_ID=deepseek-chat`。真实调用（`ChatOpenAI` 与直接 HTTP 两条路径）均返回中文正确应答，provider 回报 `model_name=deepseek-flash`——即讲义 DSV4.1Flash 简称对应的实际服务端模型；gate 的 `model` 能力判为 satisfied。密钥值未写入任何文档、receipt 或索引。
- **智谱搜索 / ModelScope 图表仍缺凭据**：本机找不到任何可用来源，保持为空，T15/T23 会被 gate 阻塞；不暗换引擎。
- **T06 尚未完成**：`src/agent/env_utils.py`、`src/agent/config.py` 已完成并通过 lint；`langchain-openai` 已入锁并写入 T00 的锁定导入断言，T00 复跑通过。CAP-01..08 的实测矩阵与 `tests/acceptance/test_t06.py`（≥8 用例，live 模式）尚未编写，因此 T06 仍为 blocked，不能登记 done。
- **CAP-08 的 Agent Protocol 服务选型未定**：官方 `langgraph-api` 0.14.1 可解析，但会把锁内 `protobuf`（7.36.1→6.33.6）、`opentelemetry-*`（1.44.0→1.42.1）、`sse-starlette`（3.4.11→3.3.4）一起降级，故未纳入依赖。可选：(a) 接受降级并采用官方服务；(b) 用独立虚拟环境托管官方服务，保持主锁不变；(c) 以 FastAPI 实现 Agent Protocol 最小子集，用官方 `langgraph_sdk` 客户端验证。需要讨论后再定。

## 下一步

`python scripts/plan_guard.py next` 当前给出 **T09**（用户沙箱池、稳定复用和恢复），T06 仍显式 blocked。

```bash
python scripts/plan_guard.py packet T09
```

## 需要用户决策/配置

1. 仍需补的外部凭据：`ZHIPU_API_KEY`、`MODELSCOPE_MCP_URL` / `MODELSCOPE_API_TOKEN`（模型已就绪）。
2. CAP-08 的 Agent Protocol 服务选型（三选一，见「未验证/阻塞项」第 4 条）——它同时决定 T20 的实现方式。

## 后续交接模板

每次任务完成/暂停时替换当前现场，保留必要历史链接：当前 task/status；已完成步骤；文件入口；
真实检查命令和 receipt；未过断言及根因；服务 PID/端口或容器；下一条命令；是否需用户配置/决策。
