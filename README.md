# 采购 Harness 演示项目

围绕采购库存业务的课程 Demo：Java 采购库存 Mini-ERP + FastMCP 网关 + DeepAgents/LangGraph
采购助手 + MongoDB 持久化 + OpenSandbox 沙箱 + Vue 工作界面。

## 从干净环境启动

```bash
# 1. 依赖（都按锁文件装，不改锁）
uv sync --frozen
cd frontend && npm ci && cd ..
docker compose -f infra/compose.yml up -d mongo

# 2. 自检：工具版本、工具链、已配置的能力名（只报名字，不报值）
python scripts/dev.py doctor

# 3. 起栈（沙箱控制服务没起的话 dev.py 会自己拉起来；数据默认保留）
python scripts/dev.py up

# 4. 烟测：通过 API 问一个只读问题，要求这一轮走委派路径
python scripts/dev.py smoke

# 5. 停止（数据留着；`reset --yes` 才清）
python scripts/dev.py down
```

`up` 的真实地址每次都是动态端口，以 `python scripts/dev.py status` 为准。
完整的排障、日志位置、重置范围见 **[运行手册](docs/runtime/runbook.md)**。

## 看代码从哪读起

三条链路各走一遍，就覆盖了这个 Demo 的骨架：**下单**、**出报告**、**技能恢复**。
见 **[walkthrough.md](walkthrough.md)**。

## 验收与证据

```bash
python scripts/plan_guard.py check     # 方案结构
python scripts/plan_guard.py next      # 下一个可执行任务
python scripts/plan_guard.py packet T24
python scripts/gate.py TXX             # 某个任务的验收；收据落在 artifacts/tasks/TXX/
python scripts/demo.py --round r1      # 24 次真实轮次（自己起一整套服务，别与 dev.py 同时跑）
```

| 想找什么 | 去哪 |
|---|---|
| 演示轮次证据（事件流、判定、产件） | `artifacts/live/<round>/` |
| 任务验收收据（命令、退出码、日志 hash） | `artifacts/tasks/<TASK>/<时间戳>/receipt.json` |
| 需求到文件的对应 | `docs/runtime/coverage-report.md`（生成物，不手写） |
| 实测结论与被推翻的猜测 | `docs/runtime/versions.md` |
| 当前进度与交接 | `docs/plan/HANDOFF.md` |

## 文档入口

- 执行方案总览：`docs/plan/README.md`
- 编码 Agent 入口：`docs/plan/START-HERE.md`
- 执行协议：`docs/plan/execution.md`
- 验收规格：`docs/plan/verification.md`
- 运行手册：`docs/runtime/runbook.md`

## 已知限制

- **前端单测要求 Node ≥ 20.19**（`jsdom` 依赖链需要 `require(esm)`）。本机默认 20.18.1 时
  `npm run build` 正常，但 `vitest` 一个用例都跑不起来。`package.json` 的 `engines` 已声明。
- **演示轮次是真实模型调用**，同一场景几次之间会有差异：`docs/plan/demo.md` 的口径是
  24 次中 ≥22 次成功且每个场景 ≥2 次，不是每次都必须一样。
- **沙箱内访问夹具站**要用 `host.docker.internal:8088`（容器、浏览器、宿主的 localhost 互不相同）；
  端口由 `FIXTURES_BASE_URL` 固定，不能改成动态端口——技能文档指向的就是这个地址。
- **用户尚未审阅**：所有应用任务的自动与真实检查可以全绿，但 `docs/plan/state.json` 里的
  `review` 字段是人工决定，不由 gate 填写。

## 目录

见 `docs/plan/architecture.md` 的目录合同。
