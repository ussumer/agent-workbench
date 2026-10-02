# 运行时版本组合（T00 resolved）

状态标记：**resolved** 表示由包管理器解析并锁定、本机可安装、关键 import 已核对。
**verified** 表示 T06 已用真实模型/服务实测。T06 之前只标 resolved。

## 本机工具

| 工具 | 版本 | 来源 / 命令 |
|---|---|---|
| Python（项目） | 3.12.14 | uv 管理解释器 `%APPDATA%\uv\python\cpython-3.12.14-windows-x86_64-none` |
| uv | 0.12.13 | `C:\Users\34114\.local\bin\uv.exe` |
| Node | 20.18.1 | `node --version` |
| npm | 10.8.2 | `npm --version` |
| JDK | 21.0.8 (OpenJDK) | `javac -version`（存在，可编译） |
| Maven | 未安装系统 mvn | 项目使用 `erp/mvnw` Maven Wrapper |
| Docker | 29.2.1 | `docker --version` |
| Git | 2.51.1 | `git --version` |

注意：`java` 存在不等于 JDK 可构建；本项目以 `javac` 与 `mvnw verify` 共同证明。

## Python 依赖（uv.lock 冻结，107 包）

| 包 | 版本 | 关键 import（已核对签名） |
|---|---|---|
| deepagents | 0.7.14 | `from deepagents import create_deep_agent, AsyncSubAgent` |
| langchain | 1.4.0 | 由 deepagents 传递锁定 |
| langchain-core | 1.6.3 | `langchain_core.language_models.chat_models.BaseChatModel` |
| langgraph | 1.2.11 | `langgraph.graph.StateGraph`、`langgraph.types.Command/interrupt` |
| langgraph-checkpoint | 4.2.0 | 由 langgraph 传递锁定 |
| langgraph-checkpoint-mongodb | 0.5.0 | `from langgraph.checkpoint.mongodb import MongoDBSaver` |
| langgraph-store-mongodb | 0.4.0 | `from langgraph.store.mongodb import MongoDBStore` |
| langchain-mongodb | 0.12.0 | 由 store/checkpoint 插件传递 |
| langgraph-sdk | 0.4.4 | Agent Protocol 客户端（`get_client`/`get_sync_client`） |
| langchain-openai | 1.6.2 | **T06 新增**。模型端点是 OpenAI 兼容协议，配置只有通用 `MODEL_BASE_URL`/`MODEL_ID`，因此用该客户端驱动；OpenAI 兼容客户端默认不装，`langchain` 1.x 只带 anthropic |
| mcp | 1.30.0 | `from mcp.server.fastmcp import FastMCP`、`mcp.ClientSession` |
| langchain-mcp-adapters | 0.3.2 | `from langchain_mcp_adapters.client import MultiServerMCPClient` |
| opensandbox | 0.1.16 | `from opensandbox.sync import SandboxSync`（同步）、`from opensandbox import Sandbox`（异步） |
| opensandbox-server | 0.2.3 | 控制服务（dev 组）；`.venv\Scripts\opensandbox-server.exe` |
| fastapi | 0.141.1 | `from fastapi import FastAPI` |
| uvicorn | 0.53.0 | ASGI 服务器 |
| pydantic | 2.13.5 | 校验 |
| pymongo | 4.17.0 | Mongo 驱动（由插件约束 `<4.18`） |
| httpx | 0.28.1 | 异步 HTTP |
| pytest | 8.4.2 | 测试 |
| ruff | 0.14.14 | lint |
| mypy | 1.20.2 | 类型检查 |

## 记录的关键签名（T07/T08/T10/T11 直接使用）

```text
create_deep_agent(model=None, tools=None, *, system_prompt=None, middleware=(),
  subagents=None, skills=None, memory=None, permissions=None, backend=None,
  interrupt_on=None, response_format=None, state_schema=None, context_schema=None,
  checkpointer=None, store=None, debug=False, name=None, cache=None)

AsyncSubAgent  -> deepagents.AsyncSubAgent
CompositeBackend(default: BackendProtocol | StateBackend,
                 routes: dict[str, BackendProtocol], *, artifacts_root: str = "/")
StoreBackend(*, namespace: Callable[[Runtime[Any]], tuple[str, ...]],
             store: BaseStore | None = None)

MongoDBSaver(client: MongoClient, db_name="checkpointing_db",
  checkpoint_collection_name="checkpoints",
  writes_collection_name="checkpoint_writes", ttl=None, serde=None, **kwargs)
MongoDBStore(collection: Collection, ttl_config=None, index_config=None,
  auto_index_timeout=15, query_model=None, rerank_config=None, **kwargs)
MongoDBStore.from_conn_string(conn_string=None, db_name="checkpointing_db",
  collection_name="persistent-store", ttl_config=None, index_config=None,
  **kwargs) -> Iterator[MongoDBStore]

FastMCP(name=None, ..., streamable_http_path="/mcp", host="127.0.0.1", port=8000, ...)
MultiServerMCPClient(connections: dict[str, ...] | None = None, *,
  callbacks=None, tool_interceptors=None, tool_name_prefix=False,
  handle_tool_errors=True)

ConnectionConfigSync(domain="127.0.0.1:18080", protocol="http", api_key=None,
  request_timeout=timedelta, ...)            # get_base_url() -> http://<domain>/v1

SandboxSync.create(image, *, timeout=timedelta(600), ready_timeout=timedelta(30),
  env=None, metadata=None, resource_requests=None, platform=None,
  network_policy=None, credential_proxy=None, entrypoint=None, volumes=None,
  connection_config=None, ...) -> SandboxSync      # 同步；Sandbox.create 同参数但为协程

sandbox.commands.run(command, *, opts=RunCommandOpts, handlers=None) -> Execution
  Execution(id, exit_code, error, complete, logs.stdout[].text, logs.stderr[].text)
    - 正常退出：exit_code=0..N；非零退出仍返回 Execution，error.traceback=['exit status N']
    - 被信号杀死：exit_code=-1，error.name='CommandExecError'，traceback=['signal: killed']
  RunCommandOpts(background, working_directory, timeout, uid, gid, envs)

sandbox.files.read_file / read_bytes / write_file / write_files / create_directories /
  delete_files / delete_directories / move_files / get_file_info / list_directory /
  search / replace_contents / set_permissions
  EntryInfo(path, entry_type, mode, owner, group, size, modified_at, created_at)
    - mode 以八进制**数字**表示（444 表示 0o444），传 0o444(292) 会被服务端拒绝
  WriteEntry(path, data, mode, owner, group, encoding)
  DirectoryListEntry(path, depth) / SearchEntry(path, pattern)
  ContentReplaceEntry(path, old_content, new_content)

sandbox.kill() / renew() / pause() / resume() / get_info() / is_healthy() / get_endpoint()
```

T11 追加（主 Agent 装配与中间件）：

```text
create_deep_agent(*, model, tools=(), system_prompt=None, middleware=(), subagents=(),
  interrupt_on=None, backend=None, checkpointer=None, store=None, memory=None, skills=None,
  permissions=None, ...) -> CompiledStateGraph
    基座中间件顺序：Filesystem -> SubAgent -> Summarization -> PatchToolCalls
    （TodoList 需自行加入；AsyncSubAgentMiddleware 仅在给出异步子代理时安装）

deepagents.SubAgent（TypedDict）: name、description、system_prompt、tools、
  model（**缺省即继承父模型；显式 None 会抑制回退并报错**）、middleware、interrupt_on

langchain.agents.middleware.TodoListMiddleware
    -> 提供 write_todos；state_schema = PlanningState，state["todos"] 为
       list[{content, status}]，status ∈ {pending, in_progress, completed}
       write_todos 每次**整体替换**清单，同一轮多次调用会被拒绝

AgentMiddleware 关键 hook（T11 用到 wrap/before 两侧）：
    before_agent(state, runtime) / abefore_agent(...)
    wrap_tool_call(request: ToolCallRequest, handler) -> ToolMessage | Command
    awrap_tool_call(request, handler: async) 同形
```

## T06 实测结论（依赖自此标记 verified）

CAP 矩阵全部由真实模型与真实本地服务产出，无 fake。逐项证据见 `artifacts/tasks/T06/20260916T054330Z/`。

| 依赖族 | 结论 | 实测证据 |
|---|---|---|
| 模型客户端 `langchain-openai` + 真实模型 | **verified** | CAP-01/02/03：中文应答、两轮上下文（2 → ×10 = 20）、`add(17,25)` 工具调用与 `ToolMessage` 内容 42、工具参数 13 个原始分片拼接成 `{"a": 17, "b": 25}` |
| LangGraph v2 streaming | **verified** | CAP-03/04：`stream(..., version="v2")` 的 part 为 `{type, ns, data, interrupts}` |
| DeepAgents 子代理与 HITL | **verified** | CAP-04/05：`task` 委派只读子代理；子代理内 `interrupt_on` 触发中断，同一 thread 上 approve 与 reject 均可恢复 |
| `langgraph-checkpoint-mongodb` / `langgraph-store-mongodb` | **verified** | CAP-06：写入与读回在**两个独立进程**（pid 12016 / 2736）完成，checkpoint_id 一致，Store 条目读回写入值 |
| OpenSandbox 执行 | **verified** | CAP-07：真实容器 `code-interpreter:v1.1.0` 执行返回 0，上传下载往返 sha256 一致 |
| Agent Protocol + `AsyncSubAgent` | **verified** | CAP-08：独立服务 `/ok` 健康，图 `echo` 被发现，同步与异步 `runs.wait` 均返回任务结果 |
| DeepAgents `create_deep_agent` 构造面 | **verified** | 签名记录（`tests/fixtures/t06-signatures.json`）含 `subagents` / `interrupt_on` / `backend` / `checkpointer` / `store` |

### 两处实测修正了文档/直觉的假设

1. **按 `ns` 区分「子事件」取决于 `stream(subgraphs=True)`**。不传该参数时，所有 part 都是根命名空间
   （实测对比：同一张图的 namespaced part 数量为 0），子代理输出会被误记到主 Agent 名下；传入后，
   `task` 委派的子代理落在 `tools:<task-id>`，作为节点使用的编译子图落在 `<node>:<task-id>`。
   初版实验曾推断「task 委派永远没有 ns」，实测证伪。
2. **`AsyncSubAgent` 是 TypedDict**，字段用下标访问（`agent["url"]`），不是属性。`SubAgent` 亦为字典形态。

## T11 实测结论（子 Agent 与中间件）

| 事项 | 结论 | 依据 |
|---|---|---|
| `create_deep_agent` 不装待办清单 | **必须自行加入 `TodoListMiddleware`** | deepagents 0.7.14 的基座是 `FilesystemMiddleware` + `SubAgentMiddleware` + `SummarizationMiddleware` + `PatchToolCallsMiddleware`；不加 `TodoListMiddleware` 时 `write_todos` 不是合法工具（实测报 `write_todos is not a valid tool`），`state` 也没有 `todos` 键。加入后 `state["todos"]` 持久化 |
| `SubAgent` 的 `model` 必须是**缺省**而非 `None` | 传 `model=None` 会**抑制**父模型回退 | `deepagents/graph.py` 用 `spec.get("model", parent_model)` 取值；键存在且为 `None` 时不回退，`create_summarization_middleware` 随即抛 `TypeError` |
| 子代理上下文隔离 | **verified** | 子代理首轮上下文恒为 2 条（system + task），父代理的无关文本不出现在其中 |
| `task` 工具返回子代理最后一条文本 | 结构化返回靠**提示词约定**，不靠框架强约束 | `SubAgentReturn.parse` 缺少字段时报错并列出缺项，避免空结果伪装成功 |
| 主 Agent 持有 ERP 工具会绕过委派 | **已用真实模型证伪分工** | 真实模型（`deepseek-chat`）在持有读工具时直接自行查询、不写 todo、不委派。改为「子代理声明的工具自动从主 Agent 摘除」后，同一提示下 3~4 条 todo + `task` 委派 + 子代理内 MCP 调用全部出现 |

## T12 实测结论（HITL 审批）

| 事项 | 结论 | 依据 |
|---|---|---|
| 框架 HITL 中断载荷 | `{"action_requests":[{name,args,description}], "review_configs":[{action_name,allowed_decisions}]}`，中断带 `interrupt.id` | 实测捕获；**`action_requests` 里没有 `tool_call_id`**，这是本任务最重要的发现 |
| `Command(resume=...)` 必须有 checkpointer | 否则 `RuntimeError: Cannot use Command(resume=...) without checkpointer` | 实测 |
| `allowed_decisions` 可按工具配置 | `interrupt_on={"order_create":{"allowed_decisions":["approve","reject"]}}` 生效，载荷里回显配置值 | 实测；默认是 `["approve","edit","reject","respond"]` |
| approve 只执行一次 | 批准后恢复，写工具执行 1 次；对同一已完成 run 再次 resume **不会**重放写 | 实测计数 |
| reject 零写 | 工具结果为 `User rejected the tool call for ...`，写调用计数 0 | 实测 |
| `SubAgent` 支持内层 middleware | `SubAgent(..., middleware=[...])` 让闸门覆盖子代理发出的写调用 | 实测 |
| 参数变更防护 | 批准后参数不一致 → `PARAMETERS_CHANGED`；同一批准重放复用同一 operation_id | 实测 |

## T15 实测结论（真实搜索与图表）

| 事项 | 结论 | 依据 |
|---|---|---|
| 智谱搜索端点 | `POST https://open.bigmodel.cn/api/paas/v4/web_search`，`Authorization: Bearer <key>` | 探测确认；指南页只给参数不给路径 |
| 智谱 key 格式 | `{id}.{secret}`，实测 **49 字符、含一个点** | 32 字符无点的那版被所有端点 401 拒绝——那是 Key ID，不是 key |
| **`count` 参数随引擎而变** | `search_pro_sogou` **忽略**它（请求 2 返回 50 条）；`search_std` **认**它（要 5 给 5、要 3 给 3） | 实测两档；上限因此不能交给引擎（同一份代码在不同档位上行为不同），仍由调用方 clamp。见「搜索引擎更换」 |
| 搜索响应字段 | `search_result[].{title,link,content,media,icon,publish_date,refer}` | 实测；`link` 是"可引用"的判据 |
| 智谱失败码 | `401`/`1000`/`1001`=鉴权；`429`+`1113`=余额或资源包；`403`=未开通 | 实测三种都遇到过 |
| ModelScope 图表传输 | **SSE**（`mcp.client.sse.sse_client`），`Authorization: Bearer` 头 | 实测握手成功 |
| 图表工具数 | **18**（方案预期 26） | 实测 `list_tools` |
| 图表返回形态 | **base64 图片**（`content[0].type == "image"`），不是 URL | 实测；按 URL 找会误判为"没有可下载地址" |
| 图表必需类型 | `generate_bar_chart` / `generate_line_chart` / `generate_pie_chart` 均在 | 实测，三张都出真 PNG |

### 一个会让朴素包装器静默踩坑的 schema 不一致

`list_tools()` 对 `generate_line_chart` 声明的 `required` 只有 `["data"]`，**但每行必须带 `time`**：

```
传 [{"category": "刹车片", "value": 8}] → isError，path ["data", 0, "time"] Required
传 [["刹车片", 8]]                      → isError，expected object, received array
传 [{"time": "2026-09-01", "value": 8}] → 成功，PNG 45323 字节
```

柱状图与饼图要的是 `{"category", "value"}`。所以「把同一份数据转发给所有图表类型」的包装器会
**通过柱状和饼图、在折线图上运行时失败**——而且失败发生在远端，看不出是本地的形状错了。
`chart_generator.py` 因此按图表族（category 族 / time 族）分别构造 data，并把这一点写进了
`src/skills/procurement/chart_params.md`。

### 一处如实标注的缺口（未编造）

任务要求"保存目录与**讲义 26 项概念**的对应差异，不编造缺失类型"。**那份 26 项清单不在本仓库内**
（全仓库检索只有任务文本提到这个数字）。因此只能单向记录：真实端点是 **18** 项，与 26 项具体差在
哪 8 项**需要讲义原文才能对齐**，我没有猜也没有用别名凑数。`chart_params.md` 里写明了这个限制。

## T19 实测结论（压缩、熔断与预算）

| 事项 | 结论 | 依据 |
|---|---|---|
| 主动压缩工具来自框架 | `deepagents.middleware.create_summarization_tool_middleware(model, backend)` → `SummarizationToolMiddleware`（hooks：`before_model`/`wrap_model_call`/`before_agent`/`after_agent`） | 实测签名 |
| 预算用框架自带的 | `ModelCallLimitMiddleware(thread_limit, run_limit, exit_behavior)` 与 `ToolCallLimitMiddleware(tool_name, thread_limit, run_limit, exit_behavior)` | 实测签名；`exit_behavior='end'` 是正常终止，`'error'` 会抛错并丢掉本轮产出 |
| `create_summarization_tool_middleware` 不在顶层导出 | 要从 `deepagents.middleware` 导入，`from deepagents import ...` 会 ImportError | 实测 |

### 三个真实 bug

1. **`RunBudget.started_at` 用了真实时钟。** 注入的 `clock` 只在 `elapsed()` 里生效，起始点却来自
   默认工厂 `time.monotonic`，于是「已用时间」是两个时间源相减，测试里得到 34314 秒这种数字。
   改为 `__post_init__` 用注入的时钟取起始点。
2. **T11 的中间件清单断言随矩阵补全而失效**（两次）：先是 `conversation_summary` 从「未实现」变成
   「已实现」，再是 `len(built) == 槽位数` 不再成立——一个槽位可以展开成多个中间件（预算是两个，
   压缩槽是工具 + 卸载器）。断言改为「至少覆盖槽位数」并把展开的槽位点名。
3. **T11 的中间件上下文缺少 T19 新槽位需要的键**（`model`/`backend`/`workspace_writer`）。
   这正是 `middleware_inventory` 的 `buildable` 字段存在的意义：缺上下文时如实标为不可构建，
   而不是静默跳过或直接抛错。

## T14 实测结论（前端与浏览器交互）

| 事项 | 结论 | 依据 |
|---|---|---|
| **前端测试需要 Node ≥ 20.19** | 本机默认 Node 是 **v20.18.1**，低于 `frontend/package.json` 声明的 `^20.19.0 \|\| >=22.12.0` | jsdom 29.1.1 → html-encoding-sniffer 6.0.0（CJS）`require()` 了 ESM-only 的 `@exodus/bytes`；Node 只在 20.19 / 22.12 起允许 `require()` ESM。低于该版本时 vitest 在**加载环境阶段**就崩，报 `ERR_REQUIRE_ESM`，错误指向依赖而不是运行时，极易误判 |
| 本机可用的合规运行时 | `~/.workbuddy/binaries/node/versions/22.22.2/node.exe` | 用它跑 `node node_modules/vitest/vitest.mjs run`：46 个用例全通过 |
| SSE 分帧必须在客户端自己做 | `EventSource` 不支持请求体，接口是 POST + fetch ReadableStream | 实测 |
| CSS 压缩会改写媒体查询 | `@media (max-width: 900px)` 被压成 `@media (width<=900px)`（现代区间语法） | 构建产物实测；断言不能写死旧写法 |
| 打包后 API 路径不成整串 | 客户端用 `` `${API_BASE}/chat/stream` `` 拼接，压缩产物里没有 `/api/chat/stream` | 构建产物实测 |

### 一条环境适配（非方案变更）

**Node 版本**：T00 已把 `engines` 定为 `^20.19.0 || >=22.12.0`，但本机 PATH 上的 `node` 是
20.18.1。差别只在 `require(esm)` 这一项能力上，而它恰好是 jsdom 能不能启动的前提，所以前端
单测在本机默认 Node 下**一个都跑不起来**（构建不受影响，`vite build` 正常）。跑前端用例时需指向
已安装的 22.22.2。

## T13 实测结论（SSE 与接口）

| 事项 | 结论 | 依据 |
|---|---|---|
| `graph.astream` 的契约入口 | `stream_mode=["messages","values"]` + `subgraphs=True` + `version="v2"` 全部被接受，part 形如 `{type, ns, data, interrupts}` | 真实 `create_deep_agent` 图上实测 |
| 工具参数是**单字符分片** | T06 抓到的 13 个分片拼成 `{"a": 17, "b": 25}`；必须按 `tool_call_id` 累加后再解析 | `tests/fixtures/stream/t06-v2-stream.json` 回归 |
| `ns` 给的是 **task id 而非子 Agent 名** | `tools:<task-id>`；名字只能从 `task` 调用的 JSON 参数里取（分片拼完才有） | 实测 |
| 消息 `content` 可能是块列表 | `str(content)` 会打印 Python repr——看起来成功其实是错的 | 实测 |
| `HumanInTheLoopMiddleware` 的 interrupt 与 `__interrupt__` | v2 part 的 `interrupts` 字段与 `state.values["__interrupt__"]` 都能拿到，`interrupt.id` 稳定 | 实测 |

### 三个真实 bug（都是"看起来能跑"的那种）

1. **run_id 生成了两次。** 路由给预约用的 run_id 和 `RunRegistry` 内部生成的不是同一个，
   于是 `update_run_status` 匹配不到任何文档、静默返回 False。表现是刷新后状态永远停在
   `running`——一个不会抛异常的错，只有主动查状态才发现。
2. **`done` 先于状态落定发出。** 原来先 publish `done` 再释放 run，客户端收到 `done` 立刻刷新
   会读到内存里"仍在运行"的句柄。改为**先落库、先释放、最后发 `done`**。
3. **展示消息按角色编号。** user 给 0、assistant 给 1，单轮测试是过的，多轮就变成"所有提问在前、
   所有回答在后"。改为按 run 在 thread 中的位置编号（`2N` / `2N+1`）。

### 一处被框架形状推翻的设计假设

最初把「审批」与工具调用用 `tool_call_id` 绑定，理由是这样最精确。实测发现**框架的 HITL 中断
根本不携带 `tool_call_id`**（只有 name/args/description），字段恒为空；同时 `interrupt_id` 只在
thread 内唯一。两者叠加使 T07 遗留的 `(interrupt_id, tool_call_id)` 唯一索引让**每一条**待审动作
互相冲突。改为按「用户回答的那一次 interrupt」绑定：`(owner_user_id, thread_id, interrupt_id)`
唯一，恢复运行时由 API 把 `approved_interrupt_id` 注入 run config。这不只是迁就框架——它同时
保留了「同一 thread 内两笔内容完全相同的独立采购必须可区分」这一契约要求，而按 payload hash
匹配做不到。旧索引已由 `drop_undeclared_application_indexes()` 清理。

## T16 实测结论（技能脚本、产件与下载）

| 事项 | 结论 | 依据 |
|---|---|---|
| 框架把 run config 注入工具的条件 | 注解必须**恰好是 `RunnableConfig`** | 实测三种写法：`config: RunnableConfig`（无默认值）注入且不进 schema；`config: RunnableConfig \| None = None` **不注入**且 `config` 出现在工具 schema 里；`config: RunnableConfig = None` 也注入。带默认值不影响，**联合类型会破坏识别** |
| 子代理内部的工具同样能读到作用域 | verified | 真图上父工具与子代理工具都拿到 `owner_user_id`/`thread_id`；子代理内另有 `ls_agent_type='subagent'` |
| `ToolRuntime` 不可用作工具参数 | 该版本会抛 `PydanticInvalidForJsonSchema` | `langchain.tools.ToolRuntime` 存在但无法生成 schema，只能用 `RunnableConfig` |
| 路径越界必须**先归一化再判前缀** | `posixpath.normpath` 之后才比对 | `/workspace/../etc/passwd` 归一化为 `/etc/passwd` 才被拒；只做前缀匹配时它会通过。`/workspaces/x` 也不会被 `/workspace` 误放行（兄弟目录共享前缀） |
| `require_owner` 的 `X-Demo-User` 头**从未生效** | 已修 | 它把 `demo_user` 声明成 FastAPI `Header(...)` 参数，但函数是被路由体**当普通函数调用**的，参数永不注入，拿到的是 `Header(None)` 对象本身（truthy），于是无 cookie 的请求报 `unknown demo user Header(None)`。没有任何测试发过该头，所以一直没被发现 |
| 技能脚本的 stdout 编码 | 必须显式 UTF-8 | Windows 控制台默认代码页会把 stdout 的 JSON 编成 GBK 字节，按 UTF-8 解析的调用方直接崩。沙箱内是 no-op |
| 模板不能自我说明 | 模板文件里写占位符说明表会被 `str.replace` 一起替换 | 报告生成改用纯模板，占位符说明移到 SKILL.md |

## T17 实测结论（技能发布与恢复）

| 事项 | 结论 | 依据 |
|---|---|---|
| 发布指针的条件更新落在哪 | **必须在 MongoDB**，不能放 Store | langgraph Store 只有 `put`/`search`，没有 compare-and-swap；先读后写会通过功能测试却输在并发上。契约 `storage-sandbox.md` 点名的 `skill_versions`/`skill_assignments` 两个集合正是这个原因 |
| 「没有指针」也必须是被断言的条件 | `expected_revision=None` 要用 **insert + 唯一索引**，不能用 upsert 的 find-and-update | 实测：`find_one_and_update(query, upsert=True)` 在已有行时**会匹配并覆盖**，等于把"我是第一个"当成了假设 |
| 工具方法签名必须**恰好**是 `RunnableConfig` | `assign_skill` 沿用 T16 的结论 | `RunnableConfig \| None = None` 不注入且 `config` 进入 schema，等于要求模型提供 owner |
| 沙箱内 `curl` 的 URL 与 allow-list 的粒度 | URL 用沙箱可达地址，allow-list 存**主机名**（不含端口） | `urlsplit(...).hostname` 会剥掉端口；用 `host:port` 做白名单永远匹配不上。且压缩包是容器内下载的，用 `127.0.0.1` 会连不上 |
| 冒烟必须双向 | 示例输入 exit 0 **且**坏输入 exit != 0 | 只测成功路径等于没测失败路径；一个把乱数据算下去的脚本比一个直接崩的更危险 |
| 恢复的短路条件 | assignment revision + 容器内 marker **同时**匹配 | marker 在容器里，所以重启 API 不必重下全部文件，而**重建容器**（marker 消失）必然触发全量恢复 |
| 一处失败的代价 | 一个坏技能会让该 owner 的短路永不生效 | 只有全部成功才写 marker（否则会让下次跳过这次没写成的文件）。代价是每轮重扫重验——是性能问题不是正确性问题，已记在代码注释里 |
| 空白 scope | `"   "` 必须走 SCOPE_REQUIRED 而不是 SCOPE_UNKNOWN | 先 strip 再判；否则调用方被告知"值不认识"，而它其实根本没被提供 |

## T20 实测结论（Agent Protocol 与后台任务）

| 事项 | 结论 | 依据 |
|---|---|---|
| 独立服务环境早就就绪 | `.venv-agent-protocol/`（langgraph-cli 0.4.31 + langgraph-api 0.14.1 + runtime-inmem 0.34.1），**故意不进 uv.lock** | `langgraph-api` 锁 `grpcio<1.82` 而 `opensandbox-server` 要 `>=1.83`；理由写在 `pyproject.toml:61-66`。主环境没有这些包，实测确认 |
| 服务里**没有模型客户端** | 环境只有 langgraph / langchain-core / httpx 等 | 这决定了后台分析图的形态：它是真实的 LangGraph，但分析是确定性的，不去伪造一个模型步骤 |
| `AsyncSubAgent` 的判别式 | `graph_id` 字段 | `deepagents/graph.py` 按有无所 `graph_id` 把子代理分流到 `AsyncSubAgentMiddleware`；launch/check/update/cancel/list 五个工具由框架生成 |
| 服务对不认识的 run 抛什么 | `langgraph_sdk.errors.NotFoundError`，`status_code == 404` | 实测。据此把「服务不认识这个 run」与「调用失败」分开：前者报 `lost`，后者保留最后已知状态——两者都**不能**变成 completed |
| 开发服务器重启会丢 run | verified | 契约明说「不承诺无损续跑」；`lost` 就是这句话的可执行版本 |
| SDK 不能改写已运行的输入 | `runs.create` 只能在同 thread 起新 run | 所以 update 记录为 `handling="followup_run"`，并把状态切到新 run；谎称改写了原输入是契约点名禁止的 |
| 主进程是唯一沙箱持有者 | 新增 `/internal/sandbox/operations`（代码此前**零匹配**） | 契约要求；实现是白名单三操作 + 内部 token + 路径边界，不是 manager 透传 |
| 只读是两处独立强制 | 图的 `WRITE_TOOLS = ()`，主进程再拒一次 `order_create` | 后台线程没人看着，靠提示词约束不够 |

## T21 实测结论（故障联调与隔离审计）

| 事项 | 结论 | 依据 |
|---|---|---|
| 共享可变状态审计（第 3 步） | **未发现缺陷** | `src/` 下无模块级 `= {}` / `= []`；MCP 侧身份是 `ContextVar` + `caller_scope` 的 try/finally reset，`current_caller()` 未绑定时**抛错**而不是默认为某人。两条纪律都已被测试钉住（含异常路径与四路并发） |
| V10 响应丢失后重试 | 同 `operation_id` 取回**同一张订单**；跨 owner 用同一个 operation_id 得到**两张不同订单** | 幂等账本按 owner 记账，作用域过宽会让一个人的重试消掉另一个人的单 |
| V04 并发重试 | 4 线程同 key 并发，**重复三轮**每轮都只产生 1 张订单 | 一次通过的竞态测试不足以说明没有竞态 |
| V07 票据绑定 | 批准 50 件后改成 9999 件会被拒（票据绑定 canonical 字节的摘要） | `payload_sha256` 来自与网关同一个 canonical builder |
| V09 两标签页 | 并发 approve 恰好一个成功、一个拿到拒绝；中断终态为 approved | 条件更新 + 唯一索引 |
| V13/V16 容器副本消失 | 删掉 `/skills/users` 与 revision marker 后从 Store 恢复，且恢复出来的脚本**真的能跑出 1533.00** | "文件在" 比 "能执行" 弱，所以两条都断言 |
| V15 半写 | 删掉被 manifest 声明的文件后 `verify_manifest` 抛错；版本仍不可分配 | 读回校验是 persisted 的含义 |
| 故障复现稳定性 | 并发写重试与"取消后并发轮询"各跑 3 次，结果完全一致 | 不靠重试消除失败样本 |
| 一处被纠正的转述 | `_require_user` **只拒**空/空白与含 `/` 的 user_id，`.` 是**允许**的 | 我先前把它记成"`.` 也拒"，实际不是。没有据此补一个演示不到的保护，而是把测试改成真实契约并写明原因（owner 来自服务端固定列表 `DEMO_USERS`，不来自请求） |

## T22 实测结论（覆盖审计与全量回归）

| 事项 | 结论 | 依据 |
|---|---|---|
| `gate --all` 的既有实现 | **本来就满足验收的两条要求**：它直接对每个任务调 `run_task`，既不递归调用 gate.py 也不读 state.json | 通读 `main()`；T22 的贡献是把这两条从"事实"变成"被断言且被守卫约束的事实" |
| 防自递归的守卫 | 新增 `assert_no_self_invocation()` | 症状是**挂起而不是报错**，所以值得一条显式拒绝；并有测试喂篡改清单确认它真的会拒 |
| 三列状态不可互相替代 | 自动 / 真实 / 人工 | 自动通过**不代表** live 通过（缺凭据时该列是 blocked），也不代表人工已接受（review 字段独立） |
| 待验收需求的交付物 | 按设计**不校验存在性** | R27/R28 的交付物本来就还没写；校验它们会让报告在未来的工作落地前无法生成，而真正不能发生的是把它们报成已通过 |
| skip 审计不能用 grep | T00 的套件里含**字符串字面量** `"@pytest.mark.skip(...)"` 作为测试数据 | 它用来验证「gate 必须让带 skip 的收据失败」。第一版审计用正则，把它误报成必需文件里的 skip——正是"不能只 grep"点名的错误。已改为 AST 检测装饰器与调用，并有「标记 vs 字符串」的对照测试 |
| 子串匹配的第二个假阳性 | `"scanned"` 含 `"canned"` | 固定演示回复的审计把 `test_a_long_message_is_not_scanned_for_preferences` 误报。已加词边界 |
| `tests/unit` 不存在 | "unit" 是**check 模式**，不是目录 | 仓库的套件是 acceptance / contract / integration，另有 tests/compat（CAP 实验库，不是套件）。断言一个从未有过的目录形状没有意义，改为断言真实布局 + unit 模式确实被声明 |
| 全量 JUnit 证据 | 26 个 done 任务的 receipt 全部通过，**零 skip、零零收集**，且每个测试类 check 都达到自己声明的最小例数 | 由 test_t22 对着 receipt 内容逐条断言，而不是看任务状态 |

### 一处真实模型暴露的设计问题

把「分派而不是包办」写进提示词**不足以**约束模型：`deepseek-chat` 在持有 `inventory_warning`
等读工具时选择自己查完直接回答，既不写 todo 也不委派。因此分工改为结构性约束——
`build_main_agent` 把子代理声明的工具从主 Agent 的工具集中摘除（`delegated_tools`），
主 Agent 只保留 `write_todos`、`task` 与子代理未覆盖的 Agent 层工具（如 T13 的图表工具）。
提示词保留对应的否定式说明，但不再是唯一防线。

## T23 实测结论（真实全栈、真实模型、真浏览器）

### 四个真缺陷，全是"看起来能跑"的那种

1. **`UserScopedStore` 只实现了同步面，框架走异步路径就崩**。`deepagents` 的 `StoreBackend`
   **偏好异步**（`aget`/`aput`/`asearch`/`abatch`，理由是 store 读取不该阻塞事件循环），而我们的
   门面是独立类、只写了同步一半 → 第一次异步触碰就
   `AttributeError: 'UserScopedStore' object has no attribute 'aget'`，整轮打崩。已补齐异步面，
   **守卫仍然只有一个定义**（`_guard`）；`abatch` 也必须守，因为框架的删除走的是 "put of None"
   而不是 `adelete`。前 22 个任务都没碰到，因为此前没有任何东西把真实图驱动到框架的异步 store 路径。
2. **记忆键不接受前导斜杠，而框架就是这么传的**。记忆挂载在 `/memories/`，`CompositeBackend`
   剥掉挂载前缀后交给后端的是 **`/preferences.md`**（带前导斜杠）；而 `memory_key()` 拒绝一切以
   `/` 开头的键。于是模型按文档写 `/memories/preferences.md` 时，运行以 `NAMESPACEVIOLATION`
   结束。已改为**归一化**。关键是这条约束**在契约与测试里都没有出处**——是自造的严格。键不可能
   因为开头有斜杠而离开命名空间（命名空间由挂载决定，不由键推导），真正有分量的是"不得穿越""不得为空"。
3. **审批授权在 HTTP 路由里，不在图里**。直接 `graph.ainvoke` 驱动会跳过 `_decide()`（登记决策）
   与 `approved_interrupt_id` 的注入，于是写操作被 `APPROVAL_REQUIRED` 拒绝——**门禁是对的，错的
   是绕过它的驱动方式**。已改为走真实路由（`/stream` + `/state` + `/resume`）；顺带这也是 T12 的
   票据绑定第一次被真正演示到。
4. **`ok` 看不见失败的运行**。`ok` 当时等价于"没有记录任何失败"，而**崩掉的运行不会记录任何失败**：
   D06 #1/#2 七秒就死，却被判通过——真正满足那三条期望的是记忆中间件**另一条**自动写入。已改为按
   **每次运行**记录 `done` 状态，任何一次 `failed` 都判该试验失败（`interrupted` 不算：停在审批或
   补充上是正常的结束方式）。

### 两处测试装置自身的错（记下来免得再写一遍）

- 自动化客户端把补充**当 dict 发**，而 `resume.supplement` 的契约类型**必须是字符串**（结构化答案
  以它的 JSON 文本传递），dict 会被 400 拒绝。
- 多轮推进循环**只在中断时前进**，于是每个多轮场景都只跑了第一轮就停——而它看起来完全正常。

## 搜索引擎更换（T23 期间，2026-09-19，用户要求降本）

用户要求"用个便宜点的"，`search_pro_sogou`（0.05 元/次）改为 **`search_std`（0.01 元/次）**，
单次成本降 80%。`contracts/external.md`、`dependencies.md` 与代码在**同一次改动里**一并更新——
后者的原话是「无引擎权限时报阻塞，**不暗换引擎**」，而一个与文档不符的引擎正是"暗换"。

### 四档实测（同一 key、同一 query）

| 引擎 | 单次 | 返回条数 | 带 `link` |
|---|---|---:|---:|
| `search_std` | **0.01 元** | 5 | **0** |
| `search_pro` | 0.03 元 | 5 | **0** |
| `search_pro_quark` | 0.05 元 | 5 | 5 |
| `search_pro_sogou` | 0.05 元 | 50 | 50 |

只看这一行会得出"便宜档拿不到可引用结果"。**多跑几条 query 后这个结论被推翻**：3 条不同 query 下
std 返回 15 条、其中 10 条带 `link`。于是补做 6 条真实 query 的采样：

| 引擎 | 单次 | 6 条里零链接的 | 可引用 |
|---|---|---:|---|
| `search_std` | **0.01 元** | 1 | 21/30 |
| `search_pro_sogou` | 0.05 元 | 0 | 300/300 |

**链接覆盖率是 query 相关的，不是随机的**：演示原来那条 query（`轴承钢 采购价格 行情`）在 std 上
连续三次都是 0/5 带链接（sogou 三次 50/50），正好落在失败的那 1/6。已换成实测能出链接的
`钢材 采购 成本控制`——否则资产验证会对着一个**正常工作的工具**报"搜索没找到东西"。

结论：80% 的降幅，代价是约 6 条 query 里 1 条查不到可引用结果，且**失败是响的**（返回空结果，
不是拿摘要冒充答案）。另外两档都不能用：`search_pro`（0.03 元）在试过的每条 query 上表现与 std
一致，没有任何一档比 std 更便宜；`search_pro_quark` 有链接但与 sogou **同价**，换过去不省钱。

### 一并纠正的两处过期说法

- T15 表里的「`count` 参数不生效，请求 2 返回 50」是 **sogou 的行为**。`search_std` **认** `count`。
  我们自己 clamp 的理由因此从"API 忽略它"改成"**它随档位而变**"——同一份代码在不同档位上行为不同，
  所以上限不能交给引擎。`web_search.py` 里那句注释已同步改写。
- `assets.json` 现在记录实际服务这一轮的引擎名（`"engine": "search_std"`）。一条没写引擎的证据，
  和一条被偷偷换掉的引擎，从外面看是分不出来的。

## 与方案文档的差异（须记录，不改变能力）

1. **MongoDB Store 的包名**：`dependencies.md` 引用的文档路径为
   `langgraph.store.mongodb.base.MongoDBStore`，其 PyPI 发行名是
   `langgraph-store-mongodb`（0.4.0），**不在** `langchain-mongodb` 内。
   T00 已确认 `langchain_mongodb` 0.12.0 不导出 `MongoDBStore`，避免误用同名类。
2. **LangChain 家族进入 1.x**：`deepagents` 0.7.14 要求
   `langchain>=1.4.0`、`langchain-core>=1.6.3`。`langgraph` 随之解析为 1.2.11，
   `langgraph-checkpoint` 4.2.0。方案文档未写死这些版本，T00 按官方 resolver 结果锁定。
3. **v2 streaming API 位置**：`langgraph.stream` 顶层导出
   `ProtocolEvent`、`StreamChannel`、`run_stream`、`convert_to_protocol_event`
   与各 Transformer（`MessagesTransformer`、`ValuesTransformer`、
   `UpdatesTransformer`、`SubgraphTransformer`、`LifecycleTransformer`）。
   方案所述“v2 统一 StreamPart”对应本版本的 `ProtocolEvent`；
   T06 会用该 API 录制真实分片。

4. **OpenSandbox 客户端换包（T00 的依赖选择被 T08 纠正）**：T00 锁定了 `opensandbox-sdk`，
   实测无法与 `opensandbox-server` 0.2.3 通信，共三处不兼容，且**没有**可用的版本组合可绕过：

   | 维度 | `opensandbox-sdk`（0.1.0/0.2.0/0.3.0 全部） | 服务端 0.2.3 期望 |
   |---|---|---|
   | 认证头 | `X-API-Key`（SDK 自建 httpx，调用方无法注入） | `OPEN-SANDBOX-API-KEY` |
   | 生命周期路径 | `POST /api/sandboxes` | `POST /sandboxes` 或 `/v1/sandboxes` |
   | 文件/命令 | `/api/sandboxes/{id}/files`、`/commands` | 只有 `/sandboxes/{id}/proxy/{port}` 端口代理 |

   实测现象：配 api_key → 全部请求 401；不配 → `POST /api/sandboxes` 404。
   改正为 **`opensandbox` 0.1.16**：它由服务端自身 OpenAPI 生成，认证头与路径一致，
   且**同时提供同步（`opensandbox.sync.SandboxSync`）与异步（`opensandbox.Sandbox`）客户端**，
   正好匹配 DeepAgents `BaseSandbox` 的同步原语 + 异步入口。`pyproject.toml` 已注明原因。

5. **`opensandbox-code-interpreter` 未引入**：快速开始文档提到它，但 T08 只需要执行与文件 I/O；
   代码解释器能力未被任何任务使用，引入会额外增加依赖面。

## 前端依赖（frontend/package-lock.json 冻结）

| 包 | 版本 | 说明 |
|---|---|---|
| vue | 3.5.42 | 运行时框架 |
| vite | 8.3.0 | 构建工具；engines 要求 node `^20.19.0 \|\| >=22.12.0` |
| @vitejs/plugin-vue | 6.0.9 | Vue SFC 支持 |
| typescript | 5.9.3 | 显式停在 5.x；不采用 TS 7，避免与 vue-tsc 3.x 的兼容风险 |
| vue-tsc | 3.3.11 | `npm run build` 先做类型检查再打包 |
| vitest | 5.0.1 | 组件/单元测试 |
| @vue/test-utils | 2.5.0 | 组件挂载 |
| jsdom | 29.1.1 | 测试 DOM 环境；30.x 要求 node `^24.15.0`，本机 node 为 24.13.0，故回落 |
| lucide-vue-next | 1.0.0 | 讲义要求的图标库；npm 已标记 deprecated（建议 `@lucide/vue`）。按方案保留，替换需先讨论 |

**Node 版本**：本机 nvm 装有 v20.18.1 与 v24.13.0。Vite 8 要求 `^20.19.0`，因此前端构建使用
**v24.13.0**（`NODE_HOME`）。nvm 的活动符号链接 `C:\nvm4w\nodejs` 当前为空，脚本改为直接解析
版本目录。

## 环境说明（本机特性，不是方案变更）

1. **JAVA_HOME 修复**：环境里的 `JAVA_HOME` 指向 `D:\Android_Studio\jbr\bin\java.exe`（文件而非目录）。
   `scripts/lib/config.py` 以“目录内存在 `bin/javac`”为判据重新解析，得到 `D:\Android_Studio\jbr`。
   Maven 本身未安装系统 `mvn`，一律使用项目 `erp/mvnw`（3.9.9，已固定 SHA-256）。
2. **`.venv` 解释器**：uv 管理的 `cpython-3.12-windows-x86_64-none` 是目录 junction，本机在该进程
   上下文中遍历会返回 `WinError 448`（不受信任的装入点），导致 uv trampoline 无法启动 Python。
   解决办法是用具体解释器路径创建标准虚拟环境：
   `& "<uv python>\cpython-3.12.14-windows-x86_64-none\python.exe" -m venv --clear .venv`。
   之后 `uv sync --frozen` / `uv run --frozen` 正常，`pyvenv.cfg` 不再指向 junction。
3. **pytest 临时目录 symlink**：pytest 会在基临时目录旁创建 `pytest-current` / `<test>current`
   符号链接并在收尾时解析它，本机同样触发 `WinError 448`，使 `pytest_sessionfinish` 与 atexit
   清理抛错。`tests/conftest.py` 仅对这一步的 `OSError` 做容忍处理；不影响用例收集、断言、
   报告或退出码。临时仓库内的子进程 pytest 另外传 `--basetemp`，从源头避免创建该链接。
4. **`./mvnw` 平台映射**：manifest 写的是 POSIX 的 `./mvnw`；`scripts/gate.py` 在 Windows 上
   解析为包装器的官方入口 `mvnw.cmd`。receipt 记录的是 manifest 原始 argv，实际执行命令记录在
   `executed_argv` 与日志首行，便于核对。

## 已知未验证项

| 项 | 状态 | 说明 |
|---|---|---|
| `infra/fixtures.Dockerfile` 镜像构建 | **已验证** | 基础镜像 `python:3.12-slim`（digest `sha256:78387bc3…`）。首次 `docker pull` 因守护进程优先走 IPv6 失败，重试即成功；`docker build --no-cache` 通过。容器内 `/health`、`/suppliers/S001/quotes`、`/skills/catalog.json` 均返回真实内容。 |
| 技能包跨平台可复现 | **已验证** | 宿主机（Windows）与容器（Linux）产出的 ZIP 哈希一致：`f29c04da27aa8019e3ca2273d231d7e96cc2383af6b29f35d4867bb2b8f0580f`，大小 3982 字节。关键在于显式固定 `ZipInfo.create_system=3`、时间戳与 `external_attr`。 |
| 容器内访问报价站（沙箱视角） | **已验证** | 沙箱内 `host.docker.internal:8088` 与 `172.17.0.1:8088` 均可访问宿主报价站；T08 验收用真实 HTTP 抓取 S001/S002 页面并核对内容 hash。 |
| MongoDB | **已验证** | `mongo:7.0.43@sha256:9854f713…` 由 `infra/compose.yml` 启动，容器 healthy，端口 27017。T07 实测：官方 `MongoDBSaver` 跨进程读回 checkpoint、MongoDB-backed `MongoDBStore` 往返、应用集合索引、用户 namespace/key 前缀隔离。 |
| OpenSandbox 控制服务 | **已验证** | 服务端是 Python 包 **`opensandbox-server` 0.2.3**（非 Go 源码），绑定 `127.0.0.1:18080`（8080 归 Java ERP），`GET /health` 返回 `{"status":"healthy"}`。配置见 `infra/sandbox/sandbox.toml`。 |
| OpenSandbox 客户端 | **已纠正依赖** | ~~`opensandbox-sdk`~~ → **`opensandbox` 0.1.16**。原选择无法与该服务端通信，详见下一行。 |
| 沙箱执行镜像 | **已验证** | `opensandbox/code-interpreter:v1.1.0`（digest `sha256:133a3c17…`，10.5GB），execd `opensandbox/execd:v1.0.22`（digest `sha256:0d8f44cf…`）。容器实测：Python 3.12.3、命令执行、文件读写、上传下载、销毁。 |
| host 8080 端口占用 | 无冲突 | 计划要求 OpenSandbox 宿主映射到 18080，避免与 Java ERP 的 8080 冲突。 |
| 真实模型 | **已验证** | `.env` 已配置 `MODEL_BASE_URL=https://api.deepseek.com/v1`、`MODEL_ID=deepseek-chat`；协议 OpenAI 兼容。真实调用通过 `ChatOpenAI` 与直接 HTTP 双路径验证：中文应答正确，provider 回报的 `model_name` 是 **`deepseek-flash`**（即讲义 DSV4.1Flash 简称对应的实际服务端模型），usage 元数据齐全。gate 的 `model` 能力判为 satisfied。 |
| 智谱搜索、ModelScope 图表 | 未联通 | `ZHIPU_API_KEY` / `MODELSCOPE_MCP_URL` / `MODELSCOPE_API_TOKEN` 本机没有任何可用来源，保持为空；T15/T23 仍会被 gate 阻塞（blocked，退出码 2）。不暗换引擎。 |
| Agent Protocol 独立服务 | **已验证（独立环境）** | 官方 `langgraph dev`（`langgraph-cli==0.4.31` + `langgraph-api==0.14.1` + `langgraph-runtime-inmem==0.34.1` + `colorama`）装在 `.venv-agent-protocol/`，绑定 `127.0.0.1:8123`，暴露图 `echo`。实测：`GET /ok` → `{"ok":true}`；官方 `langgraph_sdk` 同步与异步客户端都能发现 assistant、`runs.wait` 取回任务结果、threads/state 读回。详见 `infra/agent-protocol/README.md`。 |
| 为什么该服务必须隔离 | **已验证的冲突** | `langgraph-api` 0.14.x 要求 `grpcio>=1.81,<1.82`，而 `opensandbox-server` 0.2.3 要求 `grpcio>=1.83`，区间不相交 → `uv lock` 报 `No solution found`。独立环境内实测 `grpcio==1.81.1`，主锁内实测 `grpcio==1.84.0`。放进 dev 组还会连带降级主锁的 `protobuf`(7.36.1→6.33.6)、`opentelemetry-*`(1.44.0→1.42.1)、`sse-starlette`(3.4.11→3.3.4)。**教训**：`uv pip install --dry-run` 不看 dependency group，无法发现此冲突，只有 `uv lock` 权威。 |

## 复现命令

```bash
uv lock
uv sync --frozen
uv run --frozen python -c "import deepagents, langgraph, mcp, opensandbox; print('ok')"

python scripts/doctor.py            # 工具链与能力配置（不输出密钥）
python scripts/gate.py T00          # 任务验收入口
```

前端：

```bash
cd frontend
npm ci                              # 使用 package-lock.json 冻结安装
npm run build                       # vue-tsc --noEmit && vite build
```
