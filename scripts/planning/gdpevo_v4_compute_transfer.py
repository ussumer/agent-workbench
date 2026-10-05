"""Intermediate transfer check: reuse the faithful bank in the real compute Actor.

This is deliberately a small, held-out smoke.  It does not replace the full
T64 compute-arm matrix; it answers the next engineering question without
retraining or feeding test feedback back into the bank.
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

from agent.config import ModelConfig  # noqa: E402
from agent.env_utils import load_env, redact, secret_values  # noqa: E402
from agent.evolution.episodes import TextSkill  # noqa: E402
from fixtures import agent_protocol_service, sandbox_service  # noqa: E402
from live.stack import running_stack  # noqa: E402
from procurement_eval.budget_proxy import gateway  # noqa: E402
from procurement_eval.services import sandbox_control  # noqa: E402
from scripts.planning.gdpevo_calibration import digest, write  # noqa: E402
from scripts.planning.gdpevo_expansion import actor_view  # noqa: E402
from scripts.planning.gdpevo_expansion_judge import grade_task  # noqa: E402
from scripts.planning.gdpevo_tool_diagnostic import (  # noqa: E402
    CONTROL, PUBLIC, TRAINING, _compute_actor, _messages,
)

DEFAULT_OUTPUT = EVAL / "procurement_eval/runs/planning-session-20261006/attempt-gdpevo-v4-compute-transfer-20261006"
DEFAULT_BANK = EVAL / "procurement_eval/runs/planning-session-20261005/attempt-gdpevo-v4-faithful-20261005k/skills.json"
DEFAULT_TASKS = ("packages-test-04", "kits-test-03")


def policy() -> dict[str, Any]:
    return {
        "total_cny": None,
        "per_attempt_cny": None,
        "enforce_cost_limit": False,
        "max_model_calls": 160,
        "max_output_tokens": 4096,
        "max_request_bytes": 262144,
        "timeout_seconds": 240,
        "input_cny_per_million": 9.0,
        "output_cny_per_million": 27.0,
        "pricing_kind": "conservative estimate; not provider invoice",
        "pricing_source": "user revoked monetary ceilings; usage recorded",
    }


def run(output: Path = DEFAULT_OUTPUT, *, bank_path: Path = DEFAULT_BANK,
        task_ids: tuple[str, ...] = DEFAULT_TASKS) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(output)
    env = load_env(path=Path("/mnt/c/dev/rush-harness/.env"))
    os.environ.update({k: v for k, v in env.items() if v is not None})
    agent_protocol_service.ENV_DIR = ROOT / ".venv-agent-protocol-linux"
    if not os.environ.get("JAVA_HOME"):
        os.environ["JAVA_HOME"] = "/tmp/jdk21/usr/lib/jvm/java-21-openjdk-amd64"
    config = json.loads((EVAL / "config.t46.json").read_text())
    original = ModelConfig.from_env({**env, "MODEL_ID": config["model_id"], "MODEL_TEMPERATURE": "0"})
    public, training, control = (json.loads(path.read_text()) for path in (PUBLIC, TRAINING, CONTROL))
    tasks = {task["task_id"]: task for task in public["tasks"]}
    selected = [tasks[task_id] for task_id in task_ids]
    if any(task["split"] != "test" for task in selected):
        raise ValueError("transfer smoke requires held-out test tasks")
    bank_data = json.loads(bank_path.read_text())
    bank = {group: [TextSkill.model_validate(skill) for skill in skills]
            for group, skills in bank_data.items()}
    output.mkdir(parents=True)
    (output / "frozen").mkdir()
    for path in (PUBLIC, TRAINING, CONTROL, bank_path, Path(__file__)):
        (output / "frozen" / path.name).write_bytes(path.read_bytes())
    result: dict[str, Any] = {
        "status": "running", "task_ids": list(task_ids), "arms": ["fixed", "skills"],
        "bank": str(bank_path), "production_assignment_changed": False,
        "full_t64": False, "learning_gain_proven": False, "rows": [],
    }
    budget = None
    try:
        sandbox_service.CONTROL_PORT = config["sandbox_port"]
        with (output / "model_calls.jsonl").open("x") as log:
            with gateway(original, policy(), log, {"thinking": {"type": "disabled"}}) as (url, budget):
                configured = dataclasses.replace(original, base_url=url, api_key="evaluation-proxy", max_tokens=4096)
                model = configured.create_chat_model()
                with sandbox_control(ROOT, output / "services" / "sandbox-control", config["sandbox_port"]):
                    with running_stack(model_config=configured, run_dir=output / "services" / "stack",
                                       database_name="v4-compute-transfer-" + uuid.uuid4().hex[:10],
                                       preserve_data=True, warm_pool_size=0, planning_only=True) as stack:
                        for task in selected:
                            view = actor_view(public, training, task["task_id"], "test")
                            for arm in ("fixed", "skills"):
                                folder = output / arm / task["task_id"]
                                row: dict[str, Any] = {"task_id": task["task_id"], "split": "test", "arm": arm}
                                try:
                                    decision, trace = _compute_actor(
                                        stack, model, view, folder,
                                        _messages(public, training, task, compute=True)[0]["content"],
                                        skills=None if arm == "fixed" else bank[task["group_id"]],
                                        model_identity={"provenance": "configured-live", "model_id": original.model_id},
                                    )
                                    row.update(status="scored", decision=decision, trace=trace,
                                               grade=grade_task(task, decision, control["rubrics"][task["task_id"]]))
                                except Exception as failure:  # preserve the failed row and trace where available
                                    row.update(status="failed", error=redact(str(failure), secret_values(env)),
                                               grade={"score": 0.0, "business_success": False, "points": {}})
                                result["rows"].append(row)
                                write(output / "rows.json", result["rows"])
                        result["status"] = "completed"
                        result["model_identity"] = original.redacted()
    except Exception as failure:
        result["status"] = "failed"
        result["error"] = {"type": type(failure).__name__, "message": redact(str(failure), secret_values(env))[:1500]}
    finally:
        result["metrics"] = budget.summary() if budget is not None else {"model_calls": 0}
        write(output / "result.json", result)
        write(output / "manifest.json", {str(path.relative_to(output)): digest(path)
              for path in output.rglob("*") if path.is_file() and path.name != "manifest.json"})
    return result


def verify(output: Path) -> dict[str, Any]:
    result = json.loads((output / "result.json").read_text())
    manifest = json.loads((output / "manifest.json").read_text())
    for relative, expected in manifest.items():
        if digest(output / relative) != expected:
            raise ValueError("evidence hash mismatch: " + relative)
    if result["status"] != "completed" or len(result["rows"]) != 4:
        return {"status": "blocked", "reason": "compute transfer incomplete", "result": result}
    for row in result["rows"]:
        if row["status"] != "scored":
            return {"status": "blocked", "reason": "compute row failed", "row": row}
        if not any(execution.get("status") == "completed" for execution in row["trace"]["executions"]):
            raise ValueError("missing completed OpenSandbox execution")
    return {"status": "passed", "rows": len(result["rows"]),
            "groups": {arm: {"mean_score": sum(row["grade"]["score"] for row in result["rows"] if row["arm"] == arm) / 2,
                             "business_success": sum(row["grade"]["business_success"] for row in result["rows"] if row["arm"] == arm)}
                       for arm in ("fixed", "skills")},
            "full_t64": False, "learning_gain_proven": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run", "verify"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--bank", type=Path, default=DEFAULT_BANK)
    parser.add_argument("--task-id", action="append", dest="task_ids")
    args = parser.parse_args()
    ids = tuple(args.task_ids) if args.task_ids else DEFAULT_TASKS
    value = run(args.output, bank_path=args.bank, task_ids=ids) if args.command == "run" else verify(args.output)
    print(json.dumps(value, ensure_ascii=False))
    if args.command == "verify" and value.get("status") != "passed":
        raise SystemExit(2)
