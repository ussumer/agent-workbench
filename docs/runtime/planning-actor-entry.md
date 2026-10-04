# 规划Actor入口（组件接线已核验，完整live待验）

T40接线收据：artifacts/tasks/T40/20261003T100854Z/receipt.json。正式chat/resume经过真实Mongo/checkpoint、MCP和Java验证，模型是明确测试替身；本页不声称真实模型规划或Prime已通过。T38按用户要求暂停，编码Agent不执行下列生成调用。

## 开始规划会话

运行中的Demo装配tests/live/stack.py提供独立planning_graph_provider，与课程主子图分离。先用既有/api/demo/session选择账号，然后POST /api/planning/{新thread_id}/goal，只提供budget/demands：

```json
{
  "budget": "2500.00",
  "demands": [
    {"part_id":"P001","quantity":42,"max_lead_days":3,"required":true},
    {"part_id":"P003","quantity":15,"max_lead_days":7,"required":true},
    {"part_id":"P004","quantity":30,"max_lead_days":7,"required":false,"priority":1,"allow_partial":true}
  ]
}
```

该入口只查询真实ERP并保存问题和原始来源，不调用模型。线程不能属于另一用户，有旧课程消息的线程不能切换模式。未配置独立规划provider返回503，不回落课程分析。

然后使用既有POST /api/chat/stream，传同thread_id、唯一request_id和目标说明。此步开始真实模型调用，必须有具体费用授权和完整live验收准备；运行模型ID/URL来自ModelConfig，不从DSV4.1flash简称猜测。

## 计算与审批

公开工具planning_read可按part_id读取必要片段；planning_source读取归档ERP响应；planning_check只验可行性、不提供私有最优解；Agent通过通用computation工具自己建立报价/候选/依赖，不提供固定planner。shell执行、通用task及直写订单在规划工具边界拒绝。动态异步分会话待后续需求验证，不替代为固定委派。

planning_submit逐供应商草案暂停，一次只展示一笔。使用现有chat/{thread}/resume，interrupt_id取当前SSE或state中的ID，resume.decisions只approve/reject。该ID是原pending_action ID；LangGraph同工具连续中断会复用任务ID，不能直接拿内部任务ID作为逐单批准身份。第二单需要第二次批准。拒绝后不会暗中执行剩余单；已写单保留。

GET /api/planning/{thread}/goal查看问题、实际订单和未确定执行状态。MCP使用MCP_APPROVAL_VERIFY_URL和INTERNAL_SERVICE_TOKEN在真实HTTP复核规划批准；Demo装配预分配自己的API端口，不猜服务地址。外部部署自行设置完整verify端点；缺配置拒绝规划写入。

## 尚缺的证据与能力

T38稳定验收与真实Actor持久计算轨迹、预算改变选择性重算、学习空间和组合反事实、TRACE逐回合策略效果、Vue规划目标输入、持续差量对账、inflight崩溃对账及动态异步/refinement均未完成。组件测试不能证明学习或最优规划，研究结论必须来自独立裁判与真实轨迹。

## T41 运行轨迹与文字知识

正式规划装配已接入EpisodeStore/PlanningTraceMiddleware；默认空bank固定基线，未安装人工学习经验。第一次chat绑定goal的bank与模型provenance；同目标跨审批恢复继续同Episode、同bank，不读取新活动指针。每次模型动作前重新选择有序技能ID；有技能时选择器和Actor都计入正式累计预算，空bank记录空选择。

控制面代码入口src/agent/evolution/episodes.py（freeze/set_current/bind/export）与orchestration.py（每回合选择/正文读取/注入/原始事件）。Actor没有bank写入或私有评分工具。freeze仅冻结文字正文，不是完整环境Snapshot；set_current不是评测晋升，不自动写日常技能assignment。模型/选择器回调及工具完整原文与SDK最终可见结果分开归档，导出校验所有事件commit、分块和hash；归档故障使API failed，已发生订单不回滚。

一次API completed、一次批准executed均不赋采购成功标签。Episode保持open/unscored；正式独立Feedback、训练完成标记、Curator层级初始化/成功失败对照、封存对照及真实学习效果另包实施。组件检查的synthetic skill/scripted模型仅为测试数据，不能作为训练经历。

## Vue目标入口

工作台选择账号后点击“新建采购规划”，填写预算（两位小数）并逐项填写物料、数量、最长交期、必需或可选。可选物料可设置优先级及部分采购，供应商拆分独立设置。保存创建独立会话，随后在消息框发送目标说明。创建目标不自动发消息；实际生成仍要求具体费用授权和T38/完整live准备。

目标卡从服务端读取已保存约束和实际订单，聊天/审批恢复结束后刷新；每张供应商订单沿已有批准/拒绝卡单独处理。订单不由前端猜测，查询失败显示错误并保留上次状态，不确定写入提示先核对。切换身份清空规划视图；已有目标修订和持续差量重规划尚待后续包。

## 约束变化后的追加规划（T43）

PATCH `/api/planning/{thread}/goal` 输入例如 `{"expected_revision":1,"budget":"3000.00","refresh_part_ids":["P004"]}`。可用demands列表局部替换原物料约束，新物料仍需新goal。只刷新指定物料真实ERP来源；对所有已写订单逐个真实核对。既有订单缺原批准/原交期快照、丢失或被外部修改时拒绝，inflight/uncertain也拒绝。该请求不调用模型；Vue编辑入口另包。

响应中的problem schema2 commitments是已采购量和原成交价/计划交期，Plan.lines必须仅填写追加量。例如S001已写P00142件/P00315件，预算变2600时追加S002 P00429件，累计2598.50；不可重新购买P001/P003。该数字仅示例，不是运行固定planner。新约束与已写数量/交期/累计花费冲突时明确不可行，无自动取消或改旧订单。公开planning_read/check会显示累计事实和受影响物料，选择与重算仍由Actor完成。

旧审批失效。修订后发新的用户消息可退出旧planning中断并继续新目标，新增草案仍逐单批准，原Episode/bank保持。真实Actor持久计算/选择性重算及学习效果未因此通过，组件模型仍明确scripted。

## 用户批准的新T38接线
默认规划runtime使用ComputationService，公开computation_execute/status/cancel/output/recover。build_planning_actor暂保留kernel参数名兼容已有装配调用，其服务和行为已改为持久JSON+独立进程；旧kernel工具不再进入Actor。read_names显式选择加载数据，save_state只暂存；Mongo CAS发布，失败/取消不覆盖。recover明确撤销API崩溃遗留operation并核验进程树，不自动重放代码。无完整fixed planner，订单仍planning_submit与MCP逐单审批。
