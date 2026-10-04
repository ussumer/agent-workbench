"""Run one real planning Actor baseline through the production HTTP path.

The evaluator owns the model budget gateway; this script only supplies public goal
data to the Harness and records the resulting business/evidence state.  It never
imports the private judge into the Actor process and never supplies a plan answer.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
EVAL_ROOT = Path("/mnt/c/dev/rsi-eval")
for path in (ROOT / "src", ROOT / "tests", EVAL_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from procurement_eval.budget_proxy import gateway  # noqa: E402
from procurement_eval.services import sandbox_control  # noqa: E402
from scripts.planning.judge import grade, solve  # noqa: E402

from agent.config import ModelConfig  # noqa: E402
from agent.env_utils import load_env  # noqa: E402
from agent.planning.models import Plan, PlanningProblem  # noqa: E402
from fixtures import agent_protocol_service, sandbox_service  # noqa: E402
from live.stack import running_stack  # noqa: E402


def sse_frames(response: Any) -> list[dict[str, Any]]:
    response.raise_for_status()
    result = []
    for line in response.text.splitlines():
        if line.startswith("data:"):
            result.append(json.loads(line[5:].strip()))
    return result


def public_goal() -> dict[str, Any]:
    return json.loads((ROOT / "fixtures/planning/goal-v1.json").read_text(encoding="utf-8"))


def drive_turn(client: Any, thread: str, message: str) -> list[dict[str, Any]]:
    return sse_frames(client.post("/api/chat/stream", json={
        "thread_id": thread, "request_id": uuid.uuid4().hex, "message": message,
    }))


def pending(client: Any, thread: str) -> dict[str, Any]:
    response = client.get(f"/api/chat/{thread}/state")
    response.raise_for_status()
    interrupts = response.json()["data"]["pending_interrupts"]
    if len(interrupts) != 1:
        raise RuntimeError(f"expected one pending interrupt, got {len(interrupts)}")
    return interrupts[0]


def resume(client: Any, thread: str, interrupt_id: str, decision: str = "approve") -> list[dict[str, Any]]:
    return sse_frames(client.post(f"/api/chat/{thread}/resume", json={
        "request_id": uuid.uuid4().hex, "interrupt_id": interrupt_id,
        "resume": {"decisions": [{"type": decision}]},
    }))


def run(output: Path, *, total_cny: float = 50.0, per_attempt_cny: float = 2.0) -> int:
    output.mkdir(parents=True, exist_ok=False)
    owner = "demo-a"
    thread = "planning-live-" + uuid.uuid4().hex[:12]
    attempt = {
        "schema_version": 1, "kind": "real-planning-baseline", "owner": owner,
        "thread_id": thread, "started_at": time.time(), "model_calls": 0,
        "model_id": "deepseek-flash", "budget_authorization_cny": total_cny,
        "per_attempt_limit_cny": per_attempt_cny, "terminal_status": "not_started",
        "frames": [], "resumes": [], "errors": [],
    }
    (output / "started.json").write_text(json.dumps(attempt, indent=2) + "\n", encoding="utf-8")

    env = load_env()
    # The evaluator's isolated Linux JDK is outside the source checkout and must
    # be explicit; never rely on a host JAVA_HOME inherited from WSL/Windows.
    if not os.environ.get("JAVA_HOME"):
        candidate = Path("/tmp/jdk21/usr/lib/jvm/java-21-openjdk-amd64")
        if (candidate / "bin/javac").is_file():
            os.environ["JAVA_HOME"] = str(candidate)
    original = ModelConfig.from_env({**env, "MODEL_ID": "deepseek-flash", "MODEL_TEMPERATURE": "0"})
    model_log = (output / "model_calls.jsonl").open("x", encoding="utf-8")
    proxy_spec = {
        "total_cny": total_cny, "per_attempt_cny": per_attempt_cny,
        "max_model_calls": 16, "max_output_tokens": 2048,
        "max_request_bytes": 131072, "timeout_seconds": 240,
        "input_cny_per_million": 9.0, "output_cny_per_million": 27.0,
        "pricing_kind": "conservative DeepSeek estimate", "pricing_source": "configured evaluation policy",
    }
    try:
        with gateway(original, proxy_spec, model_log, {"thinking": {"type": "disabled"}}) as (url, budget):
            model = dataclasses.replace(original, base_url=url, api_key="evaluation-proxy",
                                        max_tokens=proxy_spec["max_output_tokens"])
            os.environ.update({
                "MONGODB_URI": "mongodb://127.0.0.1:27028",
                "FIXTURES_BASE_URL": "http://localhost:8088",
                "AGENT_PROTOCOL_BASE_URL": "http://127.0.0.1:8123",
                "OPENSANDBOX_DOMAIN": "127.0.0.1:18081",
                "OPENSANDBOX_BASE_URL": "http://127.0.0.1:18081",
            })
            sandbox_service.CONTROL_PORT = 18081
            agent_protocol_service.ENV_DIR = ROOT / ".venv-agent-protocol-linux"
            run_dir = output / "services"
            with sandbox_control(ROOT, run_dir / "sandbox-control", 18081):
                with running_stack(model_config=model, run_dir=run_dir, database_name="rush_harness_test_" + thread,
                                   preserve_data=True, warm_pool_size=0) as stack:
                    import httpx

                    with httpx.Client(base_url=stack.base_url, timeout=300, trust_env=False) as client:
                        session = client.post("/api/demo/session", json={"user_id": owner})
                        session.raise_for_status()
                        goal = public_goal()
                        goal_result = client.post(f"/api/planning/{thread}/goal", json={
                            "budget": goal["budget"], "demands": goal["demands"],
                        })
                        goal_result.raise_for_status()
                        attempt["goal"] = goal_result.json()["data"]
                        frames = drive_turn(client, thread,
                                            "请先检查所有必需物料的可行性和交期，再用持久计算比较候选；提交合法的多供应商方案并逐单等待审批。")
                        attempt["frames"].append(frames)
                        if frames and frames[-1]["payload"].get("status") == "interrupted":
                            action = pending(client, thread)
                            frames = resume(client, thread, action["interrupt_id"])
                            attempt["resumes"].append({"interrupt_id": action["interrupt_id"], "decision": "approve", "frames": frames})
                            attempt["frames"].append(frames)
                        if not frames or frames[-1]["payload"].get("status") != "interrupted":
                            raise RuntimeError("baseline did not produce the second approval interrupt")
                        stale_action = pending(client, thread)
                        initial_row = stack.database.planning_goals.find_one({"owner_user_id": owner, "thread_id": thread})
                        if initial_row and initial_row.get("proposal"):
                            problem = PlanningProblem.model_validate(initial_row["problem"])
                            initial_plan = Plan.model_validate(initial_row["proposal"]["plan"])
                            attempt["initial_judge"] = {
                                "oracle": dataclasses.asdict(solve(problem)),
                                "grade": dataclasses.asdict(grade(problem, initial_plan)),
                            }
                            attempt["initial_proposal"] = initial_row["proposal"]

                        # A fresh constraint is deliberately changed only after the first
                        # result; the next request must reuse committed quotes and reject
                        # the old proposal rather than buying the old optional quantity.
                        revision = client.patch(f"/api/planning/{thread}/goal", json={
                            "expected_revision": 1, "budget": "2200.00",
                        })
                        attempt["revision_http_status"] = revision.status_code
                        attempt["revision"] = revision.json() if revision.headers.get("content-type", "").startswith("application/json") else revision.text[:500]
                        row = stack.database.planning_goals.find_one({"owner_user_id": owner, "thread_id": thread})
                        attempt["old_orders"] = row.get("orders", []) if row else []
                        if row and row.get("orders"):
                            attempt["planning_revision"] = row["revision"]
                        stale = client.post(f"/api/chat/{thread}/resume", json={
                            "request_id": uuid.uuid4().hex, "interrupt_id": stale_action["interrupt_id"],
                            "resume": {"decisions": [{"type": "approve"}]},
                        })
                        attempt["stale_approval"] = {"status": stale.status_code, "body": stale.text[:1000]}
                        status = client.get(f"/api/planning/{thread}/goal")
                        attempt["goal_after_revision"] = status.json() if status.status_code == 200 else status.text[:500]
                        follow = drive_turn(client, thread,
                                            "预算现在是2200元。读取已有报价和约束，只重算受影响的可选分配；先保留已批准订单，不重复采购，再提交新候选。")
                        attempt["frames"].append(follow)
                        for _ in range(2):
                            if not follow or follow[-1]["payload"].get("status") != "interrupted":
                                break
                            action = pending(client, thread)
                            follow = resume(client, thread, action["interrupt_id"])
                            attempt["resumes"].append({"interrupt_id": action["interrupt_id"], "decision": "approve", "frames": follow})
                            attempt["frames"].append(follow)
                    attempt["database"] = stack.settings.database
                    attempt["kernel_sessions"] = list(stack.database.planning_kernel_sessions.find(
                        {"owner_user_id": owner, "thread_id": thread}, {"_id": 0, "values_json": 0}))
                    attempt["kernel_executions"] = list(stack.database.planning_kernel_executions.find(
                        {"owner_user_id": owner, "thread_id": thread}, {"_id": 0, "code": 0}))
                    attempt["episode"] = stack.database.planning_episodes.find_one(
                        {"owner_user_id": owner, "thread_id": thread}, {"_id": 0})
                    attempt["orders"] = list(stack.database.planning_goals.find_one(
                        {"owner_user_id": owner, "thread_id": thread}, {"_id": 0, "orders": 1}).get("orders", []))
            attempt["terminal_status"] = "completed"
            attempt["metrics"] = budget.summary()
            attempt["model_calls"] = budget.calls
    except Exception as failure:  # retain structured failure; caller/gate decides pass
        attempt["terminal_status"] = "failed"
        attempt["errors"].append({"type": type(failure).__name__, "message": str(failure)[:1000]})
        attempt["metrics"] = locals().get("budget").summary() if "budget" in locals() else {"model_calls": 0}
        attempt["model_calls"] = attempt["metrics"].get("model_calls", 0)
    finally:
        model_log.close()
        attempt["finished_at"] = time.time()
        (output / "result.json").write_text(json.dumps(attempt, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return 0 if attempt["terminal_status"] == "completed" else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--total-cny", type=float, default=50.0)
    parser.add_argument("--per-attempt-cny", type=float, default=2.0)
    args = parser.parse_args()
    return run(args.output, total_cny=args.total_cny, per_attempt_cny=args.per_attempt_cny)


if __name__ == "__main__":
    raise SystemExit(main())
