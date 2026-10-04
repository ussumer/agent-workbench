"""Read-only T46 verification. Never starts services or generates model tokens."""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import tarfile
from decimal import Decimal
from pathlib import Path
from typing import Any

from scripts.planning.judge import grade

from agent.evolution.episodes import digest
from agent.planning.models import Plan, PlanningProblem


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def verify_manifest(directory: Path) -> dict:
    manifest = json.loads((directory / "evidence-manifest.json").read_text())
    require("result.json" in manifest and "source.tar.gz" in manifest, "incomplete manifest")
    for name, expected in manifest.items():
        path = directory / name
        require(path.resolve().is_relative_to(directory.resolve()), "manifest path escapes attempt")
        require(path.is_file(), f"missing evidence: {name}")
        require(hashlib.sha256(path.read_bytes()).hexdigest() == expected,
                f"evidence hash mismatch: {name}")
    return manifest


def verify_source(directory: Path, revision: dict, *, archive_name: str = "source.tar.gz") -> None:
    with tarfile.open(directory / archive_name) as archive:
        files = {m.name: m for m in archive.getmembers() if m.isfile()}
        require(set(files) == set(revision["files"]), "source archive file set differs")
        for name, expected in revision["files"].items():
            content = archive.extractfile(files[name])
            require(content is not None and hashlib.sha256(content.read()).hexdigest() == expected,
                    f"source archive hash mismatch: {name}")


def verify_episode(result: dict) -> dict:
    export = result["episode_export"]
    episode, bank, events = export["episode"], export["bank"], export["events"]
    require(episode == result["episode"], "episode export differs from final episode")
    require(episode["owner_user_id"] == result["owner"] and
            episode["thread_id"] == result["thread_id"] and
            episode["goal_id"] == result["goal"]["goal_id"], "foreign episode")
    require(bank["_id"] == episode["bank_id"] and
            bank["owner_user_id"] == result["owner"] and
            digest(bank["skills"]) == bank["sha256"] == episode["bank_sha256"], "bank binding mismatch")
    require(not bank["skills"], "T46 baseline must have empty bank")
    require([e["seq"] for e in events] == list(range(1, episode["event_seq"] + 1)),
            "episode event sequence incomplete")
    for event in events:
        require(event["owner_user_id"] == result["owner"] and
                event["episode_id"] == episode["_id"] and
                event["run_id"] in episode["run_ids"], "foreign episode event")
        require(digest(event["payload"]) == event["sha256"], "episode payload hash mismatch")
    started = [e for e in events if e["kind"] == "run_started"]
    require({e["run_id"] for e in started} == set(episode["run_ids"]), "run binding incomplete")
    require({e["payload"]["public_input"]["goal"]["revision"] for e in started} == {1, 2},
            "episode does not span both revisions")
    return {"episode_sha256": digest(export), "bank_sha256": bank["sha256"],
            "events": len(events), "runs": len(started)}


def verify_orders(result: dict) -> dict:
    require(result["erp_before"] == [], "ERP initial state has orders")
    orders = result["orders"]
    actual = {r["order_id"]: r for r in result["erp_after"]}
    require(len(actual) == len(result["erp_after"]) == len(orders) == 2,
            "expected exactly two ERP orders")
    require({o["result"]["data"]["order_id"] for o in orders} == set(actual),
            "unexpected or missing ERP order")
    approvals = [a for e in result["episode_export"]["events"] if e["kind"] == "run_settled"
                 for a in e["payload"]["approvals"] if a["status"] == "executed"]
    for order in orders:
        stored = order["result"]["data"]
        erp = actual[stored["order_id"]]
        require(stored == erp, "ERP order differs from execution response")
        payload = order["approved_payload"]
        require(erp["owner_user_id"] == result["owner"], "foreign ERP owner")
        require(all(erp[k] == payload[k] for k in ("supplier_id", "currency", "note")),
                "ERP order header differs from approval")
        require([{k: line[k] for k in ("part_id", "quantity", "unit_price")}
                 for line in erp["lines"]] == payload["lines"], "ERP lines differ from approval")
        total = Decimal("0")
        for line in erp["lines"]:
            amount = Decimal(line["unit_price"]) * line["quantity"]
            require(amount == Decimal(line["line_amount"]), "ERP line amount differs")
            total += amount
        require(total == Decimal(erp["total_amount"]), "ERP total differs")
        binding = [a for a in approvals if a["interrupt_id"] == order["interrupt_id"]]
        require(bool(binding), "order has no executed approval evidence")
        for approval in binding:
            require(approval["payload"] == payload and
                    approval["payload_sha256"] == digest(payload), "approval payload hash differs")
            require(approval["planning_binding"]["revision"] == order["revision"] and
                    approval["planning_binding"]["goal_id"] == result["goal"]["goal_id"],
                    "approval revision/goal differs")
    require(result["old_orders"] == [orders[0]], "revision did not preserve original order")
    require([o["revision"] for o in orders] == [1, 2], "orders not bound to revisions")
    return {"erp_orders_sha256": digest(result["erp_after"]),
            "approval_orders_sha256": digest(orders),
            "total_cny": str(sum(Decimal(o["total_amount"]) for o in actual.values()))}


def verify_computations(result: dict) -> dict:
    executions = result["kernel_executions"]
    observed = {}
    for event in result["episode_export"]["events"]:
        p = event["payload"]
        if event["kind"] == "tool_observed" and p["name"] == "computation_execute":
            body = json.loads(p["raw_result"]["content"])
            observed[body["data"]["operation_id"]] = body["data"]
    require(len(executions) == len(observed) > 0, "computation/Episode operation set differs")
    version, previous, states = 0, None, []
    for execution in executions:
        op = execution["operation_id"]
        require(execution["owner_user_id"] == result["owner"] and
                execution["thread_id"] == result["thread_id"], "foreign computation")
        require(execution["base_version"] == version, "computation version chain broken")
        require(execution["result"] == observed[op], "computation result differs from Episode")
        require(execution["result"]["processes_stopped"] is True, "computation not stopped")
        if execution["status"] != "completed":
            require(execution["result"]["version"] == version and
                    "candidate_values_json" not in execution, "failed computation published data")
            continue
        require(execution["result"]["version"] == version + 1, "commit did not advance one version")
        state = json.loads(execution["candidate_values_json"])
        if previous is not None:
            require(state["analysis"]["offers"] == previous["analysis"]["offers"],
                    "persisted quotes changed")
            require(state["analysis"]["req_lines"] == previous["analysis"]["req_lines"],
                    "required calculation was not preserved")
        if version == 2:
            require(execution["read_names"] == ["analysis"] and
                    'load_state("analysis")' in execution["code"], "revision did not load committed state")
            require(previous["analysis"]["budget"] == 2500 and state["analysis"]["budget"] == 2200,
                    "budget change missing from committed state")
        previous = state
        states.append({"version": version + 1, "operation_id": op, "state_sha256": digest(state)})
        version += 1
    require(version == 3, "missing committed revision calculation")
    require(len(result["kernel_sessions"]) == 1, "unexpected computation sessions")
    session = result["kernel_sessions"][0]
    require(session["version"] == version and session["active"] is None and
            session["last_commit"] == states[-1]["operation_id"] and
            json.loads(session["values_json"]) == previous, "authoritative final state differs")
    return {"computation_sha256": digest(executions), "states": states,
            "quotes_sha256": digest(previous["analysis"]["offers"])}


def audit(directory: Path) -> dict[str, Any]:
    manifest = verify_manifest(directory)
    result = json.loads((directory / "result.json").read_text())
    verify_source(directory, result["source_revision"])
    if result["terminal_status"] == "prepared":
        require(result["model_calls"] == result["metrics"]["model_calls"] == 0,
                "prepare generated model calls")
        require(result["metrics"]["reserved_upper_cny"] == 0 and
                (directory / "model_calls.jsonl").stat().st_size == 0,
                "prepare has paid/unknown model requests")
        require(result["erp_before"] == result["erp_after"] == [], "prepare wrote ERP orders")
        frozen = result.get("runtime_freeze")
        freeze_report = None
        if frozen is not None:
            require(frozen == json.loads((directory / "runtime-freeze.json").read_text()),
                    "runtime freeze differs from result")
            require(result["started_at"] <= frozen["frozen_at"] <= result["finished_at"],
                    "runtime freeze has invalid time binding")
            require(frozen["model"]["model_id"] == result["model_id"] and
                    result["model_id"] in frozen["available_models"], "model preflight identity differs")
            require(frozen["erp_jar_sha256"] and frozen["dependencies"] and
                    frozen["agent_protocol_dependencies"], "runtime dependency freeze incomplete")
            require(len(frozen["images"]) == 2 and all(i["id"] for i in frozen["images"]),
                    "execution/execd image identity incomplete")
            verify_source(directory, frozen["evaluator_source"], archive_name="evaluator-source.tar.gz")
            freeze_report = {"sha256": digest(frozen), "erp_jar_sha256": frozen["erp_jar_sha256"],
                             "evaluator_sha256": frozen["evaluator_source"]["content_sha256"]}
        return {"status": "prepared-verified", "result_sha256": manifest["result.json"],
                "model_calls": 0, "runtime_freeze": freeze_report}
    require(result["terminal_status"] == "completed", "attempt not completed")
    require(result["metrics"]["model_calls"] == result["model_calls"] > 0 and
            result["metrics"]["usage_complete"] is True and
            result["metrics"]["reserved_upper_cny"] <= 2, "model/cost evidence missing")
    require(all(c["observed_model"] == result["model_id"] for c in result["metrics"]["calls"]),
            "provider response model differs")
    initial = grade(PlanningProblem.model_validate(result["goal"]),
                    Plan.model_validate(result["initial_proposal"]["plan"]))
    final = grade(PlanningProblem.model_validate(result["final_goal"]["problem"]),
                  Plan.model_validate(result["final_goal"]["proposal"]["plan"]))
    require(initial.accepted and final.accepted, "independent judge rejects plan")
    return {"status": "business-evidence-verified", "result_sha256": manifest["result.json"],
            "source_sha256": result["source_revision"]["content_sha256"],
            "episode": verify_episode(result), "orders": verify_orders(result),
            "computations": verify_computations(result),
            "initial_grade": dataclasses.asdict(initial), "final_grade": dataclasses.asdict(final),
            "unproven": ["pre-run ERP JAR and dependency freeze for historical attempt",
                         "independent 2500-budget two-order execution without budget revision",
                         "attempt-wide deadline and cancellation stopping descendants"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("attempt", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.attempt)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"status": report["status"], "report": str(args.output)}))


if __name__ == "__main__":
    main()
