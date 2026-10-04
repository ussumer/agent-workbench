# MCP 工具契约 v1

使用课程的 FastMCP 网关和 httpx AsyncClient；公开八个 ERP 工具。Streamable HTTP 路由 `/mcp`，具体尾斜杠行为由 SDK 启动测试固定。MCP 协议封装交给官方库。

## 工具映射

| 名称 | 模型可见参数 | Java 映射 | 权限 |
|---|---|---|---|
| supplier_query | q?、active?、page=1、page_size=20 | GET /suppliers | 读 |
| part_query | part_id | GET /parts/{id} | 读 |
| part_search | q、page=1、page_size=20 | GET /parts | 读 |
| part_by_supplier | supplier_id、page=1、page_size=20 | GET /suppliers/{id}/parts | 读 |
| inventory_warning | page=1、page_size=20 | GET /inventory/warnings | 读 |
| order_search_details | order_id?、supplier_id?、page=1、page_size=20 | GET /orders | 当前用户读 |
| order_create | supplier_id、currency、lines、note? | POST /orders | 必须审批 |
| order_update | order_id、expected_version、supplier_id、currency、lines、note? | PUT /orders/{id} | 必须审批 |

参数类型按 ERP 契约定义。禁止模型提供 actor、grant、operation_id 或 service token；它们从运行时可信上下文注入。GET 默认过滤不是权限检查，Java 仍按 actor 限制订单。

模型接收统一工具结果：

```json
{"ok":true,"data":{"items":[],"total":0,"page":1,"page_size":20},"error":null,"request_id":"req-..."}
```

业务/网络失败：`ok=false,data=null,error={code,message,retryable,details}`。不能将失败转成空列表成功。调用方保留协议级异常与业务错误的区别。

## 生命周期与调用约束

- 服务启动创建连接池、停机关闭；连接 3 秒、读取 15 秒、总工具预算 20 秒，均可配置。
- 读请求最多额外重试 2 次，指数退避；写请求只有相同 operation_id 和冻结字节的幂等重发才可重试。
- 每次调用通过上下文传播 actor/grant；禁止修改共享客户端全局默认 headers 导致串用户。
- 未识别工具或不匹配 schema 启动报错，不能静默不注册。
- Gateway 工具返回完整小型结构；超大结果由 Agent offload 保留原文件，不在 MCP 中随意丢字段。
- `chart_generator` 是 Agent 层对远端图表 MCP 的统一入口，不计入这八个业务工具。

## 审批授权约束

`interrupt_on` 负责图中断，服务端审批记录负责“这个用户批准了哪些参数”。通过图恢复并不允许任意新的写参数自动继承批准。

API 保存 pending_action：owner、thread、interrupt_id、tool_call_id、tool_name、冻结 JSON 字节/hash、operation_id、before_snapshot、状态。resume 接口只接受 approve/reject；改参数必须形成新候选动作和新审批。工具执行器取批准记录，验证完整匹配后向 MCP 附加短期内部授权；MCP 向 API 内部验证端点确认 owner/action/hash，随后调用 Java。

内部端点不暴露前端，需独立 service token；批准令牌不进入模型消息、SSE 或沙箱。一次授权只对应一个 operation_id；重放同一操作可查询原结果，不能授权其他内容。Java 不公开宿主端口给沙箱网络，沙箱也没有网关/ERP 凭据。

测试必须证明：直接调用写 MCP 无审批被拒、approve 后仅写一次、reject 零写、改参数不能沿用授权、demo-b 不能使用 demo-a 的授权。

## T39 规划授权的版本检查

规划grant签名内增加可选approval_ref（thread_id/interrupt_id），不是模型可写参数。携带该字段的写入必须调用MCP_APPROVAL_VERIFY_URL（完整 /internal/approvals/verify URL），使用INTERNAL_SERVICE_TOKEN。缺配置/不可达/拒绝或响应绑定不一致都不转发Java。原课程grant字节格式和工具参数保持兼容；历史课程grant原实现仅验签，不能称已通过内部HTTP复核。T39为规划grant实现真实复核，未来统一课程路径需另包验证。

内部API验证goal/revision/proposal及当前执行租约；目标更新后旧grant即使未过期也被拒。规划组件使用实际business envelope判断结果，retryable失败按不确定执行保留锁与operation，避免已提交但响应丢失后再次采购。影响T12及MCP写工具回归。
