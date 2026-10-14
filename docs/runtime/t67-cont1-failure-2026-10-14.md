# T67 cont1 失败与只读诊断

2026-10-14 用户明确继续。源码 5916f77；独立目录 `artifacts/experiments/t67-gdpevo-group025-ds-20261014-cont1`。真实零模型 doctor ready 后导入 run2 两份原始技能，第三份独立生成成功（9 模型调用、6 completed OpenSandbox 计算）。此前 run1/run2 的 failed 状态和旧费用未改变。

完整 30 次业务校准仅运行首题的一对，连续两次提交失败后自动停止，原正式分母中失败保留 0，剩余 28 次未运行；不能汇报总体均分或提升，也不能进入 Stage3 投票。

|臂|真实轨迹|终止原因|
|---|---|---|
|base|10 模型调用；请求38次工具，17次Java成功回包、15次404、6次限额阻止；0次完成计算|在猜测商业文档编号时耗尽36次工具预算，末次为限额提示，无JSON答案|
|fewshot|7 模型调用；9次Java读取、4次真实完成计算；finish_reason=stop|完整JSON前后带说明文字，被严格解析器拒绝|

初次进度说明误将两个失败都归为工具上限，随后读取终末模型输出已纠正。两者不是相同故障，也不能说明模型完成了业务评测。

另发现 base 的原始 callback 日志第37/38行因并发工具回包写入交错而无效；原字节不修补，正式严格 trace 核验失败。第一次只读 forensic 因此报错，保留 `scratch/runtime/cont1-forensic/attempt1-failure.txt`。第二次独立诊断 `scratch/runtime/cont1-forensic2/report.json` 明确 full_trace_verification_passed=false。可解析的全部26对真实 model input/output 与 gateway用量一致，26调用全HTTP200，1,402,664输入与21,321输出token，没有未知用量。63个原始实验文件哈希均一致。原 MCP 工具账本未记录抛错的15次404，这些失败仍存在完整 messages.json 中；下次须补成可核验失败工具记录。

仅为诊断，在原运行之外提取 fewshot 终末消息中唯一完整 fenced JSON，正文无语义改写，独立原评分器得 **6/13 = 0.461538**。通过需求量、工程资格和交期；未通过来源状态分类、第一年产能缺口及财务计算。原 fewshot 行仍 failed，未把该分补进原实验。这不是全量校准、学习增益或封存泛化。

本轮26新调用，保守估算13.199643 CNY；历史run2复用13调用/7.175898，加run1失败11调用/4.031811，共50历史及本轮调用、24.407352 CNY保守估算。实际账单未知，复用不重复收费。

全部当前 Actor worker、Actor容器、control容器和本次两条专用网络均独立检查为不存在，原 cleanup passed。历史 Actor stdio=false 留存，不据此补造已正常退出。共享Java业务环境保留。没有新模型重试或T68恢复。

19项最终验收组件测试及范围复审通过，但实际最终 gate 必须blocked：校准不完整，严格轨迹不完整，没有六票质量报告。任务保持blocked。后续主线在 T67.md 中明列：提交包装解析、并发轨迹与失败工具记录、预算结束行为及发现契约；未经讨论不启动新付费轮。

最终 gate：`artifacts/tasks/T67/20261014T140612Z/receipt.json` BLOCKED；实际执行新最终验收入口并指向 cont1，新增模型调用0。
