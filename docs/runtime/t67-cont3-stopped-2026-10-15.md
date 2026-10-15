# T67 cont3 已止损（2026-10-15）

源码78695e2，新目录artifacts/experiments/t67-gdpevo-group025-ds-20261015-cont3，实际源码副本和SHA保存并与Git匹配。三份新技能已生成、均finish_reason=stop，各9/2/10次完成计算。

仅完成首题：base在128/120工具额度结束，120次server记录中36成功、工具消息83次404、8次被额度阻止，106个不同猜测路径，0次成功计算，failed0保留；fewshot真实提交独立重评分8/13=0.6153846154、MCP/计算/清理核验通过。只有单题，不支持全量难度或训练效果。

按照同类故障再次出现就止损的已声明边界，对已验证的cont3主进程SIGINT停止，未补跑。首次cwd安全断言未发送信号，WSL实际路径/dev/rush-harness-training，核对唯一命令后中断成功。原result.json因KeyboardInterrupt仍status=running，不手改原manifest；独立scratch/runtime/cont3-stopped-audit.json明确operator_stopped_incomplete，主进程已退出、所有六个scope worker/Actor、control和专用网络均观察不存在，control cleanup passed。下一题base部分目录及所有调用保留，原74份run manifest哈希一致。

cont3共81网关请求，77有usage、4个HTTP503无usage（6/27/29/36），没有缺失finish；已知输入4405066/输出55698，已知部分保守估算41.149440CNY，usage_complete=false，actual未知。连历史run1/run2/cont1/cont2共155请求，已知部分估79.173954CNY，不能把四个未知费用当0。

零模型实际客户端检查证明：表层max_retries=0，底层sync/async SDK仍2，构造后赋值未生效。44个技能网关请求对应40个逻辑模型回调，不能声称无重试校准通过。此前进度中“未见网络故障”只据成功产出，已纠正为保留4个503未知usage。

cont2先前在skill37/36失败，是上一修复只放宽solve的遗漏：24请求、估13.617162CNY、0业务题，原19hash/账本/资源清理均已核对。预算随后统一skill/solve120工具60模型600步和8轮重复结果停止，122回归及独立复审通过后才启动cont3。现在base变换ID不断404又暴露停止判定问题，按约定不继续叠补丁。

未完成问题进入T67任务：语义信息增量/连续无效探索判定，公开真实文档ID可发现性，构造时真实SDK重试配置及请求-回调关系，原生中断收据。完整30题/六票Stage3缺失；不修改rubric凑分、不晋升、不称封存泛化。不得自行恢复模型费用或重复生成技能，须用户讨论明确恢复。T68不恢复。原失败证据不混入代码提交。

最终gate `artifacts/tasks/T67/20261015T040106Z/receipt.json` BLOCKED（exit2），指向cont3；无新增模型调用。本次cont2+cont3共新增105请求，已知部分保守估54.766602CNY，4个usage未知；加旧50共155请求，已知部分估79.173954。
