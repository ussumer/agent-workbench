# T67 control integration plan

Goal: preserve the approved stack while integrating the proven official-server dual-NIC deployment. User said continue after the verified topology report. No paid model calls before actual MCP doctor passes.
Spec: docs/runtime/t67-sandbox-network-diagnosis-2026-10-13.md; T67.

- [x] Parent: add scripts/planning/gdpevo_v4_control.py, infra/sandbox/t67-control packaging and test_t67_control.py. Red/green: management-only API bind, loopback publication, Actor private membership, cleanup on partial creation. Bake installed exact official server dependencies into immutable Docker image; no Actor mounts.
- [x] Runtime owner: delegate isolated_control helper; actual MCP doctor (authenticated Java read, OpenSandbox42, Mongo state/status, explicit cleanup); fresh real tool-enabled skill sessions; verifier raw calls/skills/train-only/served source freeze.
- [x] Independent scoped review, then fresh staging and actual zero-model preflight with operator-held token. Commit useful changes and honest HANDOFF/state.
- [ ] Full original DS construction/calibration/review remains required:3skills+15base15fewshot and6Stage3 votes. No done before complete gate; T68 never resumed.

Helper interface: isolated_control(config,directory) yields execution network name. control-runtime.json keys: schema_version,container_id,container_name,management_ip,execution_ip,port,image_id,source_snapshot_sha256,server_version,networks,config_sha256. control-cleanup.json records status/errors/removed_sandboxes/control_removed/networks_removed. business_http forwards real Java HTTP through trusted control optional business NIC; Actor stays private only, no host/source mounts or Docker socket.

Stop after2 evidenced unsuccessful fixes to same blockage or20min without progress. Preserve all failures and fees. Do not repeat unrelated passed checks. Use subagent-driven-development and scoped independent review.
