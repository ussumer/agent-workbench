"""Mechanism smoke for the compute T64 runner: three real episodes, one per injection mode.

This is an explicit mechanism validation, not the full experiment: it proves the runner's
end-to-end wiring (sandbox, durable computation, per-turn selector, usage accounting,
sandbox recycling) before the paid full run, and measures per-episode cost for the estimate.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
EVAL = Path("/mnt/c/dev/rsi-eval")
for folder in (ROOT, ROOT / "src", ROOT / "tests", EVAL):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from fixtures import agent_protocol_service, sandbox_service  # noqa: E402
from live.stack import running_stack  # noqa: E402
from procurement_eval.budget_proxy import gateway  # noqa: E402
from procurement_eval.services import sandbox_control  # noqa: E402
from scripts.planning.gdpevo_calibration import digest, write  # noqa: E402
from scripts.planning.gdpevo_v4_compute_training import (  # noqa: E402
    Context,
    UnlimitedCalls,
    arm_skills,
    compute_episode,
    load_inputs,
    policy,
    write_manifest,
)

DEFAULT_OUTPUT = EVAL / "procurement_eval/runs/planning-session-20261006/attempt-gdpevo-v4-compute-smoke-20261006"
DEFAULT_BANK = EVAL / "procurement_eval/runs/planning-session-20261005/attempt-gdpevo-v4-faithful-20261005k/skills.json"
EPISODES = (("fixed", "quotes-train-01"), ("skills", "packages-train-01"), ("dynamic", "kits-train-01"))


def run(output: Path = DEFAULT_OUTPUT, *, bank_path: Path = DEFAULT_BANK) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(output)
    public, training, control, _answers, train, _test = load_inputs()
    tasks = {task["task_id"]: task for task in train}
    bank = json.loads(bank_path.read_text())
    output.mkdir(parents=True)
    (output / "frozen").mkdir()
    for path in (bank_path, Path(__file__)):
        (output / "frozen" / path.name).write_bytes(path.read_bytes())
    caller = UnlimitedCalls()
    env = caller.env
    os.environ.update({k: v for k, v in env.items() if v is not None})
    protocol_env = ROOT / ".venv-agent-protocol-linux"
    if not protocol_env.exists():
        protocol_env = Path("/mnt/c/dev/rush-harness/.venv-agent-protocol-linux")
    agent_protocol_service.ENV_DIR = protocol_env
    if not os.environ.get("JAVA_HOME"):
        os.environ["JAVA_HOME"] = "/tmp/jdk21/usr/lib/jvm/java-21-openjdk-amd64"
    config = json.loads((EVAL / "config.t46.json").read_text())
    result: dict[str, Any] = {"status": "running", "mechanism_smoke": True, "full_t64": False,
                              "learning_gain_proven": False, "production_assignment_changed": False,
                              "bank": str(bank_path), "episodes": [list(e) for e in EPISODES],
                              "rows": [], "checks": {}}
    smoke_policy = policy() | {"max_model_calls": 60}
    sandbox_service.CONTROL_PORT = config["sandbox_port"]
    try:
        with (output / "model_calls.jsonl").open("x") as log:
            with gateway(caller.model, smoke_policy, log, {"thinking": {"type": "disabled"}}) as (url, budget):
                configured = dataclasses.replace(caller.model, base_url=url, api_key="evaluation-proxy", max_tokens=4096)
                model = configured.create_chat_model()
                with sandbox_control(ROOT, output / "services" / "sandbox-control", config["sandbox_port"]):
                    with running_stack(model_config=configured, run_dir=output / "services" / "stack",
                                       database_name="v4-compute-smoke-" + uuid.uuid4().hex[:10],
                                       preserve_data=True, warm_pool_size=0, planning_only=True) as stack:
                        ctx = Context(public, training, control, stack, model, caller.secrets)
                        for arm, task_id in EPISODES:
                            task = tasks[task_id]
                            dynamic_bank, static = arm_skills(arm, task, bank, bank)
                            row = compute_episode(ctx, output / arm / task_id, task, control, arm,
                                                  phase="smoke", dynamic_bank=dynamic_bank, static=static)
                            result["rows"].append(row)
                            write(output / "rows.json", result["rows"])
                        result["usage"] = budget.summary()
        checks: dict[str, Any] = {}
        rows = result["rows"]
        checks["all_scored"] = all(row["status"] == "scored" for row in rows)
        checks["metrics_int_tokens"] = all(
            type(row["metrics"]["input_tokens"]) is int and type(row["metrics"]["output_tokens"]) is int
            for row in rows)
        checks["sandbox_recycled"] = all(row.get("sandbox_recycled") for row in rows)
        completed_exec = {}
        for row in rows:
            trace_path = output / row["arm"] / row["task_id"] / "compute-trace.json"
            trace = json.loads(trace_path.read_text()) if trace_path.is_file() else {}
            completed_exec[row["arm"]] = sum(1 for e in trace.get("executions", []) if e.get("status") == "completed")
        checks["completed_executions_per_arm"] = completed_exec
        checks["every_arm_computed"] = all(count >= 1 for count in completed_exec.values())
        dynamic_trace_path = output / "dynamic" / "kits-train-01" / "compute-trace.json"
        dynamic_trace = json.loads(dynamic_trace_path.read_text()) if dynamic_trace_path.is_file() else {}
        export_text = json.dumps(dynamic_trace.get("episode_export", {}), ensure_ascii=False)
        checks["dynamic_per_turn_selector_evidence"] = "turn_grounded" in export_text and "selection" in export_text
        dynamic_row = next(row for row in rows if row["arm"] == "dynamic")
        checks["dynamic_selector_overhead_recorded"] = dynamic_row["metrics"].get("episode_model_calls", 0) >= 2
        result["checks"] = checks
        result["status"] = "completed" if all(
            value is True or key in ("completed_executions_per_arm",) for key, value in checks.items()
        ) else "failed"
    except Exception as failure:
        result["status"] = "failed"
        result["error"] = {"type": type(failure).__name__, "message": str(failure)[:1500]}
    write(output / "result.json", result)
    write_manifest(output)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--bank", type=Path, default=DEFAULT_BANK)
    args = parser.parse_args()
    value = run(args.output, bank_path=args.bank)
    print(json.dumps({"status": value["status"], "checks": value.get("checks"),
                      "usage": value.get("usage")}, ensure_ascii=False))
    if value["status"] != "completed":
        raise SystemExit(2)
