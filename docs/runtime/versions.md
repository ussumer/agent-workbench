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
| Agent Protocol 独立服务 | 未选型 | 本机无 `langgraph_api`/`langgraph_runtime`。官方 `langgraph-api` 0.14.1 可解析，但会把锁内的 `protobuf` 7.36.1→6.33.6、`opentelemetry-*` 1.44.0→1.42.1、`sse-starlette` 3.4.11→3.3.4 一起降级，因此未纳入本包依赖；T06 CAP-08 与 T20 的选型见 HANDOFF。 |

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
