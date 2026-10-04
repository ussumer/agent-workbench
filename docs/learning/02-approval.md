# Part 02 用户批准怎样约束实际下单

## 学习目标

能解释批准绑定具体操作、状态转换与原子更新、工具执行授权三种不同机制。知道前端确认框只是展示，真正的约束在后端。

## 机制解释

用户说“帮我下单”与批准一份具体订单不同。模型可以提出候选参数，但不能自行赋予写权限。信息不足时 request_order_info 发起补充中断；参数齐全后，框架的 interrupt_on 在订单写工具执行前暂停图。API 保存审批记录并给前端展示，用户批准后再恢复图。

批准记录绑定 owner、thread、interrupt、tool、目标、冻结参数和 operation_id。参数规范化后保存字节与哈希。执行前重新规范化实际参数并核对哈希：批准50件却调用100件，authorize 返回 PARAMETERS_CHANGED，不发送订单写请求，变更需要重新审批。

```text
候选订单 → 图中断 → 保存冻结参数 → 展示审批
用户批准 → 可信恢复配置 → authorize 检查 → MCP 授权核验 → ERP
```

状态机表达哪些转换合法，例如 pending→approved 或 pending→rejected。ALLOWED_TRANSITIONS 的 frozenset 是不可变规则集合，can_transition 检查规则；集合本身不提供并发锁。真正处理两个页面同时批准的是 Mongo 条件更新：查找条件包含当前 status=pending，修改状态与匹配检查作为一次原子操作，竞争失败者得不到匹配记录。同 request_id 的重放与不同请求的第二次点击也要区别处理。

review 函数只是整理审批展示信息。authorize 校验本次调用能否使用批准，包括参数一致性。verify_internal 核对 MCP 票据与持久审批记录的 operation_id、tool、target、payload hash 和状态。不要把这三个函数当成同一个“审核”。

写入前还有 WriteApprovalMiddleware。没有可信 approved_interrupt_id、归属不对或参数不一致时，返回拒绝结果；通过后才调用 MCP 写通道。冻结参数不是依赖模型自己记住；身份与授权通过服务端配置和通道传递。

## 代码阅读

1. [订单 YAML](../../src/agent/subagents/configs/procurement_order.yaml)：只看 interrupt_on 和 allowed_decisions。
2. [service.py](../../src/agent/approval/service.py)：先看 record 保存什么，再看 authorize 中 canonical_bytes/matches_bytes，最后看 _decide、review、verify_internal。
3. [models.py](../../src/agent/approval/models.py)：看 PendingStatus、ALLOWED_TRANSITIONS、matches_bytes。
4. [store.py](../../src/agent/approval/store.py)：看 transition 的查询过滤和 find_one_and_update。
5. [middleware.py](../../src/agent/approval/middleware.py)：看 awrap_tool_call 调 authorize 后才进入 _execute。

先跳过摘要金额格式、签名编码和完整 HTTP 通道实现。不要把 SDK 人工中断与自定义执行授权混成一层。

## 具体案例

A、B两个标签页同时批准同一动作。两个请求可能都先读到 pending；随后条件更新只有一个能从 pending 改为 approved。另一个不同请求被拒绝。执行阶段仍可能因恢复或响应丢失重试，因此这一层不能单独保证“订单只创建一次”，需要 Part03 的 ERP 幂等。

参数篡改证据读 [T12](../../tests/acceptance/test_t12.py) 中 PARAMETERS_CHANGED 断言，并找同请求重放、并发决策案例。看测试检查拒绝与业务写入，而不是只检查弹窗。

## 自检

1. approved=True 缺少了哪些信息？
2. 参数改变是在前端拦截，还是执行前核验？
3. frozenset 与 Mongo 条件更新分别解决什么？
4. 用户批准一次，能否证明 ERP 永远只收到一次尝试？

核对要点：具体参数和操作归属；后端校验；合法转换规则与原子竞争；批准一次不排除执行重试。

## 面试表达

“审批绑定冻结的订单内容。执行前核对实际参数，改变就拒绝；审批状态通过数据库条件更新避免重复决策。前端负责展示，服务端负责授权。”

## 确认

写出50件变100件的拒绝位置，再用两列区分“状态是否合法”和“并发谁能成功”。解释通后确认本章。
