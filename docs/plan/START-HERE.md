# 给编码 Agent 的入口

将下列消息作为新会话任务。仓库文件是事实来源，聊天摘要不能覆盖契约。

```text
你负责按本仓库 docs/plan/ 实现课程采购 Harness Demo。
先读 AGENTS.md、docs/plan/README.md、docs/plan/execution.md、
docs/plan/HANDOFF.md，然后运行：
python3 scripts/plan_guard.py check
python3 scripts/plan_guard.py next

只选一个依赖已完成的未完成任务，用
python3 scripts/plan_guard.py packet TXX
输出任务和必读规格，读完后实施。不要一次重写整个项目。

Java ERP、Vue、FastAPI、DeepAgents/LangGraph、MCP、MongoDB、
OpenSandbox 和课程能力已确认。不得用 Python ERP、宿主机 shell、
内存持久化、硬编码 Agent 或其他框架替代。
任何课程能力/技术调整必须先讨论并获得用户同意。

字段和状态迁移以契约为准，必要的测试在本任务内完成。
T00 建立应用验收入口后，运行本任务 gate，修复实际失败。
记录命令、退出码、断言数和真实服务模式。缺凭据或服务不可用时
记录 blocked，保留失败证据，不能跳过检查再登记 done。

通过 gate 才登记 done，用户审阅单独登记。未完成时记录现场，
更新 HANDOFF.md，不删需求、不降低阈值、不伪造截图或通过记录。
最终说明本包改动、检查结果、关键代码阅读入口和下一任务。
同一任务的临时失败继续修复，不在刚写完文件时结束。
```

## 用户准备

- 整体确认方案后从 T00 开始；以后使用同一入口续跑。
- 密钥仅配置到本地 `.env`，不粘到方案或提交。
- 搜索、图表配置可稍后补，但 T15/T23 必须真实接入后才完成。
- 执行模型必须能读写仓库、执行终端。纯聊天环境不能自主构建验收。
- 不要求每个任务额外请求许可；用户可逐包审核，里程碑发现问题后重开相关任务并重新验证受影响部分。
