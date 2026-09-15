# 任务索引

执行协议见 [execution](../execution.md)。T00 创建 gate.py 后，各任务用 `python3 scripts/gate.py TXX`；当前任务状态见 [state.json](../state.json)。一个 Agent 同时只执行一个任务，编号是默认阅读顺序，依赖图决定哪些任务可在阻塞时继续。

| ID | 任务 | 前置 | gate模式 |
|---|---|---|---|
| [T00](T00.md) | 建立工程、依赖锁与验收入口 | 无 | unit |
| [T01](T01.md) | 固定业务种子与契约测试数据 | T00 | unit |
| [T02](T02.md) | Java目录查询与库存预警 | T01 | integration |
| [T03](T03.md) | Java订单事务、版本和幂等 | T02 | integration |
| [T04](T04.md) | 真实MCP网关与八个ERP工具 | T03 | integration |
| [T05](T05.md) | 报价站与技能下载资源 | T01 | integration |
| [T06](T06.md) | 真实模型与框架兼容性验证 | T00 | live |
| [T07](T07.md) | Mongo会话、checkpoint与Store | T00 | integration |
| [T08](T08.md) | OpenSandbox协议适配和执行环境 | T00, T05 | integration |
| [T09](T09.md) | 用户沙箱池、稳定代理和恢复 | T07, T08 | integration |
| [T10](T10.md) | CompositeBackend与预置技能发现 | T07, T09 | integration |
| [T11](T11.md) | 主Agent、YAML子Agent与规划 | T04, T06, T10 | integration |
| [T12](T12.md) | 双层HITL、批准绑定与重放防重 | T03, T07, T11 | integration |
| [T13](T13.md) | FastAPI SSE、历史和恢复接口 | T12 | integration |
| [T14](T14.md) | Vue工作界面与浏览器交互 | T13 | integration |
| [T15](T15.md) | 真实搜索与统一图表入口 | T04, T06 | live |
| [T16](T16.md) | 预置抓取分析技能与报告下载 | T05, T10, T11, T15 | integration |
| [T17](T17.md) | 技能创建、下载、验证、分配和持久化 | T07, T16 | integration |
| [T18](T18.md) | 长期偏好与自动采购记忆 | T07, T11 | integration |
| [T19](T19.md) | 上下文压缩、熔断和共享预算 | T09, T11, T18 | integration |
| [T20](T20.md) | Agent Protocol异步分析子Agent | T06, T11, T14, T16 | integration |
| [T21](T21.md) | 恢复、隔离和跨模块故障联调 | T14, T17, T19, T20 | integration |
| [T22](T22.md) | 完整确定性回归与课程覆盖审计 | T21 | integration |
| [T23](T23.md) | 真实模型演示与资产验证 | T22, T15 | live |
| [T24](T24.md) | 打包、运行手册和学习交付 | T23 | integration |

## 执行策略

先做可重复的业务数据/MCP，再验证模型和图接口，再连接持久化与沙箱，最后组合UI和技能。外部凭据缺失时保持相关任务blocked，`plan_guard next`会列出其他可做任务；不删除依赖边。

T22/T23/T24分别证明确定性回归、真实模型行为和清洁启动交付，三者不能合并成一条“演示成功”。编码速度通过缩小每包上下文和快速定位失败提高，最终规格不随任务进度降低。
