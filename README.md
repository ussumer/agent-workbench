# 采购 Harness 演示项目

围绕采购库存业务的课程 Demo：Java 采购库存 Mini-ERP + FastMCP 网关 + DeepAgents/LangGraph
采购助手 + MongoDB 持久化 + OpenSandbox 沙箱 + Vue 工作界面。

## 文档入口

- 执行方案总览：`docs/plan/README.md`
- 编码 Agent 入口：`docs/plan/START-HERE.md`
- 执行协议：`docs/plan/execution.md`
- 当前进度与交接：`docs/plan/HANDOFF.md`
- 验收规格：`docs/plan/verification.md`

## 开发命令

```bash
python scripts/plan_guard.py check          # 方案结构检查
python scripts/plan_guard.py next           # 下一个可执行任务
python scripts/plan_guard.py packet T00     # 任务包与必读规格
python scripts/gate.py TXX                  # 任务验收入口（T00 后可用）
python scripts/doctor.py                    # 环境诊断（不输出密钥）
```

依赖安装使用已冻结锁文件：

```bash
uv sync --frozen
```

## 目录

见 `docs/plan/architecture.md` 的目录合同。
