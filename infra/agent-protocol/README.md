# 独立 Agent Protocol 服务

异步子 Agent（DeepAgents `AsyncSubAgent`）通过 Agent Protocol 调用一个**独立进程**。
本目录提供该服务的最小可运行配置。

## 拓扑

| 组件 | 位置 | 说明 |
|---|---|---|
| 客户端 | 主进程（`.venv`） | `langgraph_sdk.get_client` / `AsyncSubAgent` |
| 服务端 | 独立进程（`.venv-agent-protocol`） | 官方 `langgraph dev`，绑定 `127.0.0.1:8123` |
| 图 | `infra/agent-protocol/graph.py` | 图 ID `echo`，确定性节点 |
| 配置 | `infra/agent-protocol/langgraph.json` | 声明 `dependencies` 与 `graphs` |

`echo` 图刻意是确定性的：CAP-01..05 用真实模型验证模型能力，CAP-08 只验证**传输**，
这样传输故障与模型故障不会互相掩盖，且异步服务冒烟不需要消耗 token。

## 为什么必须单独一个环境

**官方服务无法与本项目的锁共存**，这不是偏好问题：

```
langgraph-api 0.14.x  ->  grpcio>=1.81.0,<1.82.0
opensandbox-server 0.2.3  ->  grpcio>=1.83.0
```

两者要求的 `grpcio` 区间不相交，`uv lock` 直接报 `No solution found`。
把这个包放进 dev 组，还会顺带把锁内的 `protobuf`(7.36.1→6.33.6)、
`opentelemetry-*`(1.44.0→1.42.1)、`sse-starlette`(3.4.11→3.3.4) 一起降级。

所以服务被安置在 `.venv-agent-protocol/`（已 gitignore）：主锁一个包都不动，
而且这正好符合真实部署形态——异步 Agent 服务本来就是独立进程/独立部署单元。

注意：`uv pip install --dry-run` **无法**发现这个冲突（它不看 dependency group），
只有 `uv lock` 才是权威判定。

## 准备与启动

```powershell
python scripts/provision_agent_protocol.py        # 建独立环境并安装（首次约数分钟）

$env:PYTHONIOENCODING="utf-8"; $env:PYTHONUTF8="1"
.venv-agent-protocol\Scripts\langgraph.exe dev `
  --config infra/agent-protocol/langgraph.json `
  --port 8123 --host 127.0.0.1 --no-browser --no-reload
```

测试由 `tests/fixtures/agent_protocol_service.py` 自行拉起与收尾。

WSL/Linux 使用独立的 `.venv-agent-protocol-linux`，与 Windows 环境并存；启动器和 Python 按当前操作系统选择，不能把 `/mnt/c/...` 配置路径传给 Windows launcher。

```bash
.venv-linux-t45/bin/python scripts/provision_agent_protocol.py
.venv-agent-protocol-linux/bin/langgraph dev \
  --config infra/agent-protocol/langgraph.json \
  --port 8123 --host 127.0.0.1 --no-browser --no-reload
```

Linux 完整依赖冻结在 `requirements-linux.lock`；复建时可在已创建的独立环境中运行：

```bash
uv pip sync --python .venv-agent-protocol-linux/bin/python \
  infra/agent-protocol/requirements-linux.lock
```

## 踩过的两个坑（已写入脚本，勿删）

1. **`.venv-agent-protocol` 必须用具体解释器创建**：`uv venv` 会生成指向 uv 托管目录
   junction 的布局，本机遍历该装入点返回 `WinError 448`，导致 `uv pip install` 失败。
   必须用 `"<uv python>\cpython-3.12.14-...\python.exe" -m venv --clear`。
2. **Windows 上必须装 `colorama`**：`langgraph_api.logging` 用
   `structlog.dev.ConsoleRenderer(colors=True)`，缺少 `colorama` 会在启动时抛
   `SystemError`，表现为 uvicorn `Unable to configure formatter 'simple'`。

另外 `--help` 与日志输出在 GBK 控制台会崩，脚本统一设置 `PYTHONIOENCODING=utf-8`。
