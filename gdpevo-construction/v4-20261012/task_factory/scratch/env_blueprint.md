# Shared task environment blueprint for group025

Implement only env/; no task answers/prompts/evaluators. Read adaptation.md. Source tasks E004/E005 under seed_scenario/ specify difficulty,not final taskdata. Existing stack required.

## Ownership and files
Root gdpevo-construction/v4-20261012/task_factory/task_group/task_group_025/env. Copy/reuse existing Java ERP source and its locked Maven build into env/erp (within build context);add minimal sourcing domain controllers/repository using existing Spring/JDBC/H2 persistent patterns. Existing ERP catalog/order APIs keep unchanged. Do not replace Java domain facts with Python memory/fixed responses. Additional read-only document service may proxy Java responses but cannot own business truth. Mongo manages app/computation via existing Harness,not as a substitute ERP. No productionDB mutations;dedicated environment names/volumes.

## Public business data
Generate reproducible shared records with seed20261012025 (generator confined to env):>=140programs,>=3candidate suppliers per program,base/premium variants,4fiscal periods,priceversions,demand revisions,capacity/qualification assessments,contracts,actual paid tooling/R&D invoices and cancelled/replaced nominations. Ensure target program IDs listedin design exist, with reachable adequate evidence and intended family relationships. Other records are realistic distractors,not task-only packages. No taskID,label,winner,expectedNPV,rubric in public responses. Programs identify dates,basecurrency,mix/volume references and normalbusiness fields;every commercial document explicit currency,UOM,effective period,approvalstatus. Auth/health/adminreset separate fromsolverentrypoints.

## Proposed interfaces (domain oriented)
GET /api/sourcing/programs?query=&offset=&limit=
GET /api/sourcing/programs/{program_id}
GET /api/sourcing/forecasts?program_id=&offset=&limit=
GET /api/sourcing/quotes?program_id=&supplier_id=&offset=&limit=
GET /api/sourcing/documents/{document_id}
GET /api/sourcing/engineering?program_id=&supplier_id=&offset=&limit=
GET /api/sourcing/contracts?program_id=&offset=&limit=
GET /api/sourcing/payments?program_id=&offset=&limit=
GET /api/sourcing/suppliers?query=&offset=&limit=
GET /health
No target-specific aggregateendpoint or computedresult API. Bodies documented in env/schema.md,not answerpaths. Documents include rawtabular/text records and sourceversions;no complete solve steps. Return deterministic pagination,total counts,and data envelope. Include all records necessary for known targets and filterqueries in actual H2database. Document noaccess to rawDB/source fromActor.

## Runtime
Provide env/Dockerfile buildcontextenv/ only,Java21runtime and locked Mavenwrapper. Internal bind0.0.0.0 port9025,networkaliastask-env,no publishedhostport;as stack adaptation Mongo/OpenSandbox are separately scoped existingservices. State_mode read_only for all sourcing endpoints;write APIs inaccessible tocalibrationroute. Fixedauth service token generatedperrun;never log secret. Judge registration handledlater:train-only gateway /api/judge when TASK_ENV_ENABLE_JUDGE=1;when0absent. Do not implement gold logic inenv;publish integration hook fortrustedcontrolplane. Health and operatorseed/reset available onlytoorchestrator. Retain source anddata SHAmanifest,DBmigration,seed,generatedcounts,and source lineage. No actormountofenv/.

## Validation
Write meaningful tests before implementation:requestedprograms materialized,pagination/filtering,actualDB facts,differentversions/no winner exposure,read-only boundaries. Build andrunJavaenvironment;query froma separateDockercontaineron samebridge. No modelcalls. Declare exact limitations;don't claim fullstackActor passed basedonHTTP. Any SDK/service block twoevidence-basedfixes thenstop/report.
