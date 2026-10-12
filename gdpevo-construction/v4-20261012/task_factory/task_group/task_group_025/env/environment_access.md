# Environment runtime access

Build from env/ only: JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64 sh build.sh. The copied SHA-locked Maven3.9.9 wrapper builds Java21/SpringBoot3.5.6; runtime Java21 image is pinned to its digest. Dockerfile consumes the Maven output, so execute build.sh rather than bare docker build on a fresh checkout. Python3 is only a trusted deterministic seed/probe builder, not an ERP or Actor substitute.

Run: supply a fresh ERP_SERVICE_TOKEN in process environment and invoke sh run.sh. Optional SOURCING_NETWORK, SOURCING_CONTAINER and SOURCING_VOLUME isolate deployments. Listen 0.0.0.0:9025; internal bridge alias task-env; no published host port. New named volume is seeded transactionally; restart preserves real H2 files. Different seed bytes reject an existing seed receipt; operator reset means provision a new dedicated volume, not an Actor endpoint. No production ERP/Mongo state is touched.

Actor sees domain MCP tools for endpoints.txt only, using <TASK_ENV_BASE_URL>. Trusted MCP/gateway holds token. Actor has no raw DB, source, seed, control/evaluator mount or reset permission. Orchestrator may access /health and Docker lifecycle. Container mounts only its dedicated data volume; probes run in a separate Docker container. The probe script mount is trusted construction evidence and is never an Actor mount.

Read-only business service does not establish full stack owner/run isolation. Parent integrates Mongo Episode/computation state, real MCP/OpenSandbox/DeepAgents, Vue and DS configuration. /api/judge remains absent here. If trusted parent gateway provides a judge, expose it only to train reflect with TASK_ENV_ENABLE_JUDGE=1 and score/correct/train-only notice; base/fewshot/test routes must have it disabled/absent. No evaluator code or gold enters env.

verify_runtime.py is a one-shot construction driver creating explicit attempt-named container/volume, probing twice across a restart, retaining logs/evidence and the successful container. It is not a background monitor or retry daemon. On a fresh evidence run choose new dedicated names and output filenames; preserve failures. See scratch/env-builder runtime reports. It calls no model.
