# T67 isolated control integration checkpoint, 2026-10-13

Status: implemented and actual zero-model doctor ready; full DS calibration and Stage3 review still incomplete.

The unchanged official OpenSandbox server0.2.3 is packaged from55 exact installed dependencies and4004 file hashes, with immutable Python base digest and source snapshot5b561c21ce2e430cb75c25f6fd7c63c519c7de12530ae7ff1732b01a680d46bc. Actual locked control image is sha256:b4e07354084f73d3b6f8dbe40e21a9058e041d6b387382c65aab4af9e09b31e9. There is no runtime mount of host site-packages or model credentials. Only trusted control receives Docker socket and read-only control config; Actor receives neither.

T67 isolated_control delegates to gdpevo_v4_control: API bound to management IP and published only on host127.0.0.1; private execution NIC reaches execd. Management must become ready before attaching the existing Java business bridge, with business gw-priority-1 and immediate managementhealth recheck. Actor only attaches to internal gateway-isolated execution network, with no mounts. Real Java HTTP is transported from the trusted control business NIC with operator token on stdin, never in command arguments or evidence. Domain allowlist remains in the real MCP server.

Real doctor13 passed: actual Java image/seed/app hashes matched frozen business source; Mongo27017 ping succeeded; unskippedSDK readiness and16 reverse socket probes passed; real MCP authenticatedJava programs read; computed42 and committeddoctor_probe at Mongo version1; second worker loaded that state and printed42, version2; explicitMCP stdio exit and sandbox deletion verified; trustedcontrol andboth ownednetworksremoved. This proves zero-model connectivity and protocol operation, not financial contribution or learning gain.

Skill generation now uses a fresh real MCP/Mongo/OpenSandbox scope and the same three Actor-visible tools instead of tools=[]; train-only examples remain frozen. Explicit finish_session is operator-only, hidden fromActor. Each skill/Actor session is immediately checked before more paid work; bad mandatory mechanism/cleanup stops while retaining rows/calls. Completion-order-independent matching uses exact normalized toolarguments. Source/helper/operator images andactualJava identity are frozen; write_todos/filesystem/subagent capabilities are excluded frommodel toolview.

Verification: integration-final.xml50passed0failed0errors0skipped, directly affected runtime/control suite. Red/green retained for stopped-container cleanup, actual source/MCP boundaries, immediate paidguard and configured probeports. Independent scopedcode review found no remainingcritical/importantissue after reviewing actualdoctor13 evidence; this review is not aStage3 vote. No modelcall/fee occurred through thischeckpoint.

Preserved failed preflights:8 used oldMongo27028; actualcourseMongo27017 was thenverified.9/10 and12 failed managementingress when businessNIC attached beforestartup; priority-only repair didnotfix it.11 was erroneouslylaunched after a PowerShell patchapplicationfailure, preserved andcorrected.13 tested verified actualordering patch andpassed. Do not rewrite these as successes; DockerDesktop's exact port-forward implementation is not conclusively attributed. The successfulordering is empiricallyverified. Allownedtemporaryresources cleaned.

Entry points: scripts/planning/gdpevo_v4_control.py; scripts/planning/gdpevo_v4_taskset.py; infra/sandbox/t67-control/{package.py,Dockerfile,requirements.lock.json}; tests/acceptance/test_t67_control.py andtest_t67_runtime.py. The package script copies inspectedinstalled library bytes offline and rejects lockdrift; no freshpip resolution. Existinguserpckl unchanged.

Next: freeze an independent finalrun directory with the existing executionimage133a byrepositorydigest; run the authorized3DSskills+15base+15matchedfewshot with all failures/calls/costs. Then independent rescore/verify andsixStage3reviews. T67 remainsin_progress; provisionaldifficulty/lift failure must remain failure, no rubric changes just to fit a band. T68 remainsblocked andneverresumed.

Evidence hashes:
- preflight-attempt13-result.json: 91b82459969e40503abfa0908ab307bd770beda836261b5e0dbed646e91bcc94
- integration-final.xml: 8c51393f11272848155a8b0db191fa2ae8bbe53e5f8a37d2a48c9c84cc94a632
