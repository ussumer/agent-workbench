# 运行手册

从干净环境启动、看懂、停掉、重置这个 Demo。**下面每一条命令都实测过**（2026-09-21，Windows 11 + Docker 29.2.1 + Python 3.12.14 + JDK 21）。凡是没跑通的都不写在这里。

## 0. 一次性准备

```bash
python scripts/dev.py doctor     # 环境自检：工具版本、工具链、已配置的能力名（只报名字，不报值）
python scripts/dev.py doctor     # 退出码非 0 就是缺关键工具，输出里会点名
```

`doctor` 报告的是**配置名**而不是值，所以它的输出可以直接贴进 issue 或证据目录。

依赖安装用已冻结的锁文件：

```bash
uv sync --frozen                 # Python
cd frontend && npm ci && cd ..   # 前端（npm ci 按 package-lock.json 装，不改锁）
```

需要的容器与外部服务：

```bash
docker compose -f infra/compose.yml up -d mongo     # 必需：MongoDB :27017
docker compose -f infra/compose.yml up -d fixtures  # 可选：把夹具站也交给容器（默认由栈自己起）
```

**沙箱控制服务不用手动起**：`dev.py up` 会先探测 18080，没在听就自己拉起来（`OPENSANDBOX_INSECURE_SERVER=YES`，仅绑 127.0.0.1）。起不来时它会打印可直接粘贴的命令，而不是抛栈。

## 1. 启动

```bash
python scripts/dev.py up
```

实测输出（2026-09-21）：

```
沙箱控制服务：由 dev.py 启动（pid 60788）
启动中（pid 51232），日志在 artifacts\dev/logs/
就绪：http://127.0.0.1:12851
  数据库  rush_harness_test_dev
  日志    artifacts/dev/run
```

- `up` 跑的是**验收轮次用的同一套装配**（`tests/live/stack.py` 的 `running_stack`），把运行目录换成 `artifacts/dev/`、数据库换成按名字的 `rush_harness_test_dev`，并显式启用 `preserve_data=True`；普通验收默认仍清理独立测试库。
- **数据默认保留**：`down` 不删任何东西，再次 `up` 会保留 MongoDB 中已有的会话、运行、技能等记录，ERP 也继续使用原有 H2 文件。仅验证库名相同不足以证明保留：T25 在独立真实库写入记录和 ERP 订单，验证异常停止后、再次启动后数据以及订单幂等记录仍在。想清数据用 `reset`。
- 各服务端口是**动态分配**的（栈启动时取空闲端口），真实地址以 `status` 为准，不要照抄上面的数字。唯一例外是夹具站：它**必须**起在 `FIXTURES_BASE_URL` 的端口上（默认 **8088**），因为技能文档告诉 agent 去 `host.docker.internal:8088` 抓报价——这一点由 `tests/live/stack.py` 保证。

## 2. 看状态与日志

```bash
python scripts/dev.py status     # 会话 pid、数据库、四个服务的真实地址与健康
python scripts/dev.py logs       # 各服务日志尾部（默认 40 行）
python scripts/dev.py logs --tail 200
```

实测 `status`：

```
运行中：http://127.0.0.1:12851（pid 67120，2026-09-21T12:41:37Z）
  数据库  rush_harness_test_dev
  erp      http://127.0.0.1:12868  ok
  gateway  http://127.0.0.1:11506  ok
  site     http://127.0.0.1:8088  ok
  app      http://127.0.0.1:12851  ok
```

日志文件：

| 文件 | 里面是什么 |
|---|---|
| `artifacts/dev/logs/serve.log` | 会话进程自己的输出；**启动失败的原因在这里** |
| `artifacts/dev/logs/opensandbox.log` | 沙箱控制服务（仅当由 `dev.py` 启动） |
| `artifacts/dev/run/` | 栈起的各服务日志（ERP、网关、夹具站、Agent Protocol） |

## 3. 烟测

```bash
python scripts/dev.py smoke
```

它通过 API 问一个只读问题，并要求这一轮**走了委派路径**：

```
线程 devsmoke-1789994551：948 帧，done=completed
  线上可见的调用：write_todos, task, write_todos
  待处理中断：0
烟测通过。
```

**这条命令能证明什么，不能证明什么**：子代理自己的工具调用跑在 `task` 里面、不上 SSE 线（见 `src/api_view/stream_adapter.py`），所以烟测证明的是「应用应答、主 Agent 走了委派、这一轮 `completed`」。真正的 ERP 读数由 `artifacts/live/` 的轮次负责核对。

## 4. 数据

```bash
python scripts/dev.py seed       # 读回 ERP 数据并和用例预期逐项核对
python scripts/dev.py seed --json
```

`seed` 不写数据——ERP 启动时自己从 `fixtures/seed` 装载——它做的是**核对**：把物料库存读回来，与 `tests/live/scenarios.py` 里 D01 的数字（其来源是 `fixtures/expected-v1.json`）逐项比对，对不上就以非 0 退出。打印"已 seed"而什么都不验的命令，和什么都没做是分不清的。

## 5. 停止

```bash
python scripts/dev.py down
```

实测：

```
正在停止 pid 67120（数据保留；要清数据用 `reset --yes`）…
已停止。
  沙箱控制服务已停止（pid 60788，由本次会话启动）
```

`down` 之后 `Get-CimInstance ... python.exe` 里**没有残留的本项目服务进程**（实测计数 0）。它只停**本次会话启动的**沙箱服务；如果那个服务在你 `up` 之前就在跑，它属于你，`down` 不动它。

## 6. 重置

```bash
python scripts/dev.py down        # 必须先停；运行中 reset 会被拒绝
python scripts/dev.py reset --yes
```

`reset` 只动三样，这就是全部：会话的 MongoDB 库（且只接受本项目创建的库名）、`artifacts/dev/`、停止标记。

**沙箱容器不在其中**：它们按 owner 归属、由沙箱服务持有，一个会伸手进去删容器的 reset 命令，正是"不误删其他容器"失效的方式。

## 7. 排障

| 现象 | 原因与做法 |
|---|---|
| `up` 报 `MongoDB is not reachable` | `docker compose -f infra/compose.yml up -d mongo` |
| `up` 报沙箱控制服务起不来 | 看 `artifacts/dev/logs/opensandbox.log`；手动起：`.venv\Scripts\opensandbox-server.exe --config infra/sandbox/sandbox.toml`（需 `OPENSANDBOX_INSECURE_SERVER=YES`） |
| `npm run build` 报 `Cannot find name 'process'` | `cd frontend && npm ci`（`vite.config.ts` 需要 `@types/node`，已在 devDependencies 里） |
| 前端单测一个都跑不起来、报 `ERR_REQUIRE_ESM` | 本机 Node 低于 20.19。构建不受影响，跑测试要用 ≥20.19 或 ≥22.12（`package.json` 的 `engines` 已声明） |
| `status` 说"没有在运行"但 `session.json` 还在 | 上次会话没干净退出。文件里是上一次的地址，不代表现在在跑 |

## 8. 验收与演示

```bash
python scripts/plan_guard.py check        # 方案结构
python scripts/gate.py TXX                # 某个任务的验收（收据落在 artifacts/tasks/TXX/）
python scripts/demo.py --round r1         # 24 次真实轮次（会自己起一整套服务）
python scripts/demo.py --round r1 --assets        # 三类图表 + 真实搜索
python scripts/demo.py --round r1 --screenshots   # 桌面 1440×900 / 移动 390×844 截图
```

`demo.py` 与 `dev.py` 都起整套服务，**不要同时跑**：两者会争同一个夹具站端口（8088）。`demo.py` 的轮次目录带互斥锁（`run.lock`），同一个轮次名不会被两个进程同时写。

## 9. 证据在哪

| 想找什么 | 去哪 |
|---|---|
| 演示轮次的原始证据（事件流、判定、产件） | `artifacts/live/<round>/`，另有 `assets.json`、`screenshots.json` |
| 任务验收收据（含命令、退出码、日志 hash） | `artifacts/tasks/<TASK>/<时间戳>/receipt.json` |
| 计划状态与交接 | `docs/plan/state.json`、`docs/plan/HANDOFF.md` |
| 实测结论（哪些是量出来的、哪些是猜过又推翻的） | `docs/runtime/versions.md` |
| 需求到文件的对应 | `docs/runtime/coverage-report.md`（由 `scripts/coverage_report.py` 生成，不手写） |
