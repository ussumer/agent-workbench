# train_001 — private construction and review notes

Author: independent task-builder /root/v4_task_train001. Created and updated: 2026-10-12. Status: task construction and evaluator self-checks only; not calibrated or Stage 3 accepted. Main owns integration and Git. This builder owns only train_tasks/001 and scratch/task-builders/train_001.

## Provenance and adaptation

Scenario SCN_025_automotive_procurement_v4, primarily E004 (GDPval 24d1e93f-9018-45d4-b522-ad89dfd78079), with E005 (05389f78-589a-473c-a4ae-67c61050bfca) providing related supplier-risk context, at upstream revision 11e7900cdcac61bc4daf59e65feb238acda98fbf. E004 requests an Excel workbook with vendor NPV sheets and a comparison/recommendation sheet. Original attachment: seed_scenario/attachments/E004/Quotations and volume projection for model I headlamp.docx. E005 concerns a failed nomination and replacement; this ordinary nomination task has no historical contracts, refund or paid-invoice claims. Nothing from V3 gold or evaluators is used.

The deliverable is deliberately adapted from Excel to normalized decision JSON. Original source companies, quotations, fixed 70:30 mix and currencies are not represented as these generated transactions. PRG-021 is synthetic shared ERP data with its actual approved 65:35 mix. Source conventions retained: two headlamps per set, first 100,000 sets tooling recovery across variants, equal upfront R&D allocation, year 1 undiscounted and years 2–4 at 10%. Exact tier equality, revision effectivity, dated FX, UOM, period price multipliers, allocation capacity, engineering timing and currency basis are declared enterprise adaptations exposed in actual public documents. Original headlamp/set and strict tier-boundary ambiguity is resolved explicitly in generated terms, not asserted as uniquely original meaning.

Final seed SHA-256: 5c96071d2d27a9f05be6f50f8b54f68a16e24f6e4ebb671c1d8cbad81205fbb7. Environment is Java/H2 business fact authority; existing Harness retains Mongo/MCP/OpenSandbox/DeepAgents. Builder reads seed on trusted control plane only. Actor accesses domain APIs through runtime tools, never env files, seed, hidden notes, gold or evaluator. No model calls, simulated Actor, paid calibration or training-effectiveness claim was made here.

## Business request and evidence map

PRG-021 initial sourcing is decided as of 2026-10-12 and projected over complete fiscal years 2027–2030. Future projections are valid: the knowledge cutoff applies to issued/approved_at/assessed_at, while effective_period controls which future year a known business record covers. A quotation valid_through 2026-12-15 remains valid for nomination on the decision date and can cover deliveries through 2030. It is incorrect to reject all future fiscal evidence because those deliveries are after the decision date.

The visible input contains only request and schema, with <TASK_ENV_BASE_URL> as environment placeholder. No private SOP, weights or supplied business answer tables appear in the prompt. Required fields, types, cents and tolerance, controlled choices and row identities are in input/payloads/answer_template.json. Domain entrypoints are programs, forecasts, quotes, engineering, documents and suppliers. Program detail links finance policy POL-021 and rates FX-021. No task-shaped full-view or winner API is required.

- Programs: PRG-021 establishes launch 2027-01-01, fiscal years and candidate SUP-010/011/012; no contract/payment records exist for this nomination.
- Forecasts: F-021-{2027..2030}-{base,premium}-r2, approved 2026-10-08, replace r1. quantity_scope is full_fiscal_year. Forecasts supply business N1 demand and independent capacity-comparison denominator.
- Quotes: Q-021-0-{base,premium}-r2 approved, Q-021-1-{base,premium}-r1 approved originals plus r2 approved amendments effective 2028-01-01, Q-021-2-{base,premium}-r1 approved while r2 is revoked. Revoked proposals cannot activate their supersedes links. Shared TOOL-021-j and RD-021-j charge IDs prevent duplicate charges across variant sheets or amendments.
- Engineering: E-021-{0,1,2}-r2 approved 2026-10-09 replaces r1. technical_coverage, qualification_status and required_actions support N3. annual_capacity after other business supports N4; requalification_weeks supports separate N5 readiness. For new tooling nomination this duration already includes tooling preparation; do not add tooling_transfer_weeks intended for reusable tools.
- POL-021: valuation fiscal 2027, first discounted 2028, 10%, tooling cap 100,000 across variants, full approved mix allocation, R&D at award with equal shares. FX-021: CNY per foreign unit by year, plus separate upfront rate. Financial amounts are raw decimal strings.

## Independent answer derivation

Approved demand is 32,000 / 39,000 / 48,000 / 47,000 sets, lifecycle 166,000. Base sets 20,800 / 25,350 / 31,200 / 30,550; premium 11,200 / 13,650 / 16,800 / 16,450. Never interpret October forecast approval as a partial-year quantity.

Commercial binding states: SUP-010 approved amended terms throughout; SUP-011 original in 2027 and amended in 2028–2030; SUP-012 original throughout because r2 revoked. N2 scores this business temporal state for both variants, not quoted prices, currency conversions or document identifiers. N6 alone owns all financial arithmetic including which price value applies.

All current engineering assessments say qualified and cover both variants. SUP-012 nonetheless has an open complete photometry validation action. Preserve both authoritative facts, without changing qualified to conditional or inventing disqualification. technical_admissible means current qualified status with full variant coverage; it does not promise capacity or on-time launch and does not erase an open action. Schema defines this exact interpretation.

Capacity gaps by year: SUP-010 8,000 / 11,000 / 16,000 / 11,000; SUP-011 zero each year; SUP-012 9,000 / 9,000 / 11,000 / 3,000. Readiness from October 12 plus 5/8/17 calendar weeks is November 16 / December 7 / February 8. Only SUP-012 is late, 38 calendar days after January 1.

Finance models a hypothetical full program award to each supplier, regardless of operational feasibility. It must not clip quantity to capacity or pretend late supplier deliveries satisfy launch. Tier counters and tooling opening balances are 0, 32,000, 71,000, 119,000; closing balances 32,000, 71,000, 119,000, 166,000. Tooling eligible sets are 32,000, 39,000, 29,000, 0. SUP-012 graduated pricing crosses the 100,000 boundary within 2029: 29,000 sets at first tier and 19,000 at second, allocated 65:35 inside both bands. Quoted piece prices are multiplied by two; quoted vehicle_set prices are not. Unit-price multipliers apply to materials, not to the separately quoted shared tooling charge. Tooling recovery uses quoted total / 100,000 times eligible delivered sets; no upfront tooling cash is added. Shared quote amendments replace charge terms rather than add liabilities. Period FX converts materials and tooling at each operating payment year; upfront FX converts R&D at award. R&D is paid once, split equally, never volume-mix split. No inflation is invented; stated negotiated period multipliers remain binding.

Each final displayed field is rounded half-up from unrounded Decimal calculations to CNY cents. NPV sums use unrounded components before final rounding. The 5-cent per-field tolerance permits legitimate final cent aggregation choices, not unit or currency errors. Quantities, classifications, dates, memberships and fiscal identities are exact. Operating cashflows exclude upfront R&D; total NPV includes it. No free-form rationale, optional evidence references or recommendation obtains an extra point.

Independent hand spotchecks:
- SUP-010 2027 material cash = 20,800 × 2 × 152.5 + 11,200 × 2 × 125 = CNY 9,144,000. Tooling = 32,000 × 33 = CNY 1,056,000; operating exposure CNY 10,200,000, undiscounted.
- SUP-011 2027 weighted material rate = (25.3147 × .65 + 41.3986 × .35) × 7.12 = CNY 220.321743 per set. For 2028 onward the USD weighted price changes to 36.111905, with respective multipliers and dated rates; r2 is not retroactive to 2027. Upfront R&D = 181,818.18 × 7.12 = CNY 1,294,545.4416.
- SUP-012 2029 material cash = 2 × (29,000 × 19.583355 + 19,000 × 18.01671) × 1.02 × 7.87 = CNY 14,613,637.426218. Tooling = 320,512.82 / 100,000 × 29,000 × 7.87 = CNY 731,506.409086. R&D allocation is half CNY 697,307.6943 to each variant, not 65:35.
- Discount denominators by year: 1, 1.1, 1.21, 1.331. Independently transcribed annual arithmetic yields total NPVs CNY 46,494,759.438392 / 38,551,752.841967 / 46,150,723.919701. Recommendation SUP-011 is both operationally feasible and lowest cost, but remains unscored as a separate reward.

build_gold.py reconstructs all outputs from final seed and records unrounded per-variant/period calculations in derivation.json. validate.py performs separate hand-transcribed weighted aggregate calculations without importing that builder or reading seed. manual_spotchecks.json preserves their unrounded results. Independent variant checks are recorded separately. These are evaluator/construction checks, not learned model successes.

## Outcome independence and tests

Raw weights total 13: N1–N5 weight 2 each; N6 weight 3. Each point is all-or-zero. A wrong scoped program/as-of fails all points; otherwise a business error affects its own output section only. Integer types are enforced including rejecting bool-as-number. Tables normalize by supplier_id/fiscal_period and action/variant sets are order independent; duplicate or unexpected candidate/period membership fails the relevant point. Additional narrative fields are ignored. Details list failed output paths without exposing expected values or private gold.

| Point | Evidence → output | Independent wrong result tested |
| --- | --- | --- |
| N1 | approved full-year forecasts → approved_volume | stale base forecast substituted |
| N2 | dated quote approval and projected effectivity → commercial_coverage | apply SUP-011 amendment to 2027 |
| N3 | current status, variant coverage and open actions → technical_admissibility | erase photometry validation |
| N4 | available allocations versus approved demand → capacity_shortfall | report zero for SUP-010 first-year gap |
| N5 | nomination duration and launch date → launch_gap | erase SUP-012 38-day lateness |
| N6 | actual dated prices/UOM/tier/multipliers/FX/shared charges/policy → financial_exposure | omit upfront R&D from NPV |

N1 and N4 share demand facts but score reconstruction versus supplier allocation deficit; N3 and N5 share engineering but score qualification/actions versus readiness arithmetic. N2 has no price value/UOM/FX checks; finance is not counted twice. Six negative submissions each lose exactly the intended complete point and preserve all other points. Arithmetic sensitivity, reordered rows, duplicates, integer types, zero-score business mistakes and malformed JSON are recorded in scratch. bash eval.sh <submission.json> prints the contracted JSON and exits 0 for scored business mistakes; parse/control failures exit 2. The full output file is never compared for equality.

## Train foundation and difficulty

This is a full business decision, not a tutorial. Solving and comparing this train task anchors recurring experiences for train_002/004 and unseen nomination entities in test_001/003/005: reconcile demand by approval and full-year scope; separate decision-date knowledge from future effectivity; do not choose maximum revision; distinguish pieces, sets and shared charge identity; aggregate both variants for cumulative tiers and tooling cap; keep upfront development separate from tooling operating recovery; use equal development shares and source-aligned discount frame; report technical, annual-capacity and calendar feasibility independently. These are train foundation anchors, not claims that this builder has inspected or solved held-out answers.

Transfer-dependent difficulty comes from reusing those source/financial conventions on different mixes, thresholds, currencies and supplier states. Task-local exploration must identify PRG-021 references across domain endpoints amid 150 programs/18 suppliers/5,560 documents, reconcile 16 forecasts/12 quote sheets/6 engineering versions, and discover policy and FX documents. Concrete traps are an approved but future-only amendment, a revoked newer quotation, mismatched quote UOM, a cumulative boundary inside year 3, and a qualified supplier with an open action and late readiness. N3 is a positive qualification control with varying actions; no forced disqualification is manufactured. Nomination NPV alone cannot demonstrate procurement feasibility. Difficulty remains uncalibrated; no score target or DS performance result is inferred from checks.

## 中文审核说明

本题是 train_001，目标 PRG-021，决策日 2026-10-12，预测覆盖 2027—2030 完整财政年度。独立构造者只写题目 001 与自己的 scratch 目录；未改环境、其他题目、全局状态或 Git。来源为 GDPval E004 的三供应商、双变体、四年 NPV 业务，E005 只提供同组风险背景；本题无历史提名失败、退款或已付发票。原始交付物为 Excel，本题明确改编为结构化 JSON，并未声称原附件数字是真实本程序交易。

最终种子 SHA-256 见英文部分，所有数值取自最终公共记录。原题 70:30 不覆盖生成程序的已批准 65:35；已批准量为 3.2/3.9/4.8/4.7 万套。审批截至日期与未来适用年度是两条时间轴：2026 年已知的 2028 年有效修订可以用于未来计算，不能提前用于 2027 年。SUP-010 全期用批准修订；SUP-011 2027 用原版、2028 后用修订；SUP-012 新版撤销，因此继续原版。商业适用性 N2 只核查业务状态，不重复奖励价格或单位金额计算。

工程记录中三家均为 qualified 且覆盖双变体，但 SUP-012 仍列 photometry validation 待办。忠实保留状态与待办，不擅自把它变成不合格。技术资格、年度产能缺口、上市日期缺口是不同结论；只有 SUP-011 产能与上市时间均满足。SUP-012 新模具提名周期 17 周已含准备，不叠加可复用资产转移周期；就绪日期 2027-02-08，晚 38 日。

财务按每家假设获得全部项目份额计算，不按不足产能砍掉需求。片价乘二、套价不乘二；阶梯计数与模具 10 万套上限跨变体累计，2029 年跨界时分成 2.9 万与 1.9 万套。模具回收按实际年度金额支付，不另加一次 upfront；研发只支付一次并 50:50 分摊，不能按 65:35 分摊。经营汇率随年度，研发汇率用 upfront；第一年不贴现，随后分母 1.1/1.21/1.331。共享费用与修订不能重复累计。最终金额单位为人民币分，每个财务字段容差 5 分，其他数量、日期、分类精确。

评分六项独立结果，原始权重 2/2/2/2/2/3，每项全部通过才得整项分。推荐与总 NPV 不重复评分；推荐文本不评分。每项都有一个只破坏该项的反例，标准答案得 1；人工独立输入的算式复核所有年度现金流与总 NPV。seed 派生脚本与独立手算脚本分别保留，失败读取和沙箱进程限制记录未隐藏。所有这些只是构造/评分器证据，没有付费模型调用，也不是 DS 校准、模型学习收益或 Stage 3 审核结论。

本题提供可迁移训练基础：审批与生效分离、完整年度量、单位归一、共享费用、跨年阶梯和上限、等额研发、分别报告资格/产能/日期。难度还包括在共享大量记录中寻找各域证据并拒绝最新但不适用的来源。题面不复制私有公式步骤，不提供完整任务数据包，不泄露隐藏答案或评分权重。历史归档中的 Unicode 默认 GBK 读取失败改为 UTF-8 后解决；Git Bash 进程受普通沙箱限制后用授权提升运行。没有通过修改环境来强迫答案成立。
