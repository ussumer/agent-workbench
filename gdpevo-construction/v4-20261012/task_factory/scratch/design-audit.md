# T67 v4 preconstruction design audit

Date: 2026-10-12. Scope: independent design-quality audit of `task_group_design.md`, `env_blueprint.md`, and `adaptation.md` against the official taskfactory guides `task_design.md`, `environment_and_data.md`, `notes_and_evaluation.md`, and `workflow.md`. Original archived E004/E005 prompts and the `word/document.xml` content of their DOCX attachments were inspected. No private contact information is reproduced. No model calls, generated task files, environment changes, answers, evaluator implementations, calibration, Stage 3 votes, or acceptance are part of this audit.

**Finding: useful shared-environment direction, but revise the design before dispatching task builders.** The two recurring workflow families, five train/five test split, business-domain APIs, versioned raw records, source separation, independent ownership, and avoidance of winner/NPV endpoints are appropriate. The current design remains too general to establish aligned financial semantics, train transfer, and independent business scoring. This is not a rejection of the authorized DS/course-stack adaptation.

## 1. Resolve financial source ambiguity in the shared data contract

The source difficulty comes from financial relationships, not just three supplier records. E004 requires four years, two variants, a 70:30 mix, year-specific discounting, shared recovery of tooling over the first 100,000 sets, and entirely upfront Year-1 R&D split equally across variants. Its DOCX contains one supplier with strict `<100000` and `>100000` price labels, leaving equality and the quantity basis unstated. It does not establish whether quoted part prices are per headlamp or per set. The other suppliers have untiered quotations. These distinctions cannot be guessed differently by ten builders.

Necessary blueprint additions:

- Separate vehicle counts, individual headlamp counts, headlamp sets, quote UOM, price-tier UOM, and tooling-recovery UOM. Require each commercial source to state the unit, conversion factor, and effective date. Preserve `one set = two headlamps` where applicable.
- Define whether tier qualification uses annual quantity, cumulative quantity, variant quantity, or combined program quantity; whether a threshold is graduated marginal pricing or an all-units rate; exact closed/open interval boundaries; and whether a changed quote resets any cumulative counter. Part-year crossing is meaningful only after these choices are explicit. Cover equality and crossing in multiple train cases.
- Distinguish tooling allocation/recovery on supplied sets from actual tooling cash payments and economic expenditure. Otherwise the same tooling can be paid upfront and added again as amortization. Preserve one common cap across variants, rather than separate 100,000-set caps. Define allocation when a year or variant straddles the cap.
- State the NPV valuation date, period timing, discount-rate source, exponents, treatment of upfront Year-1 payments, and rounding. Do not infer a universal enterprise formula from E004's terse wording. Preserve the equal R&D allocation even though variant volumes differ.
- Give forecasts source dates, approval/effective states, supersession links, and comparable time buckets. A newer unapproved record must not automatically become the answer.

E005 is a different financial frame: failed nomination after four crash-test failures, two months of launch delay, a request to terminate current and future nominations, recovery of the 30% tooling/development amount already paid upfront, and comparison of replacement tooling, unit costs, lead times, and qualitative currency risks over the provided volumes. The phrase does not unambiguously distinguish a 30% deposit recoverable in full from a 30% refund applied to an already-paid amount. **The current short phrase `30% upfront paid cost recovery` must be expanded.** Main now declares the generated default explicitly: refund claim is 30% of actually-paid eligible tooling and R&D. This is a documented synthetic enterprise convention, not a claim that E005 unambiguously defines it. A 30% deposit and a 30% refund of that deposit are separate percentages with separate bases. Paid-invoice principal, deposit percentage, refund percentage, legal recoverability, requested amount, and actually received credit are separate facts. A task must never apply 30% twice merely because a paid invoice is already a 30% deposit.

Replacement tasks need separately evidenced historic paid exposure, requested/recoverable refund, sunk or retained costs, reusable owned tooling, new costs, and incremental versus full-program comparison. E005 does not supply an NPV instruction or quantitative exchange-rate formula; adding either is an explicit enterprise adaptation, not an original source fact. Its quotes are INR while FX risk is qualitative. Supply actual currency clauses, dated rates/scenarios, and contract terms before scoring numeric FX consequences. Keep refund claims separate from booked cash recovery. Supply dated failure/escalation and qualification evidence before inferring contractual termination or launch recovery.

## 2. Replace task-role rows with concrete task briefs and a scoring-point transfer matrix

The existing table assigns task/family/program/focus, but omits task-specific output shape, 6-10 scoring goals, weights, discovery burden, and point-level train anchors. The official draft requires these before builders start. `test_005` is especially uncertain: a nomination refresh using a replacement anchor may be valid, but the exact repeated revision/commitment operation has not been identified.

For each of the ten tasks, add a builder brief with the business objective, as-of snapshot, principal source relationships/conflicts, required independent result dimensions, answer-template shape (units/precision/enums/set ordering), and the expected 6-10 business scoring points with raw weights 1/2/3. This is design, not a precomputed answer. For every transfer-dependent test point, add its point ID, weight, two relevant train anchors where the convention recurs, what precisely transfers, and what task-specific facts change. Mark exploration-only points separately.

Repeated family names are insufficient anchors. Establish repeated experience for unit normalization, source precedence, tier/cap boundaries, invoice-versus-contract recovery, and effective revision/commitment handling. A test-specific clause may require exploration, but a high-weight hidden enterprise convention cannot appear first in test. Synthetic conventions must be supported by ordinary business evidence and solved train cases; they must not be arbitrary inaccessible rules invented by evaluator authors. Do not turn prompt/payloads into a policy recipe or answer-like source-fact summary to compensate for weak anchors.

## 3. Prevent toy compression and answer-shaped data

A 140-program database is useful workplace context, but record count and pagination do not prove intrinsic difficulty. Filtering a program down to one clean forecast, three current quotes, and preclassified supplier statuses could collapse the entire task to arithmetic plus a winner lookup. Source E004 itself has only three bidders; comparable depth should preserve its multi-year/two-variant financial analysis rather than add unrelated systems for their own sake.

Each brief should specify material effective-state reconstruction, raw source/document reconciliation, calculations across periods and variants, independent feasibility or recovery evidence, and final business output. Include plausible alternate/stale documents with provenance, not merely irrelevant distractors. Version disagreements must be resolvable from timestamps, authority, effective dates, and linked amendments; no unrecoverable missing evidence. Ensure base attempts cannot receive most score by copying one small public record.

Shared programs may carry genuine business facts such as supplier state and contract terms, but do not store `eligible_for_this_task`, a precomputed recommended replacement, cost rank, expected launch disposition, or a resolved financial answer in those records. A failed incumbent alone must not force a particular replacement; derive alternatives' cost and schedule tradeoffs from independent terms. Generate ordinary records before inspecting their suitability for a task, and record any target reassignment. Do not secretly tune candidate capacities/prices/qualifications to guarantee a planned answer. No task-specific data bundles or fixed task labels should enter public APIs.

## 4. Make rubric independence an explicit builder contract

The global instruction to avoid NPV/ranking/recommendation duplicates is good but insufficient. Require a per-task rubric dependency table: each scoring point -> business conclusion -> evidence -> output fields -> independent error it detects. Merge points that restate one root fact or decision. Numerical exposure plus a ranking mechanically derived from that same exposure plus the cheapest-supplier recommendation cannot earn three points for one result.

At least four business outcomes must actually differ. Depending on the task, defensible dimensions include effective demand reconstruction, financial exposure, technically admissible candidate set, launch feasibility, independently evidenced refund/retained liability, and contract/change disposition. Do not score financial totals and all their components again as independent goals; do not split incidental fields to manufacture 6-10 points. If there are insufficient independent outcomes, improve the business request before writing the evaluator. Accept financially/operationally legitimate ties and alternatives using declared tolerances. Use whole-point all-or-zero checks, preserve unaffected points on ordinary business mistakes, and keep formatting prerequisites from dominating score. Standard-answer self-scoring alone will not establish evaluator independence.

## Concrete independent outcome menus for the two families

These are design options, not answers or an instruction to score every option. Choose 6-8 only where the business request and generated evidence make each conclusion substantive; do not pad a task that supports fewer independent conclusions.

| Nomination outcome | Suggested structured output dimension | Independence condition |
| --- | --- | --- |
| Effective approved demand and variant mix | `demand_by_period_variant` | Reconstruct the business demand, not simply copy source IDs. |
| Binding commercial coverage | `commercial_admissibility_by_supplier` | Assess coverage/validity across variants and periods, rather than re-score cost or engineering. |
| Engineering/quality admissible candidates | `technical_eligibility_by_supplier` | Derive from actual ET/QT evidence and required clearance. |
| Allocatable supply capacity | `capacity_shortfall_by_supplier_period` | Different conclusion from technical competence and delivery date. |
| Earliest qualified supply and launch gap | `ready_date_by_supplier`, `launch_gap_days` | Use actual sequencing/timing; do not split a single leadtime lookup into several points. |
| Comparative baseline discounted economic exposure | `financial_exposure_by_supplier` | One financial result goal; do not score its totals, rank and recommendation again. |
| Capital/liquidity requirement, if requested | `cash_requirement_by_date` | Different treasury decision from discounted economic cost, using actual payment schedules. |
| FX downside, if evidenced and requested | `scenario_exposure_delta_by_supplier` | Independent defined scenario based on currency clauses; not an arbitrary qualitative risk label. |

| Replacement outcome | Suggested structured output dimension | Independence condition |
| --- | --- | --- |
| Settled eligible historic liability | `settled_eligible_amount_by_category` | Reconcile invoices/payments; do not assume quote total equals paid principal. |
| Contractual refund claim and receipt state | `refund_claim_amount`, `received_amount`, `claim_disposition` | Apply evidenced legal basis; distinguish a claim from recovered cash. |
| Tooling ownership/reuse/stranding | `tooling_disposition`, `usable_credit` | Title, usability and credit conditions support an asset decision independent of refund. |
| Incremental forward replacement cost | `incremental_exposure_by_supplier` | Exclude historic double counts; one financial goal rather than total/rank/winner duplicates. |
| Alternate supplier technical admissibility | `technical_eligibility_by_supplier` | Derive actual ET/QT approval and conditions. |
| Capacity adequacy and launch recovery | `capacity_shortfall`, `ready_date`, `remaining_delay` | Split capacity and date goals only if independently requested and independently falsifiable. |
| Termination/change scope and clearance | `termination_scope`, `award_clearance_disposition` | Contract/legal authority is separate from the financial preference for a replacement. |
| Optional treasury/FX risk consequence | `cash_requirement_by_date` or `scenario_exposure_delta` | Use only with independent evidence and a real requested business decision. |

Deliver the final recommendation, but do not reward it again if it merely restates already scored cost/eligibility conclusions. If recommendation represents an additional strategic decision, its constraint and independently falsifiable outcome must be defined explicitly.

## Focused generator snapshot: actionable leakage and uniformity findings

After parent reported environment availability, `env/generate_seed.py` was read for the policy/source-contract risk only; no environment file was edited. This is not an environment acceptance review. The file was changing during inspection: an initial 1.00/0.70 refund fraction was replaced with the declared 0.30-of-paid convention in the latest read. Treat that mismatch as corrected in source, not as an unresolved failure or proof generated data was regenerated.

- Line 25 publishes a single `POL` record containing an amortization formula, tier/reset rule, proportional year allocation, equal R&D allocation, historic sunk-cost treatment and discount exponents. It is close to a complete transferable SOP. Retain ordinary primitive business terms (rates, cap, units, effective dates, payment clauses), but remove calculation recipes and answer-path hints from solver-visible records. Keep hidden source-adaptation audit language out of public `source_convention`; that belongs in notes/manifests. The line-46 quote field `upfront_cost_scope` includes the direct instruction `count once`; represent a shared charge ID/scope in the commercial source and let reconciliation establish nonduplication.
- The line-24 program record lists only `r2` forecast IDs, and lines 34/46 make all revision-2 forecasts/quotes approved and revision-1 superseded. That relationship does not leak numerical answers, but weakens any claimed transfer-dependent source-selection outcome. Add plausible newer pending amendments, mixed effective dates or source-specific supersession where the task brief genuinely needs reconciliation. Do not add arbitrary ambiguity.
- Every program receives an incumbent cancelled/superseded contract and a replacement authorization limited to the first two suppliers (lines 52-53). This can compress different nomination/replacement briefs into one repeated change workflow and preempt candidate judgment. Use coherent business lifecycle variation; ensure nomination-only cases do not depend on irrelevant replacement exclusions, and distinguish contractual authority from technical eligibility.
- Every supplier receives the same 100,000 cumulative tier topology (line 46), unlike E004's untiered and tiered bidder mix. Vary fixed versus tiered commercial offers and genuine boundary cases while retaining shared interpretable terms. Data volume alone does not fix this template risk.
- `annual_discount_exponents=[1,2,3,4]` discounts Year-1 operating costs; the source asks discounting for Years 2-4. The generated t=0/year-end timing is an explicit semantic change. Record it as synthetic enterprise valuation practice or revise to the intended source-aligned frame, and align all builders. Tooling amortization versus upfront payments remains a necessary reconciliation contract, not something a scorer may silently resolve.

## 5. Complete the shared environment contract before integration

The blueprint lists useful business APIs but leaves several required semantics to implementation. Add ordinary data/access contracts for dated FX terms/rates if quantitative FX is used, engineering and quality evidence with dates and revision links, contract amendments/recovery/ownership clauses, payment allocations/credit status, and launch milestones/requalification duration. They may live in the listed domain endpoints/documents; new infrastructure is unnecessary. Define IDs/link fields, effective-state rules, pagination/filter scope, reachable raw-document content, and error behavior. Explicitly separate orchestrator endpoints from the agent allowlist in `env/endpoints.txt` and `environment_access.md`.

Use `<TASK_ENV_BASE_URL>` in all eventual solver prompts/payloads. Keep the port/network configuration in trusted orchestration only. Read-only sourcing does not automatically mean isolated runtime: owner/run scoping, fresh application/computation state, no source/control mounts, allowed network routes, and separate judge-enabled reflect versus judge-disabled base/fewshot/test instances must be specified for the adapted runtime. `/api/judge` is a train-only control surface available only to reflect skill generation, not to ordinary fewshot or test agents. Its output is only normalized score/correct/train-only notice, never evaluator details or answers. No implementation claim is made here.

## Authorized adaptation: exact semantic differences to retain

- Upstream SQLite/network query service becomes Java ERP persistent H2 plus domain APIs/MCP; relational raw-data access must remain adequate. This is authorized, not a reason to reject the design.
- Dockerized Codex GPT-5.5 xhigh processes become fresh configured DS/DeepAgents/LangGraph sessions with real OpenSandbox and Mongo state. Trace format, model/tool behavior, and empirical score distributions differ. Upstream calibration bands therefore remain provisional DS construction targets. Same version/split/config/runtime/scorer and matched repeats are required for any comparison.
- Original workbook and Word/email/report deliverables become structured JSON business outputs. This removes spreadsheet formulas/layout verification and professional-document workflow from the source difficulty. Record it explicitly and preserve the financial reconciliation, assumption handling, commercial-risk and actionable disposition content; do not imply unchanged artifact-work difficulty.
- Capacity, revisions, owned tooling reuse, requalification and numerical FX mechanisms extend the originals. Label them synthetic enterprise adaptations, provide evidence, keep recurring anchors, and avoid test-only hidden SOPs.

## Design exit condition

Before task-builder dispatch, update the design/blueprint with the common financial contract, ten concrete briefs, point-level transfer matrix, and independent-outcome rubric plan; reconcile builder questions centrally. Validate source/UOM/tier/refund and dependency semantics in the generated shared data before builders derive answers. Subsequent construction, evaluator negative checks, real DS calibration and six independent Stage 3 reviews remain outstanding. This report is one preconstruction audit and must not be counted as a Stage 3 acceptance vote or business-learning evidence.

Protocol note: application-repo `python3` invocations exited 1 without output; Windows `python` reported a valid 55-task plan, no eligible pending task, and no T67 packet. Parent confirms the external training plan protocol was completed centrally (70 tasks, 67 done, T67 packet). No protocol status or task state is changed by this subagent.


## First scoped fix review — 2026-10-12

Scope: reread `financial-contract.md`, `task-builder-contract.md`, all ten `task-briefs/*.md`, `transfer-matrix.md`, and the updated generator only for prior findings and critical contradictions. This is a preconstruction design re-review, not a Stage 3 vote. No formal task files, environment files, model calls, answers, or calibration were produced.

**Dispatch recommendation: two concrete contract fixes remain necessary; other prior findings are substantially addressed.** The parent-selected six nomination/seven replacement points are appropriate and override the generic eight-point menu. Do not require padding or rerun unaffected work.

### Resolved or sufficiently addressed at design stage

- Ten individually owned briefs now state output objects, weights, normalized units/precision and business conclusions; point-level recurring train anchors are supplied in the transfer matrix. Actual implemented tasks must still establish those anchors, rather than merely repeat the matrix.
- The financial contract separates units, tiers, source effectivity, common tooling charges, upfront R&D, paid principal versus refund/received credits, and independent cost/risk evidence. The chosen synthetic refund base is explicit.
- Generator policy terms have been reduced to business primitives; explicit calculation recipes, `count once` and public construction-adaptation commentary were removed. Charge IDs now support common-charge reconciliation. Mixed approval states, future amendments, flat/tiered offers, varying tooling title, and nomination versus replacement lifecycles address several earlier uniformity findings.
- Source-aligned Year-1-undiscounted treatment is now stated; source provenance and structured-deliverable adaptation remain required. Replacement history is limited to a subset of shared programs, not every task target.
- Recommendation and ranking are not independently rewarded. Boundary checks, targeted point mutations, real evidence and independent gold calculations are required. These are design requirements, not evidence those checks have passed.

### Remaining blocker 1: N2 and N6 still double-score financial facts

Every nomination brief defines N2 as effective commercial coverage **and normalized variant/year unit prices**. N6 scores the complete discounted cashflow, including tier/currency components. The warning to redesign later does not remove the overlap from the dispatched specification.

Minimum correction: N2 should score only binding commercial applicability/coverage across supplier, variant and fiscal interval (validity, approval, amendment coverage, contractual availability and any uncovered segments). Prices and UOM-normalized rate calculations may be reported, but score them only inside N6. N2 must be independently falsifiable by an applicability/coverage mistake when the arithmetic output is otherwise right; N6 must be independently falsifiable by arithmetic while N2 applicability remains correct. Record this division in the brief and transfer matrix, not only a generic instruction to builders.

### Remaining blocker 2: resolve as-of versus future-period applicability

The generator gives an as-of date of 2026-10-12 while forecasts refer to fiscal years 2027-2030. For `n % 4 == 2`, approved revision-2 annual quantities become effective on July 1 of each projected year; revision 1 remains approved over the full year. The records do not say whether revision 2 is a replacement full-year forecast, a quantity for the remaining half-year, or a prospective rate requiring interval allocation. Multiplying an annual quantity by half a year, taking revision 2 for the whole year, or adding revision 1 and revision 2 would produce incompatible golds.

Minimum correction: distinguish the knowledge cutoff from the applicability interval. Approved/issued evidence known by as-of may govern future production periods; future fiscal dates are not by themselves grounds to discard the record. A document approved after as-of is unavailable to that snapshot. State whether forecast `quantity` is an entire fiscal-period plan or an interval-specific volume. If the latter, supply interval quantities or an evidenced allocation convention. Define how an approved amendment supersedes the old coverage and how r1/r2 quotes span the future horizon. The promised `env/schema.md` was absent at this read; it must provide this common interpretation before builders derive answers. Construction-only prose cannot silently make an otherwise ambiguous business fact determinate.

The same contract should clarify tooling/R&D amendment charge identity: the generator uses different `TOOL/RD-...-r1/r2` IDs while repeating the same program tooling/R&D amount. An amended price schedule must explicitly replace the existing capital obligation, rather than accidentally create a second tooling payment. Supply a stable underlying charge ID or a replacement/reference relationship. Current words about common program exposure are directionally correct but do not identify the relation between revisions.

### Data-to-brief issues to correct or acknowledge before gold construction

- The stated train_005 partial-refund focus is not present in its target. Current refunds are partial only for program numbers divisible by 5; none of replacement targets 047/069/096/118 meets that condition. Correct the focus or select a naturally suitable program, with a recorded reason and family/anchor consistency. Do not invent paid credits in gold.
- All generated capacity shortfalls are zero. For fiscal index k=0..3, maximum demand is `basevol + 9000*k`; minimum supplier capacity is `basevol + 17000 + 6000*k`, so there is always at least 8,000 sets of surplus. Zero is a legitimate business result and must not be changed merely to force a different answer. But N4 cannot be claimed to exercise recurring binding capacity constraints, and it is low difficulty relative to the plan. Either use legitimate more varied capacity facts/select suitable naturally generated targets, or explicitly acknowledge this limitation and verify that other points supply the intended majority of substantive difficulty. Dataset size does not establish this.
- The generic task-builder contract still says eight points and requires an R8 sensitivity. Individual briefs explicitly override it with six/seven, so this is not a blocker; replace the stale default centrally to prevent unnecessary requests or padding.
- All test transfer rows currently use the same broad “what changes” text and mark almost every point as transfer. During integration label actual exploration-only points honestly and confirm task-specific source conflicts, conditions and independent outcomes. Do not claim observed transfer or saturation avoidance before real DS evidence.

After the two common-contract blockers and the train_005 evidence mismatch are resolved, task builders may proceed against a frozen documented environment. Builder discovery must retain the ability to report missing evidence or unsupported outcomes. This review does not certify environment readiness, evaluator validity, real model performance, or learning improvement. Parent owns dispatch and state registration.
