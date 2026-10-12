# PRG-040 supplier-change decision record — private construction notes

Author: independent task builder /root/v4_task_train005. Created and updated 2026-10-12. Task: train_005, task_group_025, SCN025. Construction verification only; no live Actor/model calls, skill generation, calibration outcome, Stage3 vote or T67 completion claim.

## Lineage and accepted adaptation

Original E005 is GDPval task 05389f78-589a-473c-a4ae-67c61050bfca, source revision 11e7900cdcac61bc4daf59e65feb238acda98fbf. E004 (24d1e93f-9018-45d4-b522-ad89dfd78079) anchors four-year headlamp quote analysis, tooling recovery and NPV. Source prompt/attachment lineage remains in seed_scenario/attachments/E005/raw-source.json and notes/E004.md,E005.md. Original E005 asks for Word escalation and CPO comparison in INR. This accepted adaptation delivers a normalized machine-readable JSON business decision record, not an unchanged Word/Excel artifact workflow; CNY replaces source INR under generated currency records. No V3 answer, checker, fixture or decision rule was used.

The generated public data are synthetic enterprise records, not original transactions. Selected PRG-040 lifecycle differs materially from source E005: planned engineering redesign supersedes the nomination, not an invented four-crash failure. Actual executed nomination and approved replacement authorization control the permitted scope, not the original narrative's future-program ban. Source ambiguity around return of 30% is explicitly resolved by the construction financial contract: 0.30 of actual settled eligible tooling/R&D principal. Deposit fraction is separately 0.30 and is not applied twice. All synthetic conventions are disclosed in adaptation.md, financial-contract.md and env/schema.md.

Final env/seed.json SHA256: 5c96071d2d27a9f05be6f50f8b54f68a16e24f6e4ebb671c1d8cbad81205fbb7; seed20261012025. Actual counts:150 programs,18 suppliers,2400 forecasts,1800 quotes,900 engineering,80 contracts,80 payments,5560 documents. Environment readiness was approved by parent with env-builder handoff and real Java/H2/separate-container evidence. Builder inspected final seed read-only as control-plane reference; Actor must use the authenticated public business interfaces and real OpenSandbox computations, never seed access or host substitutes.

## Business task and material map

Visible input: real CPO request for PRG-040, as-of2026-10-12, full fiscal2027-2030, shared <TASK_ENV_BASE_URL>, and input/payloads/answer_template.json. Schema defines exact nested objects, monetary integer cents, stable IDs, ISO dates, controlled enums, strict booleans, and normalized table/set ordering. No solution SOP, facts, weights, private gold or call order is shown. Optional recommendation and incumbent_reference_exposure are unscored.

Public programs/suppliers locate relationships. Forecast interfaces provide full-year approved and draft history: all eight F-040-year-variant-r1 are approved, r2 are drafts. Quote interfaces/documents provide commercial applicability, currencies/UOM, shared charge IDs, period multipliers and tiers; SUP013 approvedr1 survives draft r2; SUP014 approvedr2 replaces supersededr1; SUP015 approvedr1 covers2027 and approvedr2 begins2028. Engineering E-040-0/1/2-r2 are approved, r1 superseded. Contracts C-040-nomination and C-040-change establish title, refund, scope, launch/qualificationdeadline and180000CNY upfront transition. PAY-040-tooling/rd establish settled principal and actual refund receipts. POL-040 establishes100000set toolingcap and2027undiscounted/10%subsequentyears; FX-040 supplies upfront and per-year CNY/USD/EUR rates.

As-of means knowledge cutoff for approvals/issued/assessed records. Projection periods can be future. No price comparison is made from draft or nonbinding quote revisions. Sources, independent Decimal derivation and unrounded traces are in scratch/task-builders/train_005/derivation.json; independent Fraction spotchecks in manual_spotchecks.json.

## Solution and scoring basis

Historical settled principal is720000CNY tooling and330000CNY R&D, total1050000. Eligible claims are216000 and99000, total315000. Actual receipts108000 and49500, total157500. Outstanding108000 and49500, total157500, due2026-12-01 and partially received. No unpaid full contract amount is presented as paid. Credit title is customer;200000CNY tooling transfer credit is noncash, reusable asset disposition, not refund or receipt. Cross-currency credit conversion/payment timing is not specified; do not fabricate a final net NPV using that credit.

R4 explicit scope: authorized replacement suppliers other than the superseded incumbent, SUP013/014 only. Both remain in C-040-change's authorized pool. SUP015 optional unscored incumbent_reference_exposure shows its quote economics without adding a financial point or forcing ranking. R5/R6 cover all three authorized quoted candidates; R7 factually reports all three authorization IDs. No permanent incumbent exclusion is inferred.

Forecast total sets2027..2030=24000,28000,40000,39000 with70:30variantmix. SUP014 quotes are per headlamp piece; multiply by2per vehicle set. SUP013 is USD/set and SUP014 EUR/piece with exact dated FX and period multipliers. Common tooling/RD IDs appear on both variants and are charged once. Tooling recovery sets24000,28000,40000,8000 exhaust100000cap; upfront R&D and180000transition at t=0. Year2027 remains undiscounted; years2028..2030 divide annual operating+tooling by1.1,1.21,1.331. End-round each requested output half-up; annual aggregate and total are calculated from full precision rather than sums of individually rounded component displays. Assets, historic payments, received refunds and outstanding claims are excluded from R4 gross figures.

Gross totals / gross NPV CNY: SUP013=45822723.16 /39291548.57; SUP014=38739593.54 /33390996.69. Optional SUP015=37327593.36 /32044323.25. Its actual approved amendment changes prices from2028; graduated120000tier crosses in2030 (28000low+11000high), not in2027. Its lower gross figure does not establish launch readiness.

SUP013 and SUP014 qualified, cover both variants, no required actions. SUP015 conditional, both variants, complete_photometry_validation required. Customer tooling transfer2weeks precedes requalification6/9/18weeks. Readiness dates2026-12-07,2026-12-28,2027-03-01. Launch and qualificationdeadline2027-01-01; gaps0,0,59days. All12supplier-year capacity comparisons are adequate, so all shortfalls are zero. This is a positive control with real comparison, not an invented shortfall. SUP014 is the qualified launch-ready nonincumbent with lower gross NPV than SUP013; recommendation is unscored.

C-040-nomination is superseded due planned_engineering_redesign. C-040-change covers this program, has not awarded the nomination, allows all three IDs, requires Finance and Engineering approval before external award. No record supports future_program_exclusion_supported=true.

Seven whole points weights2/2/2/3/2/2/1 (sum14), dependency table in scratch rubric_validation.md: R1 paid principal; R2 refund reconciliation; R3 asset/title; R4 gross prospective finance; R5 technical qualification/actions; R6 production and calendar; R7 authorized scope. Values are not scored twice across these outcomes. Recommendation/rank, evidence prose, filenames and formatting do not earn points. Evaluator checks section business fields against private derived reference, unordered rows keyed by supplier/category/fiscalperiod, extra unscored evidence allowed. Integer monetary fields tolerate one minor unit inclusive; years/sets/weeks/days exact; booleans accept only JSONtrue/false. Missing/wrong business section loses only its point; parse/control errors exit2, valid business mistakes exit0.

## Transfer, distinct variation and construction record

train_003/005 jointly anchor settled-paid versus full contract commitment, 30%ofpaid versus deposit, refund claim versus actual receipt/outstanding, reusable customer tooling versus supplier-owned stranded asset, and gross future exposure separate from historical claims/assets. Shared convention is reusable; train005 actual partial receipt, customer title, superseded redesign lifecycle, all-three authorization and all-adequate capacity differ from failed/cancelled lifecycle exploration in train003. Other train001/002/004 reinforce approved-source precedence, quote effectiveperiod/UOM, shared program charges, graduated tiers, cap and first-year-undiscounted NPV. Future held-out replacements require fresh investigation of title, actual credit receipts, contractual authorization, candidate qualifications and launch durations, not memorizing this winner.

Task-specific exploration is selecting valid records from broad shared portfolio and reconciling PRG040's customer title/partial receipt/supersession. Financial semantics and discovery habits recur across the train group. No skill was handwritten and no new model request made.

Evidence: initial19native evaluator cases allpassed, including gold1 and seven one-point business negatives, reordering,1cent/2centboundary, exactyears/quantities, strictbooleans,duplicatekeys,missingsection and parse/control failures. Parent pre-calibration scope clarification changed prompt/schema/gold together to explicit nonincumbent R4; affected six native financial/schema cases passed. Preserve validation_results_initial_all_three.json and scope_refinement_results.json. Real Bash entrypoint gold/negative logs are recorded separately. Native Python has no jsonschema package: declared required/type/enum/array subset is validated locally; full Draft2020-12 schema and gold validation subsequently passed using the already installed Linux project venv; no dependency was installed. No Actor results, difficulty target pass, training improvement or formal calibration claimed. Parent owns integration, review, Git commit and T67 state.

## 中文审阅说明

本题是独立构造的train_005，来源E005及E004，不复用V3答案或评分器。原始E005要求Word邮件和报告，本题已明确改编为JSON业务决策记录；不是原交付格式无变化。数据是合成企业组合，最终种子hash已记录，Actor必须通过真实业务接口发现事实并在OpenSandbox计算。

PRG040实际是工程改型导致合同被替代，不是臆造四次碰撞失败。工装客户所有、非现金转移抵扣20万元；已付工装72万和开发33万，退款请求为实际已付105万元的30%=31.5万，到账15.75万，未到账15.75万。押金比例和退款比例各有独立语义，不能再乘一次30%，不能把未收请求当成现金到账。

未来费用评分只覆盖授权范围内、原供应商以外的SUP013/SUP014；原供应商SUP015费用作为可选不计分参考。技术/产能/就绪仍检查三家实际授权候选。评分口径为gross_before_asset_credit_and_historical_claims，历史已付、退款和资产抵扣不进入未来毛额。没有合同说明跨币种抵扣时间，不凭空算净NPV。三家年产能都足够是真实正例；SUP015技术有条件且晚59天，不为强造短缺修改数据。

正式输入只有业务目标、程序号、截止日、接口占位符和交付格式，没有SOP、全事实、私有答案或评分权重。私有笔记逐点列证据，评分七项独立权重2/2/2/3/2/2/1，每项全得或全失；推荐和排名不另计分。原供应商仍在程序授权名单内，不能推断所有未来项目永久禁止。

已完成控制面独立Decimal推导、Fraction手工数值复核和逐项负例。原始19例与口径澄清后的受影响核验分别保留，金额1分容差，年份数量无容差，布尔严格。构造验证不等于真实Actor、正式校准或学习收益。没有模型调用、环境修改、其他题目修改、Git暂存或T67完成声明；最终集成和提交由主Agent处理。
