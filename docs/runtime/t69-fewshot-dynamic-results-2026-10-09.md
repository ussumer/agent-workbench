# T69 同监督 few-shot 静态与动态选择对照

复用T65已冻结fewshot技能库及相同完整同组train输入/正确决策，仅改变静态注入与逐回合PlanningTraceMiddleware选择。没有Curator调用/重新训练/生产晋升。20 train复测只作诊断；20开发留出题各3重复、两臂共120test；总160 episode。留出题已用于开发，不是新的封存测试。

## 结果
|指标|静态|dynamic|
|---|---:|---:|
|train复测通过|17/20|16/20|
|train复测均分|0.965385|0.957692|
|开发test通过|38/60 (63.3%)|40/60 (66.7%)|
|开发test均分|0.914103|0.903846|
|test每次重复通过|14/20、13/20、11/20|15/20、11/20、14/20|
|test三次全部成功的任务|7/20|10/20|
|全部阶段模型调用|534|984|
|仅test模型调用|430|790|
|全部阶段保守费用CNY|73.838421|134.388477|

60个task/repeat配对中dynamic得分更高10、更低10、相同40。整题成功仅dynamic成功8对，仅static成功6对。dynamic通过数多2，但平均分低0.010256；不能说全面优胜、统计显著或TRACE完整因果验证。相同Actor额度并非相同总成本，Selector额外调用已计费；动态选择是否值得成本需用户按业务目标判断。

159 scored、1 environment_failed；失败为dynamic repeat3/packages-test-04的GraphRecursionError，达到120图递归上限，属于Actor执行退化/护栏终止，不能解释为服务不可达。该条计0且没有补跑。159 scored全部有completed真实计算，dynamic保存逐回合选择、冻结正文与原始工具观察。静态没有伪造selector轨迹。

## 用量与证据
1518次模型调用，输入21714010/output474104，usage_complete=true，无budget denied；保守全价总208.226898CNY，实际账单未知。此金额含train复测与test、Selector；无新增Curator费用。

运行目录 artifacts/experiments/t69-fewshot-dynamic-20261009-run1；独立verify passed，rows160/test_rows120，输入/评分/manifest/动态证据核对。源码6e44962；任务登记97b444c。T65/T68原结果未改，T68继续blocked不恢复。

首次gate因未载入MODEL环境配置BLOCKED收据 artifacts/tasks/T69/20261009T093517Z/receipt.json 保留；加载既有配置的后续gate结果另见state/HANDOFF。不把独立verify替代必需gate。

最终 gate `artifacts/tasks/T69/20261009T093641Z/receipt.json` passed：8 unit、0 failed/0 skipped及paired-evidence通过。日志hash已核对；state登记done仅表示实验与证据验收，不表示dynamic获胜。
