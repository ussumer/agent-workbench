# 简历与面试口述稿初稿

## 主项目：采购 Harness 与自进化评测闭环

演示入口：[真实证据回放 Demo](evidence-replay.html)。现场先切换同一题的 fixed / skills / dynamic，再打开 computation trace。

**项目描述版**

独立搭建面向采购库存场景的 Agent Harness，接入 Java Mini-ERP、MCP 网关、MongoDB、LangGraph 和 OpenSandbox，支持审批、持久计算、工具执行和失败恢复。针对报价版本、ERP 权威字段、包装运费、历史承诺等困难约束，设计 train/validation/held-out test 隔离、独立 judge、轨迹反馈、候选技能 bank 和动态 selector。固定同一模型与任务集重复评测，held-out 均分由 fixed 的 0.758974 提升到 dynamic 的 0.888462，完整成功由 7/60 提升到 35/60；保留环境失败和格式失败，不自动修改生产 assignment。

**更短的一条简历版**

搭建带 ERP、MCP、Mongo、OpenSandbox 和 LangGraph 的采购 Agent Harness；将轨迹反馈、技能候选、验证选择和动态决策接成闭环，在 20 题困难采购 held-out 集上将 fixed 均分 0.758974 提升至 dynamic 0.888462（每 arm 3 次重复，失败保留在分母）。

## 副项目：在线微课平台后端

**项目描述版**

参与整理并扩展基于 Spring Cloud 的在线微课平台，覆盖认证、用户、课程、学习进度、积分、优惠券和交易等 17 个 Maven 模块。重点完善优惠券发放链路：用 Redis Lua 完成库存和限领预扣，以 RabbitMQ outbox 处理异步投递，用 message ID、数据库唯一约束和事务条件更新抵御重复消费；补充 Compose 部署、配置生成、初始化 SQL 和升级脚本。

**更短的一条简历版**

基于 Spring Cloud 搭建在线微课平台后端，覆盖 17 个业务模块；重点实现优惠券 Redis Lua 预扣、RabbitMQ 异步投递、MySQL 事务幂等和失败恢复，并整理单机 Compose 部署与迁移脚本。

## 30 秒自我介绍

> 我现在大二，主要做过两个方向的项目。一个是采购 Agent Harness，我把 ERP、工具调用、沙箱和独立评测接起来，再做训练反馈和技能选择，重点研究模型在多约束任务里为什么会错、怎么验证改进。另一个是 Java 微服务的在线微课平台，我主要关注优惠券发放、消息重复、缓存和数据库之间的失败边界。两个项目一个偏 Agent 工程，一个偏后端系统，我都比较重视把结果和证据留下来。

## 3 分钟展开主项目

> 最开始我发现，普通 Agent 在采购题上经常能给出一段很像答案的文字，但报价版本、运费和审批范围经常对不上。于是我先做了独立 judge，不让模型自己给自己打分。之后把失败输出、允许使用的训练反馈和执行轨迹交给 Curator，生成隔离的技能候选，再只用 validation 总体结果决定是否保留。最后把冻结的 bank 放到 held-out test 上，test 期间不再反馈。
>
> 当前 strongest result 是 fixed 0.758974，skills 0.876923，dynamic 0.888462。每个 arm 的 test 跑了 3 次，失败没有被删掉。这个结果说明在当前困难任务集上，技能和状态选择有明显提升，但我不会说它已经证明了通用自我学习，也没有把候选自动装到生产。
>
> 真实执行部分还会把计算放到 OpenSandbox，状态放在 Mongo 的版本化 JSON 里，审批和订单写入仍然走 ERP/MCP。这样即使进程重启，也能知道这次计算读了哪个版本、写了什么、为什么没有发布。

## 面试官追问时的回答

**“这是不是 prompt engineering？”**

> 有 prompt 变化，但项目不止是改 prompt。候选有独立版本、hash、验证记录和 bank 绑定；test 不回流，执行结果也要经过工具和 judge。它仍然不是权重微调，我会把它叫做可审计的运行时技能演化。

**“0.888462 能说明统计显著吗？”**

> 不能。我有三次重复和同题对照，所以能说这是工程上的重复提升信号；但当前没有把它写成统计显著性结论。

**“你负责了多少代码？”**

> 我会按自己实际能讲清楚的模块回答：评测 runner、任务和独立 judge 接口、技能候选/选择证据、持久计算和演示链路。课程原始代码或没有亲自改过的模块，我不会全部算成自己的工作。

**“为什么不用一个普通 API 调用？”**

> 如果只看 v3 输入输出，普通 API 可以做单题；但它无法证明审批、持久状态、工具执行、失败恢复和技能候选是否真的生效。Harness 的价值是把这些边界变成可重放的运行证据。

## 简历使用建议

- 主项目放前面，副项目放后面。
- 技术栈只列自己能解释的：Python、LangGraph/DeepAgents、MongoDB、OpenSandbox、MCP、Java、Spring Cloud、Redis、RabbitMQ、MySQL。
- 不写“实现通用自我学习”“达到论文效果”“生产自动进化”。
- 面试前准备一条 fixed 失败、一条 skill diff、一条 dynamic trace 和一个副项目重复消息案例。
