# Automotive sourcing finance and supplier-change task group 025

Status: construction design; not generated tasks, not calibrated, not accepted.

## Source interpretation and difficulty audit
Stage1 SCN025 E004 (original GDPval24d1e93f...) contains3suppliers,2variants,4year volume,70:30mix,10%discount years2-4,tooling amortization across first100000sets irrespective of variant,upfront R&D split equally by variants,and quotations. E005 (05389f78...) adds failed supplier nomination,30% upfront paid cost recovery,replacement costs/leadtime/FX and launch recovery. Preserve these financial/workflow relationships. Source attachments are unchanged in seed_scenario. Exact original per-headlamp/set language and strict < /> tier equality are ambiguous: generated records must explicitly state UOM and effective tier intervals; never silently attribute resolution to source or hide it from agents. Any added firm convention is labeled synthetic enterprise adaptation and anchored by multiple solved train tasks.

Two recurring families: multi-year sourcing nomination, and replacement/change reconciliation. Not ten unrelated workflows. Five train are full cases, not tutorials or explicit SOP recipes. Every task requires querying and reconciling shared business records, independent financial analysis, supplier feasibility, and structured nomination/change results. No v3 fixtures or scoring reused.

## Task roles (one independent builder per row)
|task|family|target program|focus and transfer|
|train_001|nomination|PRG-021|two variants,source version and UOM,upfront R&D versus recoverable tooling|
|train_002|nomination|PRG-034|tier crossing during year,cumulative tooling cap,forecast reconciliation|
|train_003|replacement|PRG-047|failed nomination,paid invoice recovery,eligibility and leadtime|
|train_004|nomination|PRG-058|different mix and timing,volume revisions,FX and capacity checks|
|train_005|replacement|PRG-040|owned tooling versus unrecovered upfront amounts,launch/requalification|
|test_001|nomination|PRG-084|new entities;source/UOM+volume revision+tiering,anchors train001/002/004|
|test_002|replacement|PRG-096|new entities;refund+capacity+requalification,anchors train003/005/004|
|test_003|nomination|PRG-107|new entities;mix+tiering+part-year crossing,anchors train001/002/004|
|test_004|replacement|PRG-110|new entities;commitments+refund+forecast change,anchors train003/005/002|
|test_005|nomination/change|PRG-129|nomination refresh after source revision,anchors train001/004/005|

Target identifiers select existing business programs; public environment is indexed by business domains, never task IDs. Task builders may change their local target selection if generated facts do not support the intended conditions, but must coordinate changes with the main agent and record why. No forced expected result until actual business data is inspected.

## Shared environment and visibility
Agent prompt gives business request/program ID, as-of date, deliverable/schema, and environment entrypoints. It does not include the wholeprogram,allquotes,gold,rubric,SOP,or prescribed call order. Shared APIs expose programs,versioned demand/forecast batches,quotes/documents,engineering assessments,contracts,paid invoices and supplier status. Data scale120+programs,350+supplier relations,forecasts and multiple quote/engineering/document versions with distractors and pagination. Exact business source facts are reachable; missing information must not make accepted tasks insoluble. No /tasks/id/data or precomputed NPV/winner endpoints.

## Scoring principles
Each builder creates6-10whole-point checks,raw weights1-3,at least4independent business outcomes. Most weight must require material discovery,reconciliation,financial computation or transferred convention. No format/copying points dominate. Avoid separate scores for totalNPV,ranking,recommendation if all are the same underlying decision; instead score semantically independent decisions such as reconstructed volumes,financial exposure with cost breakdown,engineering feasibility,recoverable historic liability,operational change disposition. Define numeric precision,normalize sets,and accept ties. Private answer and checker are produced by task-specific reasoning with independent manual/sensitivity checks,not served to Actor. Gold must be explainable from shared facts and declared adapted policies,not copied from another evaluator.

## Construction ownership and gates
Main owns design/blueprint,source audit,transfer matrix andintegration. Clean-context env-builder owns env only and never tasks/gold/rubrics. Ten clean task-builder contexts own one task each and request changes through main. Build environment first,validate public capabilities from separate container,then dispatch task-builders in batches. Verify gold scores1,negative inputs fail intended rubric points,trainjudge cannot reveal test or answers. Only after this run3independent DSfewshot packages and15base/15fewshot runs. Six independent Stage3reviewers examine actual group+construction/calibration traces;main doesnot simulate votes.

## Calibration and final handoff
Use adaptation.md,actual DSconfig,identical runtime/model/limits and three repeats. Agent views separated fromcontrol materials/network. Retain failed attempts,unknown usage and walltime. Frozen accepted candidate is versioned as task_group025-v4;calibration test is development evidence,not a pristine sealed research result. T67 stays incomplete until build,calibration andreview pass. Published taskgroup alone doesnot imply learning effectiveness or contribution fromdynamic/computation.

## Concrete briefs and transfer gate
Ten task-briefs/*.md define each output/weighted rubric; transfer-matrix.md gives point-level repeated anchors; financial-contract.md resolves generated source semantics. Final targets are provisional until shared facts validate family and complete references. No taskbuilder may silently invent missing feasibility or policy.

## Final constructed candidate (pre-calibration)
Allfiveformaltrainandfiveformalheldouttasksarepresent. Distinctbuildercontexts: train001/002/003/004/005 andtest001/002/003/004/005. Nominatingprograms021/034/058/084/107/129;replacement047/040/096/110. Sourcefinancialsemanticsvalidatedinprivatecomponentproofs. No formalDSskillgeneration orcalibrationscoresyet. Do notcall candidateacceptedonconstructionalone.
