# T67 sandbox network diagnosis, 2026-10-13

## Finding
The execution service works. The failing boundary is access from the WSL-hosted OpenSandbox control service to the Docker Desktop isolated container network. Moving the same official control service into that network made normal SDK readiness and real command execution succeed. This is a zero-model topology feasibility result; the production T67 adapter is not yet changed and calibration is not complete.

## Layered evidence
| Check | Existing WSL control deployment | Container control deployment |
| --- | --- | --- |
| Execution service inside sandbox /ping | HTTP200 | Normal SDK readiness passed |
| Peer on same private network /ping | HTTP200 | Control service reaches sandbox via its private NIC |
| WSL direct private container address | ReadTimeout | No longer part of execution routing |
| SDK via official server proxy | ReadTimeout | Passed; real python3 command returned42, exit0 |
| Sandbox connection to control/host/external probes | Not assessed in failed SDK path | All16 attempted connections failed |

Attempt6 retained the original private internal network with gateway_mode_ipv4=isolated and empty Actor mounts. Actual SDK URL was captured, including /v1/sandboxes/<id>/proxy/44772/ping. Inside and peer requests returned200; WSL direct and both SDK/manual proxy requests timed out. Source inspection shows official proxy resolves the container private IP, so use_server_proxy does not create host reachability to that private IP. This establishes the deployment boundary failure, without attributing the exact packet-drop layer to a particular firewall or Docker Desktop component.

## Tested repair topology
Keep the official SDK/server, installed versions and code-interpreter image. Run the trusted control service in a container with two NICs: a management bridge and the private execution network. Bind the lifecycle API specifically to the management NIC; publish that port only to host127.0.0.1. Set server.eip to127.0.0.1:18125 so SDK endpoints continue through the loopback official proxy. The control's private NIC reaches execd; it does not listen for lifecycle requests there. The Actor attaches only to the private internal network, with no host mounts or writable volumes.

Attempt7 used installed opensandbox_server0.2.3 dependencies read-only in python3.12-slim. Only the trusted control container received its configuration/dependencies and Docker socket. The Actor received none of these mounts. This reuses the control service's existing Docker-management authority; Docker socket must never go to the Actor. The temporary control container is not yet a locked permanent runtime image and must be packaged/locked before formal T67 calibration.

The actual unskipped SDK readiness check passed; official SDK command execution ran python3 print(6*7), returned42 andexit0. From the same Actor, a zero-model socket probe attempted ports18125,27028,9025,443 on both control NIC addresses,host.docker.internal and1.1.1.1; no connection succeeded. These concrete probes are not an exhaustive isolation proof. No model was called and no procurement record changed. Diagnostic containers/networks were cleaned and checked absent.

## Preserved failed diagnostic preparation
Attempt5 used an overly short3-second management request timeout, causing POST creation to time out after the server had created a sandbox. The follow-up reused that same sandbox instead of duplicating it, but diagnostic python was absent from PATH (python3 is present) and its five-minute TTL expired before SDK reconnection. Both reports remain. The temporary sandbox and network were subsequently cleaned. Attempt6 corrected the diagnostic timeout, command path andTTL without changing service/network behavior; it produced the complete layered evidence. Do not mislabel these diagnostic mistakes as extra evidence that execution services were broken.

## Remaining integration
Update only T67 isolated_control deployment to the tested trusted dual-NIC control topology; retain source/runtime freezes and record actual control image identity. Add focused checks for management-only bind,Actor network/mount boundaries,normal SDK readiness andactual code execution. Strengthen probe_isolation to include current control NIC addresses and actual ports. Complete real MCP doctor and fresh tool-enabled fewshot sessions afterward. Do not rerun unchanged task scorers or treat this diagnostic success as accepted task difficulty, model calibration, training gain or Stage3 review.

## Evidence
All relative paths below are under gdpevo-construction/v4-20261012/task_factory/scratch/runtime/ and retained locally. Diagnostic scripts are temporary, not production implementation.
- network-diagnosis-attempt5-result.json and network-layers-attempt5-result.json: preserved diagnostic preparation failures.
- network-diagnosis-attempt6-result.json: complete original-topology layered comparison.
- network-control-attempt7-result.json and network-control-attempt7/control.log: successful candidate topology,realSDK/command,reverse probes andcleanup.

SHA256:
- network-diagnosis-attempt6-result.json: 35818f7001d37a2fe527941287b4499754f7deb7f22c51894e84668d2625803d
- network-control-attempt7-result.json: 656972c7e1c6ef422561d292116567cb51f3ea3ce9994d5a7541b1ae667612b5
