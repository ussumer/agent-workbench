# 依赖与接口核验

核验日期：2026-09-15。官方资料不是本机联调证据；完整版本组合由 T00/T06 验证。

## 官方依据

| 项目 | 资料与结论 | 必做验证 |
|---|---|---|
| 后端 | [DeepAgents backends](https://docs.langchain.com/oss/python/deepagents/backends)：StoreBackend 使用 LangGraph Store；工厂/namespace 接口有版本迁移 | 记录构造签名，验证用户 namespace |
| 流 | [LangGraph streaming](https://docs.langchain.com/oss/python/langgraph/streaming)：v2 从 1.1 起支持统一 StreamPart | 固定 v2，录制子图、token、interrupt |
| 恢复 | [interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)：依赖 checkpoint，节点可能重放 | 中断前无不可重复副作用 |
| 审批 | [DeepAgents HITL](https://docs.langchain.com/oss/python/deepagents/human-in-the-loop) | 实测 interrupt_on、approve/reject |
| 技能 | [skills](https://docs.langchain.com/oss/python/deepagents/skills)：渐进加载 | 验证刷新时机，不承诺运行中任意热加载 |
| MCP | [LangChain MCP](https://docs.langchain.com/oss/python/langchain/mcp) | 会话管理、调用上下文身份传播 |
| Mongo Store | [MongoDB 维护的 LangGraph Store API](https://langchain-mongodb.readthedocs.io/en/latest/langgraph_store_mongodb/base/langgraph.store.mongodb.base.MongoDBStore.html) | 不误用 langchain_community 同名类 |
| 限制 | [官方中间件](https://docs.langchain.com/oss/python/langchain/middleware/built-in) | 避免和框架内置摘要、限制重复安装 |
| 异步 | [async subagents](https://docs.langchain.com/oss/python/deepagents/async-subagents)：Agent Protocol，预览功能 | 固定可运行版，独立服务启动/查/更新/取消 |
| 沙箱 | [OpenSandbox 快速开始](https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/getting-started/index.md) | 区分管理服务、execd、SDK，实际创建执行传文件销毁 |
| 搜索 | [智谱 API](https://docs.bigmodel.cn/api-reference/工具-api/网络搜索)：web_search、search_pro_sogou | 无引擎权限时报阻塞，不暗换引擎 |
| 图表 | [AntV 原始项目](https://github.com/antvis/mcp-server-chart) | 从 ModelScope 真实端点发现工具，保存目录快照 |
| Java | [Boot 3.5 要求](https://docs.spring.io/spring-boot/3.5/system-requirements.html) | 本项目 Java 21，不需要升级 Boot 4 |

## 版本锁定程序

1. 检查 Python、uv、Node、Docker；Java 必须检查 javac，仅有 java 不算 JDK 可构建。可用项目构建容器提供 JDK 21/Maven，不修改系统默认 Java。
2. Python 3.12；Vue 3 + Vite + TypeScript；Java 21 + Boot 3.5。解析精确补丁版、生成锁、记录来源。
3. Python 解析 DeepAgents、LangGraph、LangChain、官方 MCP Python SDK 的 FastMCP、langchain-mcp-adapters、MongoDB checkpoint/store 插件、OpenSandbox SDK、FastAPI、httpx、pydantic、pytest、ruff。核对包名与 import，不混淆独立 fastmcp 包与 MCP SDK 的 FastMCP。
4. 必须同时具备 v2 streaming 和 AsyncSubAgent。以官方 metadata 和 resolver 生成组合，不人工拼凑最低版本。
5. T00 生成 `docs/runtime/versions.md`：精确版本、来源、关键 import、镜像 tag/digest、验证命令和结果。创建 uv.lock、package-lock.json、Maven wrapper 及固定插件版本。
6. T06 实测后标记 verified，此前只标 resolved。后续 frozen/ci 安装，不每个任务升级。

## T06 最小兼容性矩阵

| 编号 | 实验 | 证据 |
|---|---|---|
| CAP-01 | 模型中文和两轮对话 | 实际模型 ID、协议、脱敏响应 |
| CAP-02 | add(a,b) 工具调用及 ToolMessage | 调用 ID、参数、答案匹配 |
| CAP-03 | 流式工具参数拼接 | 原始分片和归一化结果 |
| CAP-04 | 主 Agent 委派只读子 Agent | task 及子事件 |
| CAP-05 | 子 Agent interrupt、原 thread 恢复 | 补充、approve、reject |
| CAP-06 | Mongo Checkpointer/Store 重启读回 | 不使用同一内存对象 |
| CAP-07 | OpenSandbox 创建执行下载销毁 | 容器 ID、返回码、文件校验和 |
| CAP-08 | AsyncSubAgent + Agent Protocol 最小调用 | import、服务启动、任务结果 |

缺模型配置时 T06 blocked，可继续不依赖 T06 的任务。不根据 DSV4.1flash 简称假定公开模型 ID、协议或上下文上限。

适配已选框架版本的 import/参数须记录差异和回归，不构成换框架。无法保留能力或改用其他数据库、沙箱、外部服务时，必须先讨论并获得同意。
