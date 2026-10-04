# Part 04 主子 Agent 怎样分工和交换信息

## 学习目标

解释工具分权、消息上下文隔离和任务信息传递，不把多个 Agent 自动等同于更高质量。

## 机制解释

主Agent规划、分派、汇总；分析子Agent查询库存、比价、搜索和制作报告；订单子Agent处理字段补充和订单操作。build_main_agent 将子Agent声明的业务工具从主工具集合移除，因此普通ERP业务通过委派进入子Agent。主Agent仍可持有其他主级工具，不应说它完全没有工具。

YAML描述职责、工具名称模式、预期工具集合、技能和审批策略。加载器解析名称模式后与expected_tools核对，缺失或扩大权限时拒绝装配。分析子Agent没有订单写工具；订单子Agent有写工具，但仍须经过审批授权。工具集合限制可执行操作，不保证模型推理正确。

当前普通同步委派不是把父Agent整段聊天复制给子Agent。锁定SDK的task工具接收 description 和 subagent_type；非fork子Agent以description作为任务消息开始，执行结果再以task工具结果返回父Agent。可信配置与回调沿框架传播，但这不等于共享全部消息。项目还通过上下文注入中间件让主子读取该用户偏好。

```text
用户需求 → 主Agent
主Agent → task(description, subagent_type) → 分析子Agent
分析结果 → task工具结果 → 主Agent
主Agent → 带必要事实的新task描述 → 订单子Agent
```

所以子Agent之间没有自动完整信息传递。主Agent需要携带必要事实或报告引用；订单子Agent应按自己的工具核验物料/供应商，缺数量、价格等必要字段时发起补充。提示词要求这些行为，不能据此声称所有自然语言委派都绝对无信息丢失，需要通过真实案例评测。

消息隔离也不等于文件隔离。同一用户的子Agent可使用同一个用户沙箱；跨用户靠owner作用域隔离。拆Agent增加模型调用、延迟和传递信息失真的可能，简单任务可用单Agent；这里的职责和权限拆分有明确业务理由。

## 代码阅读

[main_agent.py](../../src/agent/main_agent.py) 只读 build_main_agent 中 delegated、main_tools、to_subagents 和 create_deep_agent。再读 [loader.py](../../src/agent/subagents/loader.py) 的 resolve_tool_patterns/build_config/to_subagents，以及 [analyst YAML](../../src/agent/subagents/configs/procurement_analyst.yaml) 和 [order YAML](../../src/agent/subagents/configs/procurement_order.yaml)。

核对订单YAML的信息核验与缺字段要求。可选读本机安装的SDK subagents.py 中 _validate_and_prepare_state 的非fork分支；SDK是底层task工具的实际实现，不必背内部源码。

## 具体案例

分析得到S001报价25.50，主Agent向订单子Agent委派“购买P001，50件，S001，25.50”。订单侧核验业务信息并触发审批。如果描述只有“按刚才结果下单”，隔离上下文的子Agent未必知道“刚才”是什么；应该传必要事实，缺失时重新查或补充，不能假设自动共享聊天。

证据读 [T11](../../tests/acceptance/test_t11.py) 的工具集合、委派和规划用例，[T29](../../tests/acceptance/test_t29.py) 的真实委派ERP查询及偏好注入。稳定测试模型验证机制；它不证明真实模型总能选对任务。

## 自检

1. 分析Agent没有写工具，和有写工具但须审批有何不同？
2. 子Agent拿到的description是谁生成的？
3. 消息隔离是否意味着文件和身份配置也全部隔离？
4. 多Agent有什么额外成本，什么时候不值得拆？

核对要点：能力权限与执行授权不同；主模型生成任务描述；不同资源有不同隔离边界；额外调用、延迟和信息传递成本。

## 面试表达

“我们按职责分配工具，并用独立任务上下文减少无关信息。父Agent通过task描述传递必要信息，再汇总子任务结果。拆分也引入延迟和信息损失，所以业务校验与评测仍然需要。”

## 确认

写一份你认为足够完整的下单委派描述，再删去价格，说明订单Agent该如何处理。明确区分代码约束与提示词要求。
