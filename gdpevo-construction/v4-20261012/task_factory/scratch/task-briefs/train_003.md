# Independent builder brief: train_003

Family: replacement. Target: PRG-047. Focus: Formal failure/replacement decision with paid exposure and claim recovery.

Read task-builder-contract.md and financial-contract.md, then actual finalized env/schema.md and business evidence. Source E004/E005; no V3. As-of2026-10-12,projection fiscal2027-2030. If these windows cannot be resolved from actual documents, report instead of inventing policy.

Required conclusions (point_id, output key, weight):
- R1: `settled_principal`; weight2; Eligible settled historic principal by category excluding unpaid records.
- R2: `refund_claim`; weight2; Contractually claimable amount versus credits received and outstanding balance.
- R3: `tooling_disposition`; weight2; Ownership/reuse/transfer credit versus stranded tooling; not a cash refund.
- R4: `incremental_exposure`; weight3; Gross future replacement quotation exposure (operating/tooling/R&D/transition), excluding historic payments, refund claims and separately reported tooling credits.
- R5: `technical_admissibility`; weight2; Alternative supplier technical qualification set/conditions.
- R6: `recovery_feasibility`; weight2; Supply capacity and launch-recovery gaps from actual evidence.
- R7: `change_scope`; weight1; Permitted termination/award scope based on executed documents, not assumed blanket ban.

Build a JSON answer_template with top-level program_id, as_of and the above independent output objects/tables. Money integer minor units with explicit currency; datesISO; stable supplier IDs; fiscal-period sorted tables; source references permitted but not independent reward. Recommendation and narrative may be unscored outputs. Standard answer requires manual spotchecks and full independent numerical derivation from actual finalseed. UseDecimal/integer precision; define tolerances.

These are six/seven requested outcomes, not eight mandatory: this taskbrief overrides broad default in task-builder-contract to avoid padding. If N2 duplicates N6 source fact instead of independent businesscoverage, redesign or merge and bring another actual independent conclusion before main approval. No formal prompt should reproduce this hidden rubric or finance recipe.

Own only train_tasks/003/ and scratch/task-builders/train_003/. English formal files; bilingualnotes. Evaluate gold and pointmutations,record boundary/sensitivity checks. Never changeenvoranother task. Return standalone rubric.yaml with point_id/goal/weight for final main task_group.yaml integration.
