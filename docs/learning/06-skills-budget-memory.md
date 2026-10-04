# Part 06 技能发布与运行控制

## 学习目标

分别解释“增加能力”“限制运行”“保存长期信息”，不把它们统称为自进化。理解T30–T35修正的具体行为。

## 机制解释

技能是说明、脚本和资源组成的能力包。产生代码不等于验证可用；验证可用不等于已发布。SkillPublisher依次收集候选、校验、真实沙箱执行、保存版本、更新assignment指针。坏脚本、文件缺失或写入失败不应让可用指针指向半成品。

版本在Mongo预占，失败编号不复用；文件写入Store并读回校验后才记录persisted。assignment用起始revision做一次条件更新，发生竞争时不静默重新基于新revision覆盖赢家。不可变版本设计与发布指针解决写入窗口，但完整候选谱系、模型自动晋升与回滚仍未实现。

业务验证需要独立预期，不能完全依赖候选自己提供的example。当前核心reorder-cost-summary有独立业务案例；一般指导类技能只具备结构验证，不应声称都证明了行为泛化。

共享预算处理有限终止。每次API run有独立callback，父子模型和工具调用汇总；Mongo对owner/thread原子扣线程额度。局部SDK限制只约束各自图，不因复用实例就天然共享全局额度。任何预算耗尽都应failed，不能让父Agent接住子Agent异常后宣称成功。

熔断处理基础设施连续失败：同owner proxy报告真实超时和运输故障，达到阈值后拒绝调用，冷却后允许有限探测。普通脚本非零退出、文件不存在和ERP业务拒绝不能混为容器故障。熔断、预算、工具错误是不同问题。

偏好是用户明确表达的允许字段；采购历史来自本run真实成功工具证据，不能由网页指令或模型自述改写。偏好与自动历史分键，减少自动写覆盖显式选择的风险；并发历史无损合并仍是遗留问题。

摘要是有损压缩。自动和主动SDK摘要模型生成前，完整原始消息与todos/中断信息归档到owner Store并读回确认；UUID key避免同run重复覆盖。消息序列化保留tool_calls、配对ID和artifact。归档失败禁止提交压缩结果，不能只留下摘要。展示历史又是独立记录。

## 代码阅读

本章可以分三次，每次只选一组：

- 技能：[pipeline.py](../../src/agent/skills/pipeline.py) 的prepare/complete/_persist；[store.py](../../src/agent/skills/store.py) 的reserve_version/record_version/assign；[procurement_validation.py](../../src/agent/skills/procurement_validation.py) 的独立预期。
- 限制：[run_budget.py](../../src/agent/run_budget.py) 的_reserve/on_chain_error；[sandbox_breaker.py](../../src/agent/middlewares/sandbox_breaker.py) 的admit与epoch；[sandbox_proxy.py](../../src/agent/backends/sandbox_proxy.py) 的_invoke。
- 记忆：[preferences.py](../../src/agent/memory/preferences.py)、[memory_update.py](../../src/agent/middlewares/memory_update.py) 的apply；[conversation_archive.py](../../src/agent/middlewares/conversation_archive.py) 的archive_context/_archive；[tools_summarization.py](../../src/agent/middlewares/tools_summarization.py) 的archive_thread_history。

跳过完整ZIP解析、所有SDK摘要策略和所有偏好正则。先解释状态与失败结果，再钻实现细节。

## 具体案例

候选技能通过自己的example，但独立业务案例失败：不发布。一个run父模型调用1次，子模型调用2次：共享计数是3。归档写Mongo失败：不调用摘要模型、不提交新压缩事件。三种结果各有独立检查。

证据：[T30](../../tests/acceptance/test_t30.py)、[T31](../../tests/acceptance/test_t31.py)、[T32](../../tests/acceptance/test_t32.py)、[T33](../../tests/acceptance/test_t33.py)、[T35](../../tests/acceptance/test_t35.py)。选一个测试，解释它为什么能发现“函数存在但运行未接线”。

## 自检

1. 版本保存成功后，发布指针竞争失败，是否应该覆盖当前指针？
2. 为什么候选自己的示例不足以证明技能正确？
3. 主子共享预算与局部中间件计数有何区别？
4. 摘要成功是否证明历史已完整归档？

核对要点：保留已发布赢家并报告冲突；独立预期防自证；不同图状态不能直接当共享计数；必须独立核验归档先于摘要。

## 面试表达

先选一个你实际掌握的专题：“技能发布采用版本与指针分离，验证失败或半写不发布。”或“我们在运行层累计父子预算，避免局部图限额失效。”不要一次堆满所有术语。

## 确认

为技能、预算和记忆分别写一个失败案例；选一个能解释到断言的测试。三组允许分别记录进度。
