"""Run one real planning Actor baseline through the production HTTP path.

The evaluator owns the model budget gateway; this script only supplies public goal
data to the Harness and records the resulting business/evidence state.  It never
imports the private judge into the Actor process and never supplies a plan answer.
"""

from __future__ import annotations

import argparse
import dataclasses
import fcntl
import hashlib
import importlib.metadata
import json
import math
import os
import socket
import subprocess
import sys
import time
import tomllib
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
EVAL_ROOT = Path("/mnt/c/dev/rsi-eval")
for path in (ROOT / "src", ROOT / "tests", EVAL_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from procurement_eval.budget_proxy import gateway  # noqa: E402
from procurement_eval.core import archive_source, fingerprint  # noqa: E402
from procurement_eval.services import sandbox_control  # noqa: E402
from procurement_eval.worker import all_orders, parse_sse, sandbox_boundary  # noqa: E402
from scripts.planning.judge import grade, solve  # noqa: E402

from agent.config import ModelConfig  # noqa: E402
from agent.env_utils import load_env, redact, secret_values  # noqa: E402
from agent.planning.models import Plan, PlanningProblem  # noqa: E402
from fixtures import agent_protocol_service, erp_service, sandbox_service  # noqa: E402
from live.stack import running_stack  # noqa: E402


def sse_frames(response: Any) -> list[dict[str, Any]]:
    response.raise_for_status()
    return parse_sse(response.text)


def terminal(frames: list[dict[str, Any]]) -> dict[str, Any] | None:
    done = [f["envelope"]["payload"] for f in frames if f["event"] == "done"]
    if len(done) != 1:
        raise RuntimeError("missing or duplicate terminal SSE event")
    if done[0].get("status") not in {"completed", "interrupted", "failed", "cancelled"}:
        raise RuntimeError("invalid terminal status")
    return done[0]


@contextmanager
def locked_ledger(ledger_path: Path, total: float):
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.with_suffix(".lock").open("a+", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        # Corruption fails closed. Only absence creates an empty ledger.
        ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {
            "schema_version": 1, "authorized_total_cny": total, "reservations": []}
        if total != ledger["authorized_total_cny"]:
            raise ValueError("authorization differs from the existing budget session")
        yield ledger
        staging = ledger_path.with_suffix(".tmp")
        with staging.open("w", encoding="utf-8") as destination:
            json.dump(ledger, destination, indent=2)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(staging, ledger_path)
        directory = os.open(ledger_path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def reserve_attempt(ledger_path: Path, amount: float, total: float) -> dict[str, Any]:
    if not math.isfinite(amount) or not math.isfinite(total) or not 0 < amount <= 2 or not 0 < total <= 50:
        raise ValueError("outside the authorized 50 CNY / 2 CNY limits")
    with locked_ledger(ledger_path, total) as ledger:
        used = sum(float(row["reserved_cny"]) for row in ledger["reservations"])
        if used + amount > total + 1e-9:
            raise RuntimeError(f"cumulative budget exhausted: {used + amount:.6f} > {total:.6f}")
        row = {
            "reservation_id": uuid.uuid4().hex,
            "reserved_cny": amount,
            "started_at": time.time(),
            "status": "reserved",
        }
        ledger["reservations"].append(row)
    return row


def settle_attempt(ledger_path: Path, reservation_id: str, metrics: dict[str, Any], total: float):
    with locked_ledger(ledger_path, total) as ledger:
        row = next(r for r in ledger["reservations"] if r["reservation_id"] == reservation_id)
        value = metrics.get("reserved_upper_cny")
        if value is None or not math.isfinite(value) or not 0 <= value <= row["reserved_cny"]:
            raise ValueError("invalid settlement; original reservation retained")
        row.update(reserved_cny=value, metrics=metrics, status="settled", finished_at=time.time())


def public_goal() -> dict[str, Any]:
    return json.loads((ROOT / "fixtures/planning/goal-v1.json").read_text(encoding="utf-8"))


def freeze_runtime(output: Path, configuration: dict, model: ModelConfig) -> dict:
    """Freeze actual execution dependencies before any generation request."""
    import httpx

    jar = erp_service.ensure_jar(output / "java-package.log")
    protocol_python = agent_protocol_service.require_environment()
    versions_code = (
        "import importlib.metadata,json; "
        "print(json.dumps({d.metadata['Name']:d.version "
        "for d in importlib.metadata.distributions()},sort_keys=True))"
    )
    protocol = subprocess.run([str(protocol_python), "-c", versions_code],
                              capture_output=True, text=True, check=True, timeout=30)
    sandbox_config = tomllib.loads((ROOT / "infra/sandbox/sandbox.toml").read_text())
    image_names = [sandbox_service.control_settings().image,
                   sandbox_config["runtime"]["execd_image"]]
    images = []
    for name in image_names:
        completed = subprocess.run(["docker", "image", "inspect", name],
                                   capture_output=True, text=True, check=True, timeout=15)
        info = json.loads(completed.stdout)[0]
        images.append({"tag": name, "id": info["Id"], "repo_digests": info.get("RepoDigests", [])})
    with httpx.Client(timeout=15, trust_env=False) as client:
        response = client.get(model.base_url.rstrip("/") + "/models",
                              headers={"Authorization": "Bearer " + model.api_key})
        response.raise_for_status()
        identities = [item["id"] for item in response.json()["data"]]
    if model.model_id not in identities:
        raise RuntimeError("configured model is absent from provider /models")
    evaluator = fingerprint(EVAL_ROOT / "procurement_eval")
    archive_source(EVAL_ROOT / "procurement_eval", evaluator,
                   output / "evaluator-source.tar.gz", output / "evaluator-source-checkout")
    frozen = {"frozen_at": time.time(), "model_calls": 0,
              "model": model.redacted(), "available_models": identities,
              "model_identity_source": "provider GET /models before generation",
              "erp_jar_path": str(jar), "erp_jar_sha256": hashlib.sha256(jar.read_bytes()).hexdigest(),
              "python_executable": sys.executable,
              "python_version": sys.version,
              "dependencies": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()},
              "agent_protocol_python": str(protocol_python),
              "agent_protocol_dependencies": json.loads(protocol.stdout),
              "images": images, "evaluator_source": evaluator,
              "mongo_database_scope": "fresh independent attempt database",
              "sandbox_port": configuration["sandbox_port"]}
    (output / "runtime-freeze.json").write_text(json.dumps(frozen, indent=2) + "\n")
    return frozen


def drive_turn(client: Any, thread: str, message: str) -> list[dict[str, Any]]:
    return sse_frames(
        client.post(
            "/api/chat/stream",
            json={
                "thread_id": thread,
                "request_id": uuid.uuid4().hex,
                "message": message,
            },
        )
    )


def pending(client: Any, thread: str) -> dict[str, Any]:
    response = client.get(f"/api/chat/{thread}/state")
    response.raise_for_status()
    interrupts = response.json()["data"]["pending_interrupts"]
    if len(interrupts) != 1:
        raise RuntimeError(f"expected one pending interrupt, got {len(interrupts)}")
    return interrupts[0]


def resume(
    client: Any, thread: str, interrupt_id: str, decision: str = "approve"
) -> list[dict[str, Any]]:
    return sse_frames(
        client.post(
            f"/api/chat/{thread}/resume",
            json={
                "request_id": uuid.uuid4().hex,
                "interrupt_id": interrupt_id,
                "resume": {"decisions": [{"type": decision}]},
            },
        )
    )


def run(
    output: Path,
    *,
    total_cny: float = 50.0,
    per_attempt_cny: float = 2.0,
    config_path: Path = EVAL_ROOT / "config.t46.json",
    prepare: bool = False,
    experiment_group: str = "fixed-v1",
    skill_file: Path | None = None,
    goal_data: dict[str, Any] | None = None,
    case_id: str | None = None,
) -> int:
    output.mkdir(parents=True, exist_ok=False)
    owner = "demo-a"
    thread = "planning-live-" + uuid.uuid4().hex[:12]
    ledger_path = EVAL_ROOT / "procurement_eval/runs/planning-session-20261004/budget-ledger.json"
    reservation = reserve_attempt(ledger_path, per_attempt_cny, total_cny)
    attempt = {
        "kind": "real-planning-baseline",
        "owner": owner,
        "thread_id": thread,
        "started_at": time.time(),
        "model_calls": 0,
        "model_id": None,
        "budget_authorization_cny": total_cny,
        "per_attempt_limit_cny": per_attempt_cny,
        "terminal_status": "not_started",
        "frames": [],
        "resumes": [],
        "errors": [],
        "budget_reservation": reservation,
        "budget_ledger": str(ledger_path),
        "schema_version": 2,
        "protocol_version": "procurement-evolution-v2",
        "experiment_group": experiment_group,
        "case_id": case_id,
    }
    (output / "started.json").write_text(json.dumps(attempt, indent=2) + "\n", encoding="utf-8")

    env = load_env()
    configuration = json.loads(config_path.read_text(encoding="utf-8"))
    # The evaluator's isolated Linux JDK is outside the source checkout and must
    # be explicit; never rely on a host JAVA_HOME inherited from WSL/Windows.
    if not os.environ.get("JAVA_HOME"):
        candidate = Path("/tmp/jdk21/usr/lib/jvm/java-21-openjdk-amd64")
        if (candidate / "bin/javac").is_file():
            os.environ["JAVA_HOME"] = str(candidate)
    original = ModelConfig.from_env(
        {**env, "MODEL_ID": configuration["model_id"], "MODEL_TEMPERATURE": "0"}
    )
    attempt["model_id"] = original.model_id
    attempt["source_revision"] = fingerprint(ROOT)
    archive_source(
        ROOT, attempt["source_revision"], output / "source.tar.gz", output / "source-checkout"
    )
    model_log = (output / "model_calls.jsonl").open("x", encoding="utf-8")
    proxy_spec = {
        "total_cny": total_cny,
        "per_attempt_cny": per_attempt_cny,
        "max_model_calls": 0 if prepare else 16,
        "max_output_tokens": 2048,
        "max_request_bytes": 131072,
        "timeout_seconds": 240,
        "input_cny_per_million": 9.0,
        "output_cny_per_million": 27.0,
        "pricing_kind": "conservative DeepSeek estimate",
        "pricing_source": "configured evaluation policy",
    }
    try:
        attempt["runtime_freeze"] = freeze_runtime(output, configuration, original)
        with gateway(original, proxy_spec, model_log, {"thinking": {"type": "disabled"}}) as (
            url,
            budget,
        ):
            model = dataclasses.replace(
                original,
                base_url=url,
                api_key="evaluation-proxy",
                max_tokens=proxy_spec["max_output_tokens"],
            )
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                site_port = probe.getsockname()[1]
            os.environ.update(
                {
                    "MONGODB_URI": configuration["mongo_uri"],
                    "FIXTURES_BASE_URL": f"http://localhost:{site_port}",
                    "AGENT_PROTOCOL_BASE_URL": "http://127.0.0.1:8123",
                    "OPENSANDBOX_DOMAIN": f"127.0.0.1:{configuration['sandbox_port']}",
                    "OPENSANDBOX_BASE_URL": f"http://127.0.0.1:{configuration['sandbox_port']}",
                }
            )
            sandbox_service.CONTROL_PORT = configuration["sandbox_port"]
            agent_protocol_service.ENV_DIR = ROOT / ".venv-agent-protocol-linux"
            run_dir = output / "services"
            with sandbox_control(ROOT, run_dir / "sandbox-control", configuration["sandbox_port"]):
                with running_stack(
                    model_config=model,
                    run_dir=run_dir,
                    database_name=thread,
                    preserve_data=True,
                    warm_pool_size=0,
                ) as stack:
                    import httpx

                    actual_jar = Path(stack.erp.process.args[stack.erp.process.args.index("-jar") + 1])
                    if hashlib.sha256(actual_jar.read_bytes()).hexdigest() != attempt["runtime_freeze"]["erp_jar_sha256"]:
                        raise RuntimeError("executed ERP JAR differs from pre-generation freeze")

                    attempt["erp_before"] = all_orders(stack)
                    attempt["sandbox_boundary"] = sandbox_boundary(stack, output)
                    if prepare:
                        attempt["terminal_status"] = "prepared"
                        attempt["erp_after"] = all_orders(stack)
                        attempt["metrics"] = budget.summary()
                        return 0
                    # Bind an immutable bank before the goal starts.  The three
                    # pilot groups use the same Actor and tools; only this
                    # control-plane bank differs.
                    from agent.evolution.episodes import EpisodeStore, TextSkill
                    evolution = EpisodeStore(stack.database)
                    skills = []
                    if skill_file is not None:
                        payload = json.loads(skill_file.read_text(encoding="utf-8"))
                        skills = [TextSkill.model_validate(payload)]
                    bank_id = evolution.freeze(
                        owner, skills,
                        provenance="fixed-empty-baseline" if not skills else f"{experiment_group}-pilot",
                    )
                    evolution.set_current(owner, bank_id)
                    attempt["bank"] = {"id": bank_id, "sha256": evolution.bank(owner, bank_id)["sha256"],
                                       "skill_count": len(skills)}
                    with httpx.Client(
                        base_url=stack.base_url, timeout=300, trust_env=False
                    ) as client:
                        session = client.post("/api/demo/session", json={"user_id": owner})
                        session.raise_for_status()
                        goal = goal_data if goal_data is not None else public_goal()
                        goal_result = client.post(
                            f"/api/planning/{thread}/goal",
                            json={
                                "budget": goal["budget"],
                                "demands": goal["demands"],
                            },
                        )
                        goal_result.raise_for_status()
                        attempt["goal"] = goal_result.json()["data"]
                        if goal_data is not None:
                            from scripts.planning.evolution_pilot import drive_case
                            drive_case(stack, client, thread, owner, attempt)
                            attempt["metrics"] = budget.summary()
                            attempt["model_calls"] = budget.calls
                            if fingerprint(ROOT)["files"] != attempt["source_revision"]["files"]:
                                raise RuntimeError("source changed during execution")
                            return 0 if attempt["terminal_status"] == "completed" else 1
                        frames = drive_turn(
                            client,
                            thread,
                            "请先检查所有必需物料的可行性和交期，再用持久计算比较候选；提交合法的多供应商方案并逐单等待审批。",
                        )
                        attempt["frames"].append(frames)
                        if terminal(frames) and terminal(frames).get("status") == "interrupted":
                            action = pending(client, thread)
                            frames = resume(client, thread, action["interrupt_id"])
                            attempt["resumes"].append(
                                {
                                    "interrupt_id": action["interrupt_id"],
                                    "decision": "approve",
                                    "frames": frames,
                                }
                            )
                            attempt["frames"].append(frames)
                        if not terminal(frames) or terminal(frames).get("status") != "interrupted":
                            raise RuntimeError(
                                "baseline did not produce the second approval interrupt"
                            )
                        stale_action = pending(client, thread)
                        initial_row = stack.database.planning_goals.find_one(
                            {"owner_user_id": owner, "thread_id": thread}
                        )
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
                        revision = client.patch(
                            f"/api/planning/{thread}/goal",
                            json={
                                "expected_revision": 1,
                                "budget": "2200.00",
                            },
                        )
                        attempt["revision_http_status"] = revision.status_code
                        revision.raise_for_status()
                        attempt["revision"] = (
                            revision.json()
                            if revision.headers.get("content-type", "").startswith(
                                "application/json"
                            )
                            else revision.text[:500]
                        )
                        row = stack.database.planning_goals.find_one(
                            {"owner_user_id": owner, "thread_id": thread}
                        )
                        attempt["old_orders"] = row.get("orders", []) if row else []
                        if row and row.get("orders"):
                            attempt["planning_revision"] = row["revision"]
                        stale = client.post(
                            f"/api/chat/{thread}/resume",
                            json={
                                "request_id": uuid.uuid4().hex,
                                "interrupt_id": stale_action["interrupt_id"],
                                "resume": {"decisions": [{"type": "approve"}]},
                            },
                        )
                        attempt["stale_approval"] = {
                            "status": stale.status_code,
                            "body": stale.text[:1000],
                        }
                        if stale.status_code != 400 or "STALE_PLAN" not in stale.text:
                            raise RuntimeError("stale approval was not refused")
                        status = client.get(f"/api/planning/{thread}/goal")
                        attempt["goal_after_revision"] = (
                            status.json() if status.status_code == 200 else status.text[:500]
                        )
                        follow = drive_turn(
                            client,
                            thread,
                            "预算现在是2200元。必须先用 computation_execute 的 load_state 读取已提交的 constraints、quotes 或其实际持久名称；不要在新代码中重新抄写报价。只重算受影响的可选分配；先保留已批准订单，不重复采购，再提交新候选。",
                        )
                        attempt["frames"].append(follow)
                        for _ in range(2):
                            if (
                                not terminal(follow)
                                or terminal(follow).get("status") != "interrupted"
                            ):
                                break
                            action = pending(client, thread)
                            follow = resume(client, thread, action["interrupt_id"])
                            attempt["resumes"].append(
                                {
                                    "interrupt_id": action["interrupt_id"],
                                    "decision": "approve",
                                    "frames": follow,
                                }
                            )
                            attempt["frames"].append(follow)
                    attempt["database"] = stack.settings.database
                    attempt["erp_after"] = all_orders(stack)
                    attempt["kernel_sessions"] = list(
                        stack.database.planning_kernel_sessions.find(
                            {"owner_user_id": owner, "thread_id": thread}, {"_id": 0}
                        )
                    )
                    attempt["kernel_executions"] = list(
                        stack.database.planning_kernel_executions.find(
                            {"owner_user_id": owner, "thread_id": thread}, {"_id": 0}
                        )
                    )
                    attempt["episode"] = stack.database.planning_episodes.find_one(
                        {"owner_user_id": owner, "thread_id": thread}
                    )
                    attempt["orders"] = list(
                        stack.database.planning_goals.find_one(
                            {"owner_user_id": owner, "thread_id": thread}, {"_id": 0, "orders": 1}
                        ).get("orders", [])
                    )
                    from agent.evolution.episodes import EpisodeStore

                    attempt["episode_export"] = EpisodeStore(stack.database).export(
                        owner, attempt["episode"]["_id"]
                    )
                    final_row = stack.database.planning_goals.find_one(
                        {"owner_user_id": owner, "thread_id": thread}, {"_id": 0}
                    )
                    attempt["final_goal"] = final_row
                    final_problem = PlanningProblem.model_validate(final_row["problem"])
                    attempt["final_judge"] = dataclasses.asdict(
                        grade(final_problem, Plan.model_validate(final_row["proposal"]["plan"]))
                    )
                    if fingerprint(ROOT)["files"] != attempt["source_revision"]["files"]:
                        raise RuntimeError("source changed during execution")
            final_status = terminal(attempt["frames"][-1]) if attempt["frames"] else None
            if final_status and final_status.get("status") == "failed":
                raise RuntimeError("planning stream failed after revision")
            if not final_status or final_status.get("status") not in {"completed", "interrupted"}:
                raise RuntimeError("planning stream did not reach a valid terminal event")
            attempt["terminal_status"] = (
                "completed" if final_status.get("status") == "completed" else "failed"
            )
            attempt["metrics"] = budget.summary()
            attempt["model_calls"] = budget.calls
    except Exception as failure:  # retain structured failure; caller/gate decides pass
        attempt["terminal_status"] = "failed"
        attempt["errors"].append({"type": type(failure).__name__,
                                  "message": redact(str(failure), secret_values(env))[:1000]})
        attempt["metrics"] = (
            locals().get("budget").summary() if "budget" in locals() else
            {"model_calls": 0, "reserved_upper_cny": 0.0, "usage_complete": True}
        )
        attempt["model_calls"] = attempt["metrics"].get("model_calls", 0)
    finally:
        model_log.close()
        try:
            settle_attempt(ledger_path, reservation["reservation_id"], attempt.get("metrics", {}), total_cny)
        except Exception as settlement_error:
            attempt["errors"].append({"type": type(settlement_error).__name__, "message": str(settlement_error)[:500]})
            attempt["budget_settlement"] = "unknown; original reservation retained"
        attempt["finished_at"] = time.time()
        (output / "result.json").write_text(
            json.dumps(attempt, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
        )
        manifest = {
            p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in output.rglob("*")
            if p.is_file() and p.name != "evidence-manifest.json"
        }
        (output / "evidence-manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
    return 0 if attempt["terminal_status"] == "completed" else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--total-cny", type=float, default=50.0)
    parser.add_argument("--per-attempt-cny", type=float, default=2.0)
    parser.add_argument("--config", type=Path, default=EVAL_ROOT / "config.t46.json")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--experiment-group", choices=["fixed-v1", "retrieval-v1", "curated-v1"], default="fixed-v1")
    parser.add_argument("--skill-file", type=Path)
    args = parser.parse_args()
    return run(
        args.output,
        total_cny=args.total_cny,
        per_attempt_cny=args.per_attempt_cny,
        config_path=args.config,
        prepare=args.prepare,
        experiment_group=args.experiment_group,
        skill_file=args.skill_file,
    )


if __name__ == "__main__":
    raise SystemExit(main())
