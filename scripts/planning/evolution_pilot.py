"""Run a small real TRACE-style ablation pilot."""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVAL_ROOT = Path("/mnt/c/dev/rsi-eval")
for path in (ROOT / "src", EVAL_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from agent.config import ModelConfig
from agent.env_utils import load_env, redact, secret_values
from agent.evolution.curator import curator_input, training_record, validate_curated
from procurement_eval.budget_proxy import gateway
from scripts.planning.live_baseline import run as run_baseline, reserve_attempt, settle_attempt

LEDGER = EVAL_ROOT / "procurement_eval/runs/planning-session-20261004/budget-ledger.json"
CONFIG = EVAL_ROOT / "config.t46.json"


def train_records() -> list[dict]:
    records = []
    for number in (6, 7):
        path = EVAL_ROOT / "procurement_eval/runs/planning-session-20261004" / f"attempt-{number}" / "result.json"
        result = json.loads(path.read_text(encoding="utf-8"))
        result["case_id"] = f"planning-attempt-{number}"
        records.append(training_record(result))
    return records


def call_curator(records: list[dict], output: Path) -> tuple[Path, dict]:
    env = load_env()
    config = json.loads(CONFIG.read_text())
    original = ModelConfig.from_env({**env, "MODEL_ID": config["model_id"], "MODEL_TEMPERATURE": "0"})
    reservation = reserve_attempt(LEDGER, 2.0, 50.0)
    log = (output / "curator-model-calls.jsonl").open("x", encoding="utf-8")
    spec = {"total_cny": 50.0, "per_attempt_cny": 2.0, "max_model_calls": 1,
            "max_output_tokens": 2048, "max_request_bytes": 65536,
            "timeout_seconds": 120, "input_cny_per_million": 9.0,
            "output_cny_per_million": 27.0, "pricing_kind": "conservative pilot estimate",
            "pricing_source": "configured evaluation policy"}
    try:
        with gateway(original, spec, log, {"thinking": {"type": "disabled"}}) as (url, budget):
            model = dataclasses.replace(original, base_url=url, api_key="evaluation-proxy",
                                        max_tokens=spec["max_output_tokens"])
            response = model.create_chat_model().bind(response_format={"type": "json_object"}).invoke(curator_input(records))
            content = response.content if isinstance(response.content, str) else json.dumps(response.content)
            (output / "curator-output.txt").write_text(redact(content, secret_values(env)), encoding="utf-8")
            if content.strip().startswith("```"):
                content = content.strip().split("\n", 1)[1].rsplit("```", 1)[0]
            skill = validate_curated(content)
            skill_path = output / "curated-skill.json"
            skill_path.write_text(json.dumps(skill.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n")
            return skill_path, budget.summary()
    finally:
        metrics = budget.summary() if "budget" in locals() else {"model_calls": 0, "reserved_upper_cny": 0.0}
        settle_attempt(LEDGER, reservation["reservation_id"], metrics, 50.0)
        (output / "curator-metrics.json").write_text(json.dumps(metrics, indent=2))
        log.close()


def drive_case(stack, client, thread: str, owner: str, attempt: dict) -> None:
    """Production goal/chat and approvals, followed by independent business audit."""
    from scripts.planning.live_baseline import drive_turn, terminal, pending, resume, all_orders
    from scripts.planning.judge import grade, solve
    from agent.planning.models import Plan, PlanningProblem
    from agent.evolution.episodes import EpisodeStore
    frames = drive_turn(
        client, thread,
        "请按本次目标核验来源、交期、必需数量与预算，使用持久计算自主比较方案。"
        "数据完整时直接提交合法最优方案，逐单等待审批；无法满足必需约束时说明原因。")
    attempt["frames"].append(frames)
    for _ in range(4):
        status = terminal(frames)["status"]
        if status != "interrupted":
            break
        action = pending(client, thread)
        candidates = action.get("candidates", [])
        if any(c.get("type") == "supplement" for c in candidates):
            attempt["unexpected_clarification"] = True
            break
        frames = resume(client, thread, action["interrupt_id"])
        attempt["resumes"].append({"interrupt_id": action["interrupt_id"],
                                  "decision": "approve", "frames": frames})
        attempt["frames"].append(frames)
    attempt["terminal_status"] = terminal(frames)["status"]
    row = stack.database.planning_goals.find_one({"owner_user_id": owner, "thread_id": thread})
    attempt["final_goal"] = row
    attempt["erp_after"] = all_orders(stack)
    attempt["database"] = stack.settings.database
    attempt["episode"] = stack.database.planning_episodes.find_one(
        {"owner_user_id": owner, "thread_id": thread})
    attempt["episode_export"] = EpisodeStore(stack.database).export(owner, attempt["episode"]["_id"])
    attempt["kernel_executions"] = list(stack.database.planning_kernel_executions.find(
        {"owner_user_id": owner, "thread_id": thread}, {"_id": 0}))
    problem = PlanningProblem.model_validate(row["problem"])
    proposal = row.get("proposal")
    if proposal is not None:
        assessment = dataclasses.asdict(grade(problem, Plan.model_validate(proposal["plan"])))
    else:
        assessment = {"accepted": False, "feasible": False,
                      "environment_status": solve(problem).status, "violations": []}
    attempt["final_judge"] = assessment
    # Each ERP order must equal a real executed approval and proposed quantities.
    approvals = list(stack.database.pending_actions.find(
        {"owner_user_id": owner, "thread_id": thread}))
    attempt["approvals"] = [{k: (bytes(v).decode() if isinstance(v, (bytes, bytearray)) else v)
                             for k, v in a.items()} for a in approvals]
    expected = {o["result"]["data"]["order_id"]: o for o in row.get("orders", [])}
    actual = {o["order_id"]: o for o in attempt["erp_after"]}
    same_orders = set(expected) == set(actual) and bool(expected)
    if same_orders:
        for identifier, order in expected.items():
            payload = order["approved_payload"]
            observed = actual[identifier]
            same_orders = same_orders and (
                observed["supplier_id"] == payload["supplier_id"]
                and sorted((x["part_id"], x["quantity"], x["unit_price"]) for x in observed["lines"])
                == sorted((x["part_id"], x["quantity"], x["unit_price"]) for x in payload["lines"])
            )
    computations = any(x.get("status") == "completed" for x in attempt["kernel_executions"])
    attempt["business_success"] = bool(
        assessment["accepted"] and same_orders and computations
        and attempt["terminal_status"] == "completed"
        and not attempt.get("unexpected_clarification"))
    attempt["business_checks"] = {"optimal_candidate": assessment["accepted"],
                                  "erp_matches_approved_orders": same_orders,
                                  "real_computation": computations}


def public_case(case_id: str) -> dict:
    from scripts.planning.live_baseline import public_goal
    cases = json.loads((ROOT / "fixtures/planning/decision-cases-v1.json").read_text())
    case = next(c for c in cases["cases"] if c["case_id"] == case_id)
    goal = public_goal()
    goal["budget"] = case["budget"]
    for demand in goal["demands"]:
        if demand["part_id"] == "P001":
            demand["max_lead_days"] = case["p001_deadline"]
        if not demand["required"]:
            demand["allow_partial"] = case["allow_partial"]
    return goal


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-existing", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    if args.verify_existing:
        session = Path(os.environ["PLANNING_EVOLUTION_SESSION"])
        report = verify_report(session)
        (args.output / "verified-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False))
        return 0
    records = train_records()
    (args.output / "training-records.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    curator_path, curator_metrics = call_curator(records, args.output)
    raw_path = args.output / "raw-skill.json"
    raw_path.write_text(json.dumps({"skill_id": "raw-training-episodes",
                                    "description": "Raw operation trace references",
                                    "body": json.dumps(records, ensure_ascii=False)},
                                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    groups = {}
    cases = ("sufficient-budget", "no-partial")
    protocol = {"kind": "public-pilot", "train": [r["case_id"] for r in records],
                "test": list(cases), "repeats": 1,
                "limitations": ["Both train episodes succeeded in business; no paired business failure inference.",
                                "Public development cases, no sealed-test or significance claim."]}
    (args.output / "protocol.json").write_text(json.dumps(protocol, indent=2))
    for group, skill in (("fixed-v1", None), ("retrieval-v1", raw_path), ("curated-v1", curator_path)):
        groups[group] = []
        for case in cases:
            group_dir = args.output / group / case
            print(f"Starting {group}/{case}", flush=True)
            code = run_baseline(group_dir, total_cny=50.0, per_attempt_cny=2.0,
                                experiment_group=group, skill_file=skill,
                                case_id=case, goal_data=public_case(case))
            result = json.loads((group_dir / "result.json").read_text(encoding="utf-8"))
            groups[group].append({"case_id": case, "exit_code": code,
                                 "terminal_status": result.get("terminal_status"),
                                 "model_calls": result.get("model_calls"), "metrics": result.get("metrics"),
                                 "source_revision": result.get("source_revision", {}).get("git_head"),
                                 "bank": result.get("bank"),
                                 "business_success": result.get("business_success", False),
                                 "business_checks": result.get("business_checks", {}),
                                 "final_judge": result.get("final_judge")})
    report = {"schema_version": 1, "protocol_version": "procurement-evolution-v2",
              "sample": "two prior real completed episodes; one real Curator call; two cases/group",
              "curator": curator_metrics, "groups": groups,
              "claims": {"real_actor_runs": True, "real_curator": True,
                         "proves_learning_gain": False, "comparison_is_descriptive": True}}
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"report": str(args.output / "report.json"), "groups": {
        group: [a["business_success"] for a in attempts] for group, attempts in groups.items()
    }}, ensure_ascii=False))
    (args.output / "evidence-manifest.json").write_text(json.dumps({
        p.relative_to(args.output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in args.output.rglob("*") if p.is_file() and p.name != "evidence-manifest.json"
    }, indent=2))
    return 0


def verify_report(session: Path) -> dict:
    manifest = json.loads((session / "evidence-manifest.json").read_text())
    for path, digest in manifest.items():
        if hashlib.sha256((session / path).read_bytes()).hexdigest() != digest:
            raise ValueError("pilot evidence hash mismatch")
    report = json.loads((session / "report.json").read_text())
    if report["curator"]["model_calls"] < 1:
        raise ValueError("real Curator evidence required")
    if set(report["groups"]) != {"fixed-v1", "retrieval-v1", "curated-v1"}:
        raise ValueError("missing ablation group")
    if any(len(attempts) != 2 for attempts in report["groups"].values()):
        raise ValueError("incomplete comparison")
    # Include all original attempts and explicitly linked environment retries.
    # A retry never erases the original from reliability or cost denominators.
    paths = sorted(session.glob("*/*/result.json")) + sorted(session.glob("retry-*/result.json"))
    summary = {}
    from scripts.planning.judge import grade
    from agent.planning.models import Plan, PlanningProblem
    from agent.evolution.episodes import digest
    identity = None
    for path in paths:
        result = json.loads(path.read_text())
        group, case = result["experiment_group"], result["case_id"]
        attempt_manifest = json.loads((path.parent / "evidence-manifest.json").read_text())
        for relative, expected_sha in attempt_manifest.items():
            if hashlib.sha256((path.parent / relative).read_bytes()).hexdigest() != expected_sha:
                raise ValueError("attempt evidence hash mismatch")
        current = (result["model_id"], result["source_revision"]["content_sha256"])
        if identity is not None and current != identity:
            raise ValueError("ablation source or model drift")
        identity = current
        item = {"case_id": case, "attempt": path.parent.name,
                "model_calls": result["model_calls"], "success": False,
                "cost_upper_cny": result["metrics"]["reserved_upper_cny"],
                "errors": result["errors"], "status": result["terminal_status"]}
        if result["model_calls"]:
            exported = result["episode_export"]
            bank = exported["bank"]
            if digest(bank["skills"]) != result["bank"]["sha256"]:
                raise ValueError("bound skill bank mismatch")
            if (group == "fixed-v1") != (len(bank["skills"]) == 0):
                raise ValueError("incorrect experiment bank")
            row = result["final_goal"]
            expected_goal = public_case(case)
            if row["problem"]["budget"] != expected_goal["budget"] or row["problem"]["demands"] != expected_goal["demands"]:
                raise ValueError("case inputs differ")
            candidate = row.get("proposal")
            assessment = dataclasses.asdict(grade(PlanningProblem.model_validate(row["problem"]),
                Plan.model_validate(candidate["plan"]))) if candidate else {"accepted": False}
            item["independent_judge"] = assessment
            item["success"] = bool(result.get("business_success") and assessment["accepted"])
            item["bank_sha256"] = bank["sha256"]
            item["skill_used"] = any(e["kind"] == "turn_grounded" and e["payload"]["selection"]
                                    for e in exported["events"])
        summary.setdefault(group, []).append(item)
    for group in ("fixed-v1", "retrieval-v1", "curated-v1"):
        if {a["case_id"] for a in summary[group] if a["model_calls"]} != {"sufficient-budget", "no-partial"}:
            raise ValueError("missing real Actor case")
    return {"groups": summary, "curator": report["curator"],
            "claims": {"real_pipeline_completed": True, "proves_learning_gain": False},
            "source_identity": identity,
            "limitations": ["Two public cases, one repeat; no sealed or statistical claim.",
                            "Paired business failure learning was not exercised.",
                            "One fixed startup failure is retained; one raw budget failure is retained."]}


if __name__ == "__main__":
    raise SystemExit(main())
