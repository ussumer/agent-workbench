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
| langgraph-sdk | 0.4.4 | Agent Protocol 客户端 |
| mcp | 1.30.0 | `from mcp.server.fastmcp import FastMCP`、`mcp.ClientSession` |
| langchain-mcp-adapters | 0.3.2 | `from langchain_mcp_adapters.client import MultiServerMCPClient` |
| opensandbox-sdk | 0.3.0 | `from opensandbox import Sandbox, Commands, Filesystem, Template` |
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

Sandbox(sandbox_id: str, status="running", template="", domain="",
  _api_url="", _api_key="", _connect_url="", _token="", ...)
Commands(_client: httpx.AsyncClient, _sandbox_id: str)
Filesystem(_client: httpx.AsyncClient, _sandbox_id: str)
Template(_client: httpx.AsyncClient)
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
