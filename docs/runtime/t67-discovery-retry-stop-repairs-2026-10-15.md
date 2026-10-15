# T67 无效探索、SDK重试与原生中断修复（2026-10-15）

用户“你解决一下”授权的代码修复已完成组件验证。没有新增付费模型调用，没有重生成技能，也没有修改旧cont2/cont3失败运行。T67整体仍blocked，T68不恢复。

## 实际改动与原因

- 原cont3 base在128次工具请求中出现83个404。旧guard把新猜测路径算作新进展；现在按实际返回事实判断，404/空页/重复记录不重置，8个完整无进展回合或12个无进展工具结果在下一次模型请求前停止。原始轨迹离线重放在第46次工具请求（4回合/16个无进展返回）触发。此数字只证明能阻断该已知空转，不能当作业务改善。
- sourcing_read只补公开协议：精确返回的document_id、documents没有list/search路由、total=0为空、过滤及分页；无任务答案/SOP注入，没有修改ERP事实或评分器。
- T67计算响应附真实owner/thread/session的Mongo状态哈希；operation/PID/base_version/version不计进展。同值覆盖虽然版本递增仍会停，真正变化的hash和计算输出可以继续。宿主没有代Actor做财务计算。
- ModelConfig.create_chat_model(max_retries=0)在创建ChatOpenAI之前传入；doctor/run核实真实sync/async root客户端均0。原来构造后给model.max_retries赋值并未改变SDK的2次默认重试。无参数调用保持兼容，未改变模型身份/依赖版本。
- run原生处理KeyboardInterrupt/CancelledError，落stopped、CLI退出2、当前scope及调用区间、独立stop-receipt和manifest。保留已经scored/generated/failed的结果，仅started改interrupted。实际MCP/stdio/control清理单列，缺失标unverified；取消优先普通执行/清理/退出错误，各异常证据保留。

## 验证证据

目录前缀：gdpevo-construction/v4-20261012/task_factory/scratch/runtime/。

1. discovery-final-green.xml：五个直接受影响套件115 passed、0 failed/errors/skipped，包括真实cont3轨迹、真实安装ChatOpenAI/OpenAI SDK在本地httpx 503传输下sync/async各只发一次请求，不访问模型服务。
2. 复审新发现取消优先级后，cancellation-priority-red.xml两例确实失败；修复后的repairs-cancellation-final-green.xml：受影响Actor套件38 passed、0 failed/errors/skipped。前一项与本项有重叠，不合计为153项；共覆盖117个不同用例。
3. discovery-zero-model-doctor1-result.json：status=ready、model_calls=0。真实Java认证读取、OpenSandbox两次42计算、Mongo保存/回读、状态哈希与status一致、失败404落盘、公开MCP描述、隔离和清理均通过。MCP sandbox container_absent=true；控制容器与两个网络清理通过。预检目录discovery-zero-model-doctor1，未覆盖旧probe。
4. 最终定点复审未发现本次修改的剩余重要问题。原失败red报告及fixture环境错误保留；未拿scripted/本地transport结果冒充真实Actor成绩。
5. 必需整体gate artifacts/tasks/T67/20261015T055339Z/receipt.json：blocked，检查exit2；指向原cont3。实际拒绝原因是冻结runtime源码已改变，没有跳过绑定。完整30业务校准和六票Stage3也仍未完成；不标T67 done。

关键证据SHA-256：

- discovery-final-green.xml: 49d0ff7f1278bc3bdd73e7cab330ec72ab4198bddc89ce14d08ddf02bff55c90
- repairs-cancellation-final-green.xml: ce05079c68679cb08db1923ff9e78be0aa4f74e8694a0b95aa5d8d9c455d3dfc
- discovery-zero-model-doctor1-result.json: 89829b3108bc3faf529acf31f48d28ad9d5ee25df8295f08f3a6266c5714b6fe

## 明确保留的未完成边界

外部procurement_eval.budget_proxy的daemon HTTP线程不等待在飞请求；关闭账本后迟到usage不能保证归档。本轮未扩大到外部工作区改造，停止收据显式late_gateway_results=unverified，预留与未知usage保留，不将未知费用记0。已在T67登记下一次付费前的本地慢回包中断检查及处置要求。可处理的Python中断收据不覆盖SIGKILL/宿主断电。

没有恢复付费运行：cont3仍只是3技能+首题配对后的失败现场，原stale running与manifest不回写。历史155请求、已知部分估算79.173954 CNY和4个未知usage原样保留，本次新增模型费用0。下一次付费需独立新目录/新冻结协议；当前修复不是完整V4接受、训练有效、封存泛化或生产晋升。
