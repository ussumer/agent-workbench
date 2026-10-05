# 四技能 bank 接入真实逐回合 Actor：T59 检查点

## 2026-10-05 放宽预算后的完整 runtime

用户撤销此前每 attempt 2 CNY 的人工限制后，runner 支持显式记录更高的 `--total-cny`、`--per-attempt-cny` 和
`--actor-turn-allowance`。新 attempt
`/mnt/c/dev/rsi-eval/procurement_eval/runs/planning-session-20261005/attempt-dynamic-tool-20261005f`
使用 `total=100`、`per-attempt=10`、`actor-turn-allowance=48`，完成 19 个 Actor 回合和 38 次模型调用，保守费用
`4.524831 CNY`，无预算拒绝。

T59 证据核验与 gate 均通过：selector 每回合调用一次，共 19 次；共 5 次 OpenSandbox computation completed；
第 14 回合从 `planning_packages_strategy` 切换到 `planning_revisions_strategy`，不是固定单技能序列。首轮和预算修订后的
最终规划都通过独立 judge，最终产生 2 个订单。Gate 收据为
`artifacts/tasks/T59/20261005T045819Z/receipt.json`。

这证明动态逐回合选择和真实工具执行 runtime 已接通；不证明学习增益，也不证明 v3 业务字段已适配生产规划合同。

## 2026-10-05 新 attempt 结论

在零模型回归 22/22 通过后，使用新的 attempt
`/mnt/c/dev/rsi-eval/procurement_eval/runs/planning-session-20261005/attempt-dynamic-tool-20261005e`
和 24 个 Actor-turn allowance 做了一次真实试跑。环境冻结、Java ERP、MongoDB、MCP、OpenSandbox、Agent Protocol
均启动并留下证据；真实模型调用 19 次，保守费用 `1.466586 CNY`。第 20 次请求由评测网关返回不可重试的
`429 configured model budget exhausted`，runner 保留了 SSE 的 `primary_failure`，没有再被空 proposal 的异常遮盖。

该 attempt 仍是 `terminal_status=failed`，不能作为 T59 完成证据；生产 assignment 未改变，`learning_gain_proven=false`。
旧 attempt 与本次 attempt 都保留，按止损规则不再增加额度或重复同一真实试跑。验证器对本 attempt 的 417 文件 manifest
在 60 秒内未结束，未将超时当作通过。

四个由 T58 Curator 生成的 scoped skills 已能完整加载到隔离 Mongo bank，原生产 `PlanningTraceMiddleware` 在每个模型动作前根据当前历史选择有序技能，再注入精确正文。没有修改生产assignment，也没有声称v3报价/包装等字段已接入真实ERP规划合同。

真实结果 `/mnt/c/dev/rsi-eval/procurement_eval/runs/planning-session-20261005/attempt-dynamic-tool-20261005d/actor/result.json`：

| 项目 | 结果 |
|---|---|
| Actor完整回合 | 8 |
| selector尝试 | 9（最后一次被网关拒绝，未计入成功请求） |
| 成功模型请求 | 16（8 selector + 8 Actor） |
| OpenSandbox计算 | 2次completed，第二次read_names=[model]复用已提交数据 |
| 实际订单 | 首单S001，2091.00元；预算修订后未完成完整目标 |
| 选择序列 | 8次均为planning_packages_strategy |
| 本轮费用 | 保守1.065591CNY；实际provider账单未知 |
| 完整目标 | failed；未晋升、未证明学习收益 |

发现的问题：

- 16请求额度包含selector和Actor，动态技能组只有8次Actor动作，后续预算修订在第9次selector处耗尽；不能把此结果和16次无selector的控制直接比较，也不提高额度重跑掩盖失败。
- bank按业务组生成，packages技能仍包含来源、ERP、历史、审批等通用规则；8次选择不变，说明本轮没有证明状态变化带来的组合收益。
- 下游runner在失败后假设final_goal.proposal存在，最终TypeError遮盖主失败；原API失败/预算拒绝均保留在Episode与日志，报告主因为请求额度耗尽。
- v3字段生产adapter缺失，本轮是运行时接线试跑，不能算v3模型准确率。

验证：12协议/归档测试通过；最终单条`test_unfinished_real_actor_cannot_pass_evidence_gate`通过，失败运行返回blocked。gate175812/180744/181435失败保留；182044旧passed只支持部分协议，判定已纠正，不可用于登记T59完成。当前T59 in_progress，receipt=null。

环境零调用失败a/b/c已保留：dotenv缺失、Protocol路径、课程Agent图表工具装配。`planning_only`只让规划实验免于装配无关课程Agent；服务仍是真Java/Mongo/MCP/OpenSandbox/DeepAgents，不提供固定planner。设置失败现在也进入try/finally，结清预留并归档；a历史2元预留已根据零调用证据结清。服务均已退出。

下一轮先讨论操作级拆分与同协议执行预算，之后才新增模型实验；不重复T57/T58 train，不恢复T51/T38，不倒用test选技能。

2026-10-05 runner 修复：已保存的 SSE 错误先于最终 proposal 读取，`primary_failure` 保留规划流的错误 code/message，不再由空 proposal 的 TypeError 遮盖。`--actor-turn-allowance=16` 现在对非空 bank 计入 selector 的 16 个请求，记录总上限 32；空 bank 为 16。摘要、重试同样消耗总额度，2 元硬限额仍可能提前终止。这是执行机会预算，不能冒充等总成本实验。未新增付费运行，不能据此改变上述 failed 结果或登记完整验收成功。
