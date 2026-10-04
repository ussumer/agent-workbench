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

## T38 Jupyter 通道补丁与复现

默认控制配置已恢复官方 `opensandbox/execd:v1.0.22`。未通过完整gate的补丁仅保留在 `infra/sandbox/sandbox.t38-candidate.toml`，使用 `rush-harness/execd:1.0.22-kernel1`；不能作为已验收运行配置。它基于官方 execd v1.0.22 的固定 digest，只替换 `/execd`；控制服务、SDK、计算镜像和 Jupyter 未替换。源码 revision 为 `4a9db411879601610843af9c8e03563694325b2a`，下载包 SHA256 为 `934b517e10e20defd4d3bada8f07082632db3a5f701ed9302b27977c8d3de605`。

补丁位于 `infra/sandbox/patches/execd-v1.0.22-jupyter-readers.patch`：reader 固定其连接、执行消息按 parent request ID 筛选、通道 query/header session 一致；每个 context 保持同一通道供串行执行，首次连接先等待 Jupyter 启动 IOPub idle，执行前以 kernel_info 匹配 shell 回包和同请求 IOPub idle 确认双通道 readiness（15 秒有界探测；每次无副作用探针使用独立消息签名，不重试 Actor 代码），连接失效时清除引用，删除 context 时关闭通道。原始 daemon 的 foreign-parent 缺陷已有原生失败记录；逐 cell 重连的真实停滞与后续诊断连接触发旧请求执行也已保留。这些原生检查不能代替完整真实服务 gate。

Linux/WSL 使用 Go 1.25.9 linux/amd64 和 Docker 构建：

```bash
python3 scripts/build_kernel_execd.py --go /path/to/go1.25.9/bin/go
```

可用 `--source /path/to/exact-upstream.tar.gz` 复用下载包，仍校验 SHA256。脚本运行 execute/auth/session 原生 race 测试，拒绝零测试、失败或跳过，编译后基于官方镜像 digest 打包；`artifacts/tasks/T38/native-builds/<attempt>/provenance.json` 记录源码、补丁、测试、二进制和镜像哈希。宿主只编译基础设施；采购 Actor 代码始终在 OpenSandbox 执行。构建完成后重启本任务控制服务，避免缓存旧 daemon；用完整 T38 gate 验证部署结果。
