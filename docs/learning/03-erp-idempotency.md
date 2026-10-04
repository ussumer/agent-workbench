# Part 03 ERP 怎样处理事务与重复请求

## 学习目标

区分事务、业务操作幂等和乐观并发控制。能够解释“已提交但响应丢失”及“相同参数的新采购”。

## 机制解释

数据库 session 是一次数据库交互的环境，不自动识别重复业务。事务解决一组写入的一致提交：订单、订单行和 operation 结果一起提交，失败一起回滚。第一次事务成功后，第二次创建请求仍然可以开启新事务，所以只用事务不能防双单。

本项目在形成审批记录时生成稳定 operation_id。同一操作的重试沿用它，ERP 用 owner+operation_id 唯一键识别重复。操作记录包含正文hash、操作类型、目标和原成功响应；相同ID、相同内容返回原结果，不再新建订单。相同ID换正文、类型或目标则返回冲突。

```text
第一次 op-123 → 创建订单和操作结果 → 同事务提交 → 响应丢失
重试   op-123 → 找到已完成记录 → 返回原订单
新采购 op-456 → 即使内容一样，也是一笔新操作
```

幂等记录与订单必须在同一事务中保存，否则可能出现“订单已提交，操作记录没保存”，重试又创建一单。并发两个相同操作还依赖数据库唯一约束和冲突后的结果读取，不能只在应用里先查有没有。

修改订单是另一种竞争：两个请求都基于version=1修改，数据库用条件更新并加版本。只有一个成功；另一个发现旧版本不匹配，需要读最新信息并重新审批。幂等键回答“这是不是同一操作”，版本回答“你依据的订单状态是不是最新”。

金额用 BigDecimal，由服务端计算总额。模型提供的最终总额不能作为可信业务结果。订单创建和修改在这个Demo中不改变库存；不要在面试中扩展成完整库存扣减系统。

## 代码阅读

先读 [ERP契约](../plan/contracts/erp.md) 的事务和重放段。再读 [OrdersService.java](../../erp/src/main/java/com/rushharness/erp/orders/OrdersService.java) 的 create/update、重放判定和重复键恢复，沿调用看 [OrderRepository.java](../../erp/src/main/java/com/rushharness/erp/orders/OrderRepository.java) 的 operation 查询与版本条件更新。

数据库约束看 [schema.sql](../../erp/src/main/resources/schema.sql)。最后回看 [approval/models.py](../../src/agent/approval/models.py) 中 operation_id 如何产生。暂时跳过分页、Controller样板与JSON序列化细节。

## 具体案例

批准S001的P001，50件，单价25.50。订单总额为1275.00。ERP已经提交但客户端未收到响应，重试原ID返回相同订单。明天用户再次买50件，新审批产生新ID，允许新订单。

证据入口：[OrderConcurrencyTest.java](../../erp/src/test/java/com/rushharness/erp/OrderConcurrencyTest.java)、[OrderApiTest.java](../../erp/src/test/java/com/rushharness/erp/OrderApiTest.java) 与 [T21](../../tests/acceptance/test_t21.py)。检查订单数量、order_id、冲突状态和金额，不只看最终回复。

## 自检

1. 为什么事务成功后重试仍可能创建第二张单？
2. 为什么不直接拿订单参数hash当唯一采购标识？
3. operation_id、request_id、expected_version分别解决什么？
4. 同ID不同参数应该返回原结果还是拒绝？

核对要点：新事务仍可创建；相同内容可代表新采购；客户端去重、业务重试、旧状态竞争各不同；拒绝内容冲突。

## 面试表达

“订单和幂等结果同事务保存，稳定操作ID识别重试，唯一约束处理并发。网络重试返回原结果，新采购生成新ID。修改订单再用版本条件更新防止覆盖别人修改。”

## 确认

自己画两条时间线：响应丢失后的重试、两个version=1的并发修改。标出数据库决定结果的那一步。
