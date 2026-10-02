# OpenSandbox 执行环境

真实执行发生在一次性 Docker 容器里；宿主只运行控制平面。

## 拓扑

| 组件 | 位置 | 地址 / 镜像 |
|---|---|---|
| 控制服务 | 宿主进程 | `http://127.0.0.1:18080`（`GET /health` → `{"status":"healthy"}`） |
| 客户端 | 宿主 Python | `opensandbox` 0.1.16 的 `SandboxSync` / `Sandbox` |
| 执行容器 | Docker，`bridge` 网络 | `opensandbox/code-interpreter:v1.1.0`（digest `sha256:133a3c17…`） |
| execd | 挂进执行容器 | `opensandbox/execd:v1.0.22`（digest `sha256:0d8f44cf…`） |
| 沙箱 → 宿主 | bridge 网关 | `http://host.docker.internal:8088` |

**端口选择**：控制服务用 **18080**，因为 Java ERP 已经占用 8080。

**本机实测**：`host.docker.internal:8088` 与 `172.17.0.1:8088` 在沙箱内均可访问宿主报价站。

## 启动

```powershell
docker pull opensandbox/execd:v1.0.22
docker pull opensandbox/code-interpreter:v1.1.0

$env:OPENSANDBOX_INSECURE_SERVER="YES"      # 见下方“认证”一节
.venv\Scripts\opensandbox-server.exe --config infra/sandbox/sandbox.toml
```

日志写到 `infra/sandbox/server.{out,err}.log`，PID 记录在 `infra/sandbox/server.pid`。

## 认证：为什么没有 api_key

`infra/sandbox/sandbox.toml` 里**故意不配置 `api_key`**，这是上游不兼容导致的，不是疏忽：

- 服务端 `opensandbox-server` 0.2.3 只认请求头 `OPEN-SANDBOX-API-KEY`
  （`opensandbox_server/middleware/auth.py` 的 `SANDBOX_API_KEY_HEADER`）。
- 已发布的 `opensandbox-sdk` 0.1.0 / 0.2.0 / 0.3.0 **全部**发送 `X-API-Key`，并且自己构造
  httpx 客户端，调用方无法注入请求头。

一旦配置 `api_key`，SDK 的每个请求都会 401，且没有受支持的让双方一致的办法。
因此服务以无认证方式运行，仅因为：它只绑定 `127.0.0.1`，并且必须显式给出
`OPENSANDBOX_INSECURE_SERVER=YES` 启动确认（服务端会打印 WARNING 记录这一点）。
等到有 SDK 版本使用服务端的请求头时应当重新评估。

同类问题还有一处：`opensandbox-sdk` 走的是另一代 API（`/api/sandboxes`、`/files`、`/commands`），
与本服务端的 `/v1/sandboxes` + 端口代理完全不同，所以本项目改用 `opensandbox` 发行包。
详见 `docs/runtime/versions.md`。

## 安全边界

- 执行容器**不挂载**宿主目录：`[storage] allowed_host_paths = ["~/.opensandbox/mounts"]`。
- 容器内看不到 `/var/run/docker.sock`（验收用例实测）。
- `drop_capabilities` / `no_new_privileges` / `pids_limit` 沿用示例配置的保守取值。
- 未配置 `[egress]`：本演示不请求网络策略，因此不启动 egress sidecar。

## 沙箱工作区

`prepare_workspace()` 在每个新沙箱中创建：

```
/workspace              工作目录
/workspace/skills       技能包解压位置
/workspace/rules        只读规则（mode 444）
/workspace/scratch      临时文件
```

`/workspace/rules` 下的文件是只读的：Agent 可以读，但不能改写自己的规则。
