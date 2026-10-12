# Independent builder brief: train_001

Family: nomination. Target: PRG-021. Focus: Formal initial sourcing decision; investigate source revisions and unit normalization.

Read task-builder-contract.md and financial-contract.md, then actual finalized env/schema.md and business evidence. Source E004/E005; no V3. As-of2026-10-12,projection fiscal2027-2030. If these windows cannot be resolved from actual documents, report instead of inventing policy.

Required conclusions (point_id, output key, weight):
- N1: `approved_volume`; weight2; Reconstructed four-year approved vehicle-set demand and variant mix.
- N2: `commercial_coverage`; weight2; Effective dated commercial applicability and variant/year coverage only; no price/currency/UOM arithmetic scoring or ID-copy reward.
- N3: `technical_admissibility`; weight2; Evidence-derived engineering/quality admissible supplier set and conditions.
- N4: `capacity_shortfall`; weight2; Supplier-specific year-by-year supply allocation shortfalls.
- N5: `launch_gap`; weight2; Earliest qualification/delivery readiness and launch gaps.
- N6: `financial_exposure`; weight3; Discounted cashflow exposure per candidate, all charge/tier/R&D/currency components together.

Build a JSON answer_template with top-level program_id, as_of and the above independent output objects/tables. Money integer minor units with explicit currency; datesISO; stable supplier IDs; fiscal-period sorted tables; source references permitted but not independent reward. Recommendation and narrative may be unscored outputs. Standard answer requires manual spotchecks and full independent numerical derivation from actual finalseed. UseDecimal/integer precision; define tolerances.

These are six/seven requested outcomes, not eight mandatory: this taskbrief overrides broad default in task-builder-contract to avoid padding. If N2 duplicates N6 source fact instead of independent businesscoverage, redesign or merge and bring another actual independent conclusion before main approval. No formal prompt should reproduce this hidden rubric or finance recipe.

Own only train_tasks/001/ and scratch/task-builders/train_001/. English formal files; bilingualnotes. Evaluate gold and pointmutations,record boundary/sensitivity checks. Never changeenvoranother task. Return standalone rubric.yaml with point_id/goal/weight for final main task_group.yaml integration.
