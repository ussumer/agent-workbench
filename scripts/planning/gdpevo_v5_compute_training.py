"""Full v3 GDPevo/TRACE evolution run with the real DeepAgents/OpenSandbox compute Actor.

Same faithful protocol as the text runner attempt-gdpevo-v4-faithful-20261005j, but every
arm executes through the compute Actor: per-episode owner/sandbox isolation, Mongo-persisted
computation data, and PlanningTraceMiddleware per-turn selection for the dynamic arm.
The fewshot/skills arms receive statically injected frozen skill bodies; fixed receives none.
Curator input stays train-only; held-out test gets no feedback, no repair and no reselection.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
EVAL = Path("/mnt/c/dev/rsi-eval")
for folder in (ROOT, ROOT / "src", ROOT / "tests", EVAL):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from langchain_core.callbacks import BaseCallbackHandler  # noqa: E402
from procurement_eval.budget_proxy import gateway  # noqa: E402
from procurement_eval.services import sandbox_control  # noqa: E402
from scripts.planning.gdpevo_calibration import digest, write  # noqa: E402
from scripts.planning.gdpevo_expansion import actor_view  # noqa: E402
from scripts.planning.gdpevo_expansion_judge import grade_task  # noqa: E402
from scripts.planning.gdpevo_tool_diagnostic import _compute_actor, _messages  # noqa: E402
from scripts.planning.gdpevo_v4_training import (  # noqa: E402
    ARMS,
    GROUPS,
    UnlimitedCalls,
    curator_prompt,
    feedback,
    fewshot_prompt,
    fresh_call_directory,
    group_curator_view,
    instance_tokens,
    load_inputs,
    parse_skill_list,
    reflect_prompt,
    select_validation_candidates,
    skill_for_task,
    write_manifest,
)

from scripts.planning.gdpevo_dense_fewshot import examples_for
from scripts.planning.gdpevo_v4_training import OUTCOMES

from agent.env_utils import redact  # noqa: E402
from fixtures import agent_protocol_service, sandbox_service  # noqa: E402
from live.stack import running_stack  # noqa: E402

SESSION = ROOT / "artifacts/experiments/t65-compute-dense-20261007"
TEXT_BASELINE = EVAL / "procurement_eval/runs/planning-session-20261005/attempt-gdpevo-v4-faithful-20261005j"
ACTOR_KIND = "deepagents-opensandbox-compute"
FROZEN_SOURCES = (
    "fixtures/planning/gdpevo-procurement-v3.json",
    "fixtures/planning/gdpevo-procurement-v3-training.json",
    "fixtures/planning/private/gdpevo-procurement-v3-control.json",
    "scripts/planning/gdpevo_v5_compute_training.py",
    "scripts/planning/gdpevo_dense_fewshot.py",
    "scripts/planning/gdpevo_v4_training.py",
    "scripts/planning/gdpevo_tool_diagnostic.py",
    "scripts/planning/gdpevo_feedback.py",
    "scripts/planning/gdpevo_expansion_judge.py",
    "src/agent/evolution/orchestration.py",
    "src/agent/evolution/episodes.py",
)


def policy() -> dict[str, Any]:
    return {
        "total_cny": None,
        "per_attempt_cny": None,
        "enforce_cost_limit": False,
        "max_model_calls": 6000,
        "max_output_tokens": 4096,
        "max_request_bytes": 500_000,
        "timeout_seconds": 240,
        "input_cny_per_million": 9.0,
        "output_cny_per_million": 27.0,
        "pricing_kind": "conservative estimate; not provider invoice",
        "pricing_source": "user revoked monetary ceilings; usage remains recorded",
    }


class UsageCollector(BaseCallbackHandler):
    """Per-episode token accounting; a collector failure must never break an episode."""

    def __init__(self) -> None:
        super().__init__()
        self.input_tokens = 0
        self.output_tokens = 0
        self.selector_input_tokens = 0
        self.selector_output_tokens = 0
        self.calls = 0
        self.usage_complete = True

    def on_llm_end(self, response, *, run_id, parent_run_id=None, **kwargs):
        self.calls += 1
        usage = (response.llm_output or {}).get("usage") or {}
        prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
        if prompt is None or completion is None:
            try:
                meta = getattr(response.generations[0][0].message, "usage_metadata", None) or {}
                prompt, completion = meta.get("input_tokens"), meta.get("output_tokens")
            except (IndexError, AttributeError, TypeError):
                prompt = completion = None
        if not isinstance(prompt, int) or not isinstance(completion, int):
            self.usage_complete = False
            return
        self.input_tokens += prompt
        self.output_tokens += completion
        if (kwargs.get("metadata") or {}).get("planning_role") == "skill_selector":
            self.selector_input_tokens += prompt
            self.selector_output_tokens += completion

    def metrics(self) -> dict[str, int | bool]:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "selector_input_tokens": self.selector_input_tokens,
                "selector_output_tokens": self.selector_output_tokens,
                "episode_model_calls": self.calls, "usage_complete": self.usage_complete}


def owner_for(arm: str, task_id: str, repeat: int | None = None, *, phase: str | None = None) -> str:
    owner = f"t64c-{phase + '-' if phase else ''}{arm}-{task_id}" + (f"-r{repeat}" if repeat is not None else "")
    if len(owner) > 64 or "/" in owner:
        raise ValueError("episode owner out of bounds")
    return owner


def static_skill_block(skills: list[dict]) -> str:
    return "\n当前聚焦技能（只作策略提示，服从当前任务输入）：\n" + "\n".join(
        f"[{s['skill_id']}] {s['description']}\n{s['body']}" for s in skills)


def dense_example_suffix(task: dict, train: list[dict], answers: dict) -> str:
    examples = examples_for(task, train, answers, exclude_self=task["split"] == "train")
    return "\n完整同组train示例（验证排除自身；不得复制实例值）：\n" + json.dumps(examples, ensure_ascii=False)


def repair_suffix(decision: dict | None, row_feedback: dict) -> str:
    return ("\n上次尝试的决策与公开诊断如下。只修复诊断指出的问题，重新完整作答；不提供参考答案：\n上次决策："
            + json.dumps(decision, ensure_ascii=False)[:3000]
            + "\n公开诊断：" + json.dumps(row_feedback, ensure_ascii=False)[:3000])


def arm_skills(arm: str, task: dict, skills: dict[str, list[dict]],
               fewshot_skills: dict[str, list[dict]]) -> tuple[list[dict] | None, list[dict] | None]:
    """Return (dynamic_bank, static_skills); the two injection modes are exclusive."""
    if arm == "dynamic":
        return skill_for_task(skills, task), None
    if arm == "fewshot":
        return None, skill_for_task(fewshot_skills, task)
    if arm == "skills" or arm.startswith("reflect"):
        return None, skill_for_task(skills, task)
    return None, None


class Context:
    def __init__(self, public: dict, training: dict, control: dict, stack: Any, model: Any,
                 secrets: list[str]) -> None:
        self.public, self.training, self.control = public, training, control
        self.stack, self.model, self.secrets = stack, model, secrets
        self.model_identity = {"provenance": "configured-live", "model_id": getattr(model, "model_name", None)}


def compute_episode(ctx: Context, directory: Path, task: dict, control: dict, arm: str, *,
                    phase: str, repeat: int | None = None, dynamic_bank: list[dict] | None = None,
                    static: list[dict] | None = None, system_suffix: str = "") -> dict:
    call_directory = fresh_call_directory(directory)
    view = actor_view(ctx.public, ctx.training, task["task_id"], task["split"])
    system = _messages(ctx.public, ctx.training, task, compute=True)[0]["content"]
    owner = owner_for(arm, task["task_id"], repeat, phase=phase)
    row: dict[str, Any] = {"task_id": task["task_id"], "group_id": task["group_id"], "split": task["split"],
                           "arm": arm, "owner": owner, "status": "started",
                           "grade": {"score": 0.0, "business_success": False, "points": {}},
                           "metrics": {"input_tokens": 0, "output_tokens": 0}}
    if repeat is not None:
        row["repeat"] = repeat
    started = time.time()
    usage = UsageCollector()
    if dynamic_bank is not None:
        from agent.evolution.episodes import TextSkill
        dynamic_bank = [TextSkill.model_validate(skill) for skill in dynamic_bank]
    try:
        decision, trace = _compute_actor(
            ctx.stack, ctx.model, view, call_directory, system,
            skills=dynamic_bank, static_skills=static, system_suffix=system_suffix,
            owner=owner, model_identity=ctx.model_identity, extra_callbacks=[usage])
        row["decision"] = decision
        row["grade"] = grade_task(task, decision, control["rubrics"][task["task_id"]])
        row["status"] = "scored"
        row["feedback"] = feedback(task, decision, row["grade"])
        row["feedback"]["failed_outcomes"] = [p for p in OUTCOMES
            if not row["grade"]["points"].get(f"{task['task_id']}:{p}", False)]
        row["trace"] = {"thread_id": trace["thread_id"], "message_count": trace["message_count"],
                        "executions": len(trace["executions"]),
                        "completed_executions": sum(1 for e in trace["executions"] if e.get("status") == "completed")}
        write(call_directory / "submission.json", decision)
    except (json.JSONDecodeError, ValueError) as exc:
        row["status"] = "format_failed"
        row["errors"] = [type(exc).__name__, redact(str(exc)[:300], ctx.secrets)]
        row["feedback"] = feedback(task, None, row["grade"])
    except Exception as exc:  # sandbox/gateway/evidence failures stay in the denominator
        row["status"] = "environment_failed"
        row["errors"] = [type(exc).__name__, redact(str(exc)[:500], ctx.secrets)]
        row["feedback"] = feedback(task, None, row["grade"])
    row["metrics"] = usage.metrics()
    row["elapsed_seconds"] = round(time.time() - started, 3)
    write(call_directory / "result.json", row)
    try:
        row["sandbox_recycled"] = bool(ctx.stack.manager.recycle(owner))
    except Exception as exc:  # cleanup failure must not mask the episode result
        row["sandbox_recycled"] = False
        row["recycle_error"] = type(exc).__name__
        write(call_directory / "result.json", row)
    return row


def run_batch(jobs: list, workers: int) -> list[dict]:
    if workers <= 1:
        return [job() for job in jobs]
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        return [future.result() for future in [pool.submit(job) for job in jobs]]


def reusable_row(directory: Path) -> dict | None:
    for candidate in [directory, *sorted(directory.parent.glob(directory.name + "-retry-*"))]:
        path = candidate / "result.json"
        if path.is_file():
            row = json.loads(path.read_text())
            if row.get("status") in ("scored", "format_failed"):
                return row
    return None


def copy_reusable(source: Path, output: Path) -> None:
    for name in ("train", "curator", "curator-input", "reflect", "fewshot-curator",
                 "fewshot-curator-input", "validation", "test"):
        if (source / name).is_dir():
            shutil.copytree(source / name, output / name, dirs_exist_ok=True)
    for name in ("training-records.json", "skills.json", "fewshot-skills.json",
                 "validation.json", "validation-summary.json", "test-results.json"):
        if (source / name).is_file():
            shutil.copy2(source / name, output / name)


def source_is_compute(source: Path) -> bool:
    protocol = source / "protocol.json"
    return protocol.is_file() and json.loads(protocol.read_text()).get("actor") == ACTOR_KIND


def diff_usage(before: dict, after: dict) -> dict:
    keys = ("model_calls", "input_tokens", "output_tokens", "cost_estimate_cny")
    return {k: round(after.get(k, 0) - before.get(k, 0), 6) for k in keys}


def run(output: Path = SESSION, *, test_repeats: int = 3, workers: int = 3,
        resume_from: Path | None = None) -> dict:
    if output.exists():
        raise FileExistsError(output)
    if test_repeats < 3:
        raise ValueError("T65 requires at least three development-test repeats")
    if resume_from is not None and (not source_is_compute(resume_from) or
            json.loads((resume_from / "protocol.json").read_text()).get("supervision_version") != "dense-policy-v2"):
        raise ValueError("resume source is not a compute-actor attempt")
    public, training, control, answers, train, test = load_inputs()
    output.mkdir(parents=True)
    (output / "frozen").mkdir()
    (output / "curator-input").mkdir()
    for relative in FROZEN_SOURCES:
        (output / "frozen" / Path(relative).name).write_bytes((ROOT / relative).read_bytes())
    caller = UnlimitedCalls()
    if resume_from is not None:
        copy_reusable(resume_from, output)
        write(output / "resume-source.json", {"source": str(resume_from)})
    config = json.loads((EVAL / "config.t46.json").read_text())
    sandbox_port = int(os.environ.get("T64_SANDBOX_PORT", config["sandbox_port"]))
    protocol = {"kind": "full v3 GDPevo TRACE evolution (compute actor)", "actor": ACTOR_KIND,
                "text_baseline_attempt": str(TEXT_BASELINE), "model_id": caller.model.model_id,
                "temperature": 0, "thinking": "disabled", "train_count": len(train),
                "test_count": len(test), "test_repeats": test_repeats, "arms": ARMS,
                "episode_isolation": "dedicated owner and sandbox per episode",
                "dynamic_selector": "PlanningTraceMiddleware per-turn selection",
                "sandbox_port": sandbox_port,
                "static_skill_arms": ["fewshot", "skills"], "faithful_supervision": True,
                "curator_input": "train public input/output/diagnostics/arithmetic only",
                "test_feedback": False, "production_assignment_changed": False,
                "learning_gain_proven": False, "selection_policy": "aggregate-v2",
                "episode_budget": {"model_calls": 30, "tool_calls": 36, "recursion_limit": 120},
                "resumed_from": str(resume_from) if resume_from else None, "workers": workers,
                "supervision_version": "dense-policy-v2",
                "test_role": "development test already inspected in T64",
                "fewshot_examples": "same-group full train input/answer; train validation excludes self",
                "source_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "source_hashes": {p.name: digest(p) for p in (output / "frozen").iterdir()}}
    write(output / "protocol.json", protocol)
    forbidden = instance_tokens(public)
    env = caller.env
    os.environ.update({k: v for k, v in env.items() if v is not None})
    protocol_env = ROOT / ".venv-agent-protocol-linux"
    if not protocol_env.exists():
        protocol_env = Path("/mnt/c/dev/rush-harness/.venv-agent-protocol-linux")
    agent_protocol_service.ENV_DIR = protocol_env
    if not os.environ.get("JAVA_HOME"):
        os.environ["JAVA_HOME"] = "/tmp/jdk21"
    original = caller.model
    secrets = caller.secrets
    phase_usage: dict[str, Any] = {}
    sandbox_service.CONTROL_PORT = sandbox_port
    with (output / "model_calls.jsonl").open("x") as log:
        with gateway(original, policy(), log, {"thinking": {"type": "disabled"}}) as (url, budget):
            configured = dataclasses.replace(original, base_url=url, api_key="evaluation-proxy", max_tokens=4096)
            model = configured.create_chat_model()
            with sandbox_control(ROOT, output / "services" / "sandbox-control", sandbox_port):
                with running_stack(model_config=configured, run_dir=output / "services" / "stack",
                                   database_name="v4-compute-t64-" + uuid.uuid4().hex[:10],
                                   preserve_data=True, warm_pool_size=0, planning_only=True) as stack:
                    ctx = Context(public, training, control, stack, model, secrets)
                    mark = budget.summary()

                    # ---- train: fixed compute episodes with one public-diagnosis repair ----
                    records: list[dict] = []
                    source_records = output / "training-records.json"
                    if source_records.is_file():
                        records = json.loads(source_records.read_text())
                        if len(records) != len(train) or {r["task"]["task_id"] for r in records} != {t["task_id"] for t in train}:
                            raise ValueError("resume source does not contain all twenty train records")
                    else:
                        def train_task(task: dict) -> dict:
                            first = reusable_row(output / "train" / task["task_id"] / "attempt-1")
                            if first is None:
                                first = compute_episode(ctx, output / "train" / task["task_id"] / "attempt-1",
                                                        task, control, "fixed", phase="train")
                            attempts = [{"attempt": 1, "decision": first.get("decision"), "grade": first["grade"],
                                         "feedback": first["feedback"], "status": first["status"],
                                         "metrics": first.get("metrics", {})}]
                            if not first["grade"]["business_success"] and first["status"] == "scored":
                                second = reusable_row(output / "train" / task["task_id"] / "attempt-2")
                                if second is None:
                                    second = compute_episode(
                                        ctx, output / "train" / task["task_id"] / "attempt-2", task, control,
                                        "repair", phase="train",
                                        system_suffix=repair_suffix(first.get("decision"), first["feedback"]))
                                attempts.append({"attempt": 2, "decision": second.get("decision"), "grade": second["grade"],
                                                 "feedback": second["feedback"], "status": second["status"],
                                                 "metrics": second.get("metrics", {})})
                            return {"task": {k: task[k] for k in ("task_id", "group_id", "split", "request", "input")},
                                    "attempts": attempts}
                        for record in run_batch([lambda t=task: train_task(t) for task in train], workers):
                            records.append(record)
                            write(output / "training-records.json", records)
                    write(output / "training-records.json", records)
                    phase_usage["train"] = diff_usage(mark, budget.summary())
                    mark = budget.summary()

                    # ---- learning: group curator, reflect-3 compute rollouts, few-shot gold ----
                    skills: dict[str, list[dict]] = {}
                    fewshot_skills: dict[str, list[dict]] = {}
                    curator_calls: list[dict] = []
                    if (output / "skills.json").is_file() and (output / "fewshot-skills.json").is_file():
                        skills = json.loads((output / "skills.json").read_text())
                        fewshot_skills = json.loads((output / "fewshot-skills.json").read_text())
                    else:
                        for group in GROUPS:
                            material = group_curator_view(public, training, records, group)
                            write(output / "curator-input" / f"{group}.json", material)
                            response, call = caller.call(
                                fresh_call_directory(output / "curator" / group / "initial"),
                                [{"role": "system", "content": curator_prompt(group, "initial")},
                                 {"role": "user", "content": json.dumps(material, ensure_ascii=False)}],
                                label=f"curator-{group}")
                            curator_calls.append(call)
                            current = parse_skill_list(response, forbidden)
                            write(output / "curator" / group / "initial-skills.json", current)
                            for round_id in range(1, 4):
                                group_tasks = [t for t in train if t["group_id"] == group]
                                round_rows = run_batch([
                                    (lambda t=task, r=round_id, s=list(current):
                                        reusable_row(output / "reflect" / f"round-{r}" / t["task_id"])
                                        or compute_episode(ctx, output / "reflect" / f"round-{r}" / t["task_id"],
                                                           t, control, f"reflect-{r}", phase="reflect", static=s))
                                    for task in group_tasks], workers)
                                write(output / "reflect" / f"round-{round_id}" / f"{group}-rollouts.json", round_rows)
                                reflect_view = {"group_id": group, "policies": material["policies"],
                                                "current_skills": current,
                                                "rollouts": [{"task_id": r["task_id"], "decision": r.get("decision"),
                                                              "grade": r["grade"], "feedback": r.get("feedback")}
                                                             for r in round_rows]}
                                response, call = caller.call(
                                    fresh_call_directory(output / "curator" / group / f"reflect-{round_id}"),
                                    [{"role": "system", "content": reflect_prompt(group)},
                                     {"role": "user", "content": json.dumps(reflect_view, ensure_ascii=False)}],
                                    label=f"reflect-{group}-{round_id}")
                                curator_calls.append(call)
                                current = parse_skill_list(response, forbidden)
                                write(output / "curator" / group / f"reflect-{round_id}.json", current)
                            skills[group] = current
                            gold_material = {"group_id": group, "policies": material["policies"],
                                "environment": public["environment"], "train": [
                                {"task_id": t["task_id"], "request": t["request"], "input": t["input"],
                                 "correct_answer": answers["tasks"][t["task_id"]][0]}
                                for t in train if t["group_id"] == group]}
                            (output / "fewshot-curator-input").mkdir(parents=True, exist_ok=True)
                            write(output / "fewshot-curator-input" / f"{group}.json", gold_material)
                            response, call = caller.call(
                                fresh_call_directory(output / "fewshot-curator" / group),
                                [{"role": "system", "content": fewshot_prompt(group) + "企业规则是推导依据；逐项核对规则与答案，禁止凭通用采购习惯补规则。"},
                                 {"role": "user", "content": json.dumps(gold_material, ensure_ascii=False)}],
                                label=f"fewshot-curator-{group}")
                            curator_calls.append(call)
                            fewshot_skills[group] = parse_skill_list(response, forbidden)
                            write(output / "fewshot-curator" / f"{group}-skills.json", fewshot_skills[group])
                        write(output / "skills.json", skills)
                        write(output / "fewshot-skills.json", fewshot_skills)
                    phase_usage["learning"] = diff_usage(mark, budget.summary())
                    mark = budget.summary()

                    # ---- validation: four compute arms on train, selection frozen before test ----
                    validation: list[dict] = []
                    def validation_row(task: dict, arm: str) -> dict:
                        base = output / "validation" / arm / task["task_id"]
                        reused = reusable_row(base / "actor" if arm == "dynamic" else base)
                        if reused is not None:
                            return reused
                        dynamic_bank, static = arm_skills(arm, task, skills, fewshot_skills)
                        return compute_episode(ctx, base / "actor" if arm == "dynamic" else base,
                                               task, control, arm, phase="validation",
                                               dynamic_bank=dynamic_bank, static=static,
                                               system_suffix=dense_example_suffix(task, train, answers)
                                               if arm == "fewshot" else "")
                    validation = run_batch([
                        (lambda t=task, a=arm: validation_row(t, a))
                        for task in train for arm in ARMS], workers)
                    write(output / "validation.json", validation)

                    def scores(rows: list[dict]) -> dict:
                        return {arm: {"count": len([r for r in rows if r["arm"] == arm]),
                                      "mean_score": sum(r["grade"]["score"] for r in rows if r["arm"] == arm)
                                      / max(1, len([r for r in rows if r["arm"] == arm])),
                                      "business_success": sum(bool(r["grade"]["business_success"])
                                                              for r in rows if r["arm"] == arm)}
                                for arm in ARMS}
                    validation_summary = scores(validation)
                    write(output / "validation-summary.json", validation_summary)
                    write(output / "candidate-selection.json", select_validation_candidates(validation))
                    phase_usage["validation"] = diff_usage(mark, budget.summary())
                    mark = budget.summary()

                    # ---- held-out test: four arms x twenty tasks x repeats, zero feedback ----
                    test_rows: list[dict] = []
                    if (output / "test-results.json").is_file():
                        test_rows = json.loads((output / "test-results.json").read_text())
                    for repeat in range(1, test_repeats + 1):
                        def test_row(task: dict, arm: str, repeat: int = repeat) -> dict:
                            base = output / "test" / f"repeat-{repeat}" / arm / task["task_id"]
                            reused = reusable_row(base / "actor" if arm == "dynamic" else base)
                            if reused is not None:
                                return reused | {"repeat": repeat}
                            dynamic_bank, static = arm_skills(arm, task, skills, fewshot_skills)
                            row = compute_episode(ctx, base / "actor" if arm == "dynamic" else base,
                                                  task, control, arm, phase="test", repeat=repeat,
                                                  dynamic_bank=dynamic_bank, static=static,
                                               system_suffix=dense_example_suffix(task, train, answers)
                                               if arm == "fewshot" else "")
                            return row | {"repeat": repeat}
                        done = {(r["repeat"], r["arm"], r["task_id"]) for r in test_rows}
                        jobs = [(lambda t=task, a=arm: test_row(t, a))
                                for arm in ARMS for task in test
                                if (repeat, arm, task["task_id"]) not in done]
                        for row in run_batch(jobs, workers):
                            test_rows.append(row)
                        write(output / "test-results.json", test_rows)
                    phase_usage["test"] = diff_usage(mark, budget.summary())

                    total = budget.summary()
                    report = {"summary": {"validation": validation_summary, "test": scores(test_rows)},
                              "validation": validation, "test_rows": test_rows,
                              "curator_calls": curator_calls,
                              "usage": {"total": total, "phases": phase_usage,
                                        "note": "resumed attempts record only new calls; source attempt keeps its own ledger"},
                              "claims": {"actor": ACTOR_KIND, "test_feedback": False,
                                         "production_assignment_changed": False,
                                         "learning_gain_proven": False,
                                         "heldout_repeats": test_repeats,
                                         "model_identity": original.redacted()}}
                    write(output / "report.json", report)
                    write(output / "bank.json", {"scope": "experiment-only",
                                                 "production_assignment_changed": False,
                                                 "skills": skills, "validation_summary": validation_summary})
    write_manifest(output)
    return report["summary"] if "report" in locals() else {"status": "failed"}


def verify(output: Path = SESSION) -> dict:
    manifest = json.loads((output / "manifest.json").read_text())
    for relative, expected in manifest.items():
        if digest(output / relative) != expected:
            raise ValueError("evidence hash mismatch: " + relative)
    protocol = json.loads((output / "protocol.json").read_text())
    if protocol.get("actor") != ACTOR_KIND or protocol.get("test_feedback") is not False:
        raise ValueError("not a compute-actor attempt with test feedback disabled")
    repeats = int(protocol["test_repeats"])
    public, training, control, answers, train, test = load_inputs()

    records = json.loads((output / "training-records.json").read_text())
    if len(records) != len(train):
        return {"status": "blocked", "reason": "incomplete train records"}
    validation = json.loads((output / "validation.json").read_text())
    if {(r["arm"], r["task_id"]) for r in validation} != {(a, t["task_id"]) for a in ARMS for t in train}:
        return {"status": "blocked", "reason": "validation arm matrix incomplete"}
    selection = json.loads((output / "candidate-selection.json").read_text())
    if selection != select_validation_candidates(validation):
        raise ValueError("candidate selection does not recompute from validation rows")
    test_rows = json.loads((output / "test-results.json").read_text())
    expected = {(r, a, t["task_id"]) for r in range(1, repeats + 1) for a in ARMS for t in test}
    if {(r["repeat"], r["arm"], r["task_id"]) for r in test_rows} != expected:
        return {"status": "blocked", "reason": "held-out repeat matrix incomplete"}
    for path in (output / "test").rglob("attempt-2"):
        return {"status": "blocked", "reason": "held-out test contains a repair attempt", "path": str(path)}
    for material in (output / "curator-input").glob("*.json"):
        text = material.read_text()
        if re.search(r"(?:quotes|packages|kits|revisions)-test-\d+", text):
            raise ValueError("curator input references held-out tasks: " + material.name)
    forbidden = instance_tokens(public)
    for bank_name in ("skills.json", "fewshot-skills.json"):
        bank = json.loads((output / bank_name).read_text())
        for group_skills in bank.values():
            for skill in group_skills:
                visible = skill["description"] + "\n" + skill["body"]
                if any(token in visible for token in forbidden if "-test-" in token):
                    raise ValueError("skill contains held-out instance token: " + skill["skill_id"])
    coverage = {"scored_rows": 0, "rows_with_model_execution": 0}
    for result_path in output.rglob("result.json"):
        row = json.loads(result_path.read_text())
        trace_path = result_path.parent / "compute-trace.json"
        if row.get("status") != "scored" or not trace_path.is_file():
            continue
        coverage["scored_rows"] += 1
        executions = json.loads(trace_path.read_text()).get("executions", [])
        if any(e.get("status") == "completed" and e.get("read_names") for e in executions):
            coverage["rows_with_model_execution"] += 1
    return {"status": "passed", "actor": ACTOR_KIND, "repeats": repeats,
            "test_rows": len(test_rows), "coverage": coverage,
            "learning_gain_proven": False, "production_assignment_changed": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run", "verify"))
    parser.add_argument("--output", type=Path, default=SESSION)
    parser.add_argument("--repeats", type=int, default=3, dest="test_repeats")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--resume-from", type=Path, default=None)
    args = parser.parse_args()
    value = run(args.output, test_repeats=args.test_repeats, workers=args.workers,
                resume_from=args.resume_from) if args.command == "run" else verify(args.output)
    print(json.dumps(value, ensure_ascii=False))
    if args.command == "verify" and value.get("status") != "passed":
        raise SystemExit(2)
