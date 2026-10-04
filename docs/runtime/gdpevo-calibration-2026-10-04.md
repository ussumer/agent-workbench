# GDPevo 新任务首轮内容难度试跑（2026-10-04）

当前配置 `deepseek-flash`，temperature=0，thinking=disabled。每题单次真实请求，4096输出token上限；无工具、无ERP写入、无TRACE/Curator训练。base只见本题与公开契约；policy-visible附加全部训练政策文字，未提供gold或评分器。

| 题目 | base评分 | 给全规则评分 | 核对的业务错误 |
| --- | --- | --- | --- |
| test-01 | 86.7% | 86.7% | 商品金额等于免邮门槛，仍收300分运费。 |
| test-02 | 46.7% | 46.7% | 提交全部可选件；明知总额超过预算仍标execute。 |
| test-03 | 100.0% | 100.0% | 本次正确补证。 |
| test-04 | 100.0% | 100.0% | 本次正确执行，未因被支配pending报价阻塞。 |
| test-05 | 46.7% | 46.7% | 重复买历史已成交件；违反pit_stop每物料盈余限额；累计预算超额仍标execute。 |

两组均为2/5整题全对、76%加权平均分，10次请求全部返回可评分JSON，无网络/格式失败；保守费用估算合计0.307314元，实际账单未知。失败响应和明知违反约束的解释完整保留。

结论：新题已能暴露业务错误，旧题8/8饱和问题在本内容试跑中未重复；但加权分76%高于GDPevo建议的base约40–60%，给规则也无改善。不能据此称完成校准或实现训练收益。需固定同一题目与评分，后续用具有计算工具的Actor区分一口作答失误和长期操作能力；当前结果只属于no-tools/one-shot条件。

评分边界：T52原rubric含allocation和approval_request两个字段，分配错误可能同时失去审批内容分，故加权分数不能视作互相独立的错误概率。source_selection/历史对账较易得分也抬高均分。保留冻结评分不追改成绩；整题全对及具体错误更能说明本轮业务结果。

原始证据目录：`/mnt/c/dev/rsi-eval/procurement_eval/runs/planning-session-20261004/attempt-gdpevo-calibration-20261004`；其中frozen保存任务/控制面/评分器/运行器，逐题request/response/submission/result/model_calls及manifest均留存。
