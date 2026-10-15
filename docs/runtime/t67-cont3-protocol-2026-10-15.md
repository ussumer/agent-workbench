# T67 cont3：两个阶段预算统一

用户已要求除死循环外预算不要太低，并于2026-10-15明确继续完整校准。cont2失败因上一修复只放宽solve，skill仍36工具，第一份技能在37/36被停止；这是执行遗漏，不是业务成绩。原19份hash/24HTTP200调用、1,488,742输入/8,092输出、13.617162CNY估算、15completed计算、1失败工具、真实stdio及全部资源清理已只读核对。旧目录不改，0业务题。

同一问题本轮只做一次有证据修正：session_limits(skill/solve)完全一致，均120工具/60模型/600图步/8192输出，8轮重复请求同结果保护也应用于skill。非新事实或新状态不以动态operationID冒充进展。base/fewshot协议仍完全相同，提示/监督/任务/评分器/技术栈不变。两项red复现遗漏，122受影响回归green且0skip；独立定点复审后进入独立cont3。若再出现同类预算/机制失败，停止该方向报告，不再继续补丁或重复启动。

独立目录artifacts/experiments/t67-gdpevo-group025-ds-20261015-cont3，新freeze/实际doctor后3fresh独立技能+15base15fewshot。旧run1/run2/cont1/cont2共74历史调用、38.024514CNY保守估算单列，actual未知；本轮全部费用/失败另算，不混合旧分数或复用无法证明的源码。未自动恢复任何失败进程。

完成后verify本cont3重评分、检查33独立身份、全账本/清理，再官方结构检查及六clean-context质量票>=5通过、最终gate。DS目标仍base .40–.60/lift .10–.30/fewshot<.80；不改rubric凑数，不称封存泛化或训练增益；T68不恢复。
