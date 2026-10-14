# T67 预算与执行证据修复

用户要求预算不要过低，针对死循环另行处理，并修复其余问题。本轮只进行代码修复、回归与零模型真实预检，没有启动新的付费校准，T68 不恢复。

## 新业务预算与循环规则
同一入口 `session_limits/actor_middlewares` 对 base/fewshot 使用一致 solve 协议：每题最多120次工具调用、60次模型调用、600个图步数，输出仍8192token。三个 skill 生成原协议保持30模型/36工具/120步，便于保留已有生成证据；总网关调用保护为30×60+3×30=1890，未重新引入已取消的金额缩小版限制。

重复请求返回同样结果连续8个完整工具回合时，在下一次模型调用前停止并写 termination.json。新的请求/文档、变更事实与状态写入会重置计数；request_id、空计算operation_id/PID与无写入版本号不作为新进展。不同404猜测本身不认定为死循环；总额度仍阻止无限探索。预算/循环终止仍是明确失败，未伪造答案或自动改成成功。

原260步存在隐藏截断：真实锁定DeepAgents图与测试模型的50轮唯一记录发现流程抛GraphRecursionError。补齐测试模型bind_tools后复现，随后600步通过。该测试为SDK组件验证，域工具与模型均为tests内测试对象，不是Actor业务成绩。

## 其余修复
- 提交解析接受唯一完整fenced JSON前后的说明文字，但不改写字段。先解析整段正文，再检查全文竞争对象；多候选、截断父对象、块内垃圾、数组及限额提示均拒绝，不从内部对象补答案。
- Callback与MCP工具账本使用每会话单writer/线程锁，100KB回包并发追加保持每事件一行。
- MCP成功、404、拒绝路径及执行异常均记录原请求/脱敏失败并继续抛错。验证器核对所有调用/参数/owner/thread，成功读仍受完整allowlist；被拒绝错误路径不当作成功越权，不阻止后续纠正路径继续完成业务。
- 结果解析在MCP context真正退出之后进行。加载、执行、cleanup异常保留并继续失败；stdio closed只表示真实context退出，退出异常仍false，sandbox cleanup是否成功独立验证。

## 证据与限制
最新受影响套件 `gdpevo-construction/v4-20261012/task_factory/scratch/runtime/repairs-final-suite3.xml`：121 passed、0 failed、0 skipped。red/首轮fixture问题也保留，包括真实recursion red2；Windows两个补充red报告位于C:/dev/rush-harness/scratch/，没有因自动审批超时重启付费命令。

原cont1/run的63份manifest证据逐字节哈希未变，失败状态、26模型调用及历史费用不改。真实零模型doctor READY：Java、OpenSandbox42两次计算、Mongo保存/回读、反向隔离、404失败落盘、stdio实际退出及容器/网络清理均通过；model_calls=0。结果 repairs-zero-model-doctor1-result.json。独立定点复审没有剩余重要发现。本次代码修复不代表完整业务校准、难度接受、训练有效或Stage3通过。

下一次付费执行必须是独立新目录并冻结新预算/源码，两臂共同使用新协议。当前严格reuse绑定会拒绝变更后的pre-model源码，不应跳过它来恢复旧运行；如复用已有三份技能，需明确记录已核验的输入/生成协议与纯记录、后处理及solve预算差异。旧failed目录不能resume或覆盖。

整体任务gate：`artifacts/tasks/T67/20261014T152504Z/receipt.json` BLOCKED（原cont1冻结源码已不同，且完整校准/Stage3未完成），没有新增模型调用。修复代码可交付，不标T67 done。
