"""T68: paired persistent JSON vs recomputation, with learning disabled.

Only this experiment's service wrapper evicts data. Production ComputationService
and T64/T65 remain unchanged. Python always runs in real OpenSandbox.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT = ROOT / "artifacts/experiments/t68-persistent-paired-20261009"
ARMS = ("A", "B")
SOURCE_FILES = (
    "fixtures/planning/gdpevo-procurement-v3.json",
    "fixtures/planning/gdpevo-procurement-v3-training.json",
    "fixtures/planning/private/gdpevo-procurement-v3-control.json",
    "scripts/planning/persistent_computation_ablation.py",
    "src/agent/planning/computation.py",
    "src/agent/planning/computation_process.py",
    "src/agent/tools/planning_computation.py",
    "scripts/planning/gdpevo_expansion_judge.py",
)


def hash_value(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def load_inputs():
    public = json.loads((ROOT / SOURCE_FILES[0]).read_text())
    training = json.loads((ROOT / SOURCE_FILES[1]).read_text())
    control = json.loads((ROOT / SOURCE_FILES[2]).read_text())
    tasks = [t for t in public["tasks"] if t["split"] == "test"]
    if len(tasks) != 20:
        raise ValueError("expected the complete twenty-task v3 development test")
    policies = {}
    for task in public["tasks"]:
        if task["split"] == "train":
            for rule in training["tasks"][task["task_id"]]["policy_evidence"]:
                policies.setdefault(task["group_id"], {})[rule["rule_id"]] = {
                    k: rule[k] for k in ("rule_id", "name", "condition", "policy", "stop")}
    return public, tasks, policies, control


def stages(task):
    revised = copy.deepcopy(task)
    revised["input"]["budget_cents"] = task["input"]["budget_cents"] * 9 // 10
    revised["input"]["revision"] += 1
    return [copy.deepcopy(task), copy.deepcopy(task), revised]


def pair_schedule(tasks, repeats=3):
    if repeats not in (1, 3):
        raise ValueError("use one authorized diagnostic repeat or three repeats")
    return [{"task_id": t["task_id"], "repeat": repeat,
             "order": list(ARMS if (i + repeat) % 2 else reversed(ARMS))}
            for repeat in range(1, repeats + 1) for i, t in enumerate(tasks)]


def metadata(values):
    return {name: hash_value(value) for name, value in values.items()}


class PairedService:
    """Own serial experimental data eviction and identical sandbox replacement.

    Raw task is an immutable source, never a derived cache. A/B preserve execution
    ledgers and output; they cannot recover old JSON via leftover container files.
    """

    def __init__(self, delegate, manager, arm):
        if arm not in ARMS:
            raise ValueError("unknown experimental arm")
        self.delegate, self.manager, self.arm = delegate, manager, arm
        self.events = []
        self.lock = threading.RLock()
        self.source = None
        self.writers = {}

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def _row(self, owner, thread, session):
        return self.delegate._session(self.delegate._scope(owner, thread, session))

    def _set_source(self, owner, thread, session="analysis", *, evict=False):
        row = self._row(owner, thread, session)
        if row["active"] is not None:
            raise RuntimeError("cannot evict an active computation")
        previous = json.loads(row["values_json"])
        values = {} if evict else previous.copy()
        values["task"] = copy.deepcopy(self.source)
        result = self.delegate.sessions.update_one(
            {"_id": row["_id"], "version": row["version"], "active": None},
            {"$set": {"values_json": json.dumps(values, allow_nan=False)}})
        if result.matched_count != 1:
            raise RuntimeError("experiment source/eviction CAS failed")
        return previous, values

    def update_source(self, owner, thread, view):
        with self.lock:
            self.source = copy.deepcopy(view)
            self._set_source(owner, thread, evict=self.arm == "B")

    def status(self, owner, thread, *, session="analysis"):
        with self.lock:
            self._set_source(owner, thread, session, evict=self.arm == "B")
            return self.delegate.status(owner, thread, session=session)

    def execute(self, owner, thread, code, *, read_names=(), session="analysis", **kwargs):
        with self.lock:
            self._set_source(owner, thread, session, evict=self.arm == "B")
            before = json.loads(self._row(owner, thread, session)["values_json"])
            event = {"session": session, "code_bytes": len(code.encode()),
                     "read_names": list(read_names), "before": metadata(before),
                     "source_hash": hash_value(self.source),
                     "prior_writers": {n: self.writers.get((session, n)) for n in read_names if n != "task"}}
            started = time.monotonic()
            result = None
            try:
                proxy = self.manager.get_or_create(owner)
                event["sandbox_id"] = proxy.id
                event["startup_seconds"] = time.monotonic() - started
                started = time.monotonic()
                result = self.delegate.execute(owner, thread, code, read_names=read_names,
                                               session=session, **kwargs)
                event["execution_seconds"] = time.monotonic() - started
                event["result"] = result
                if result.get("status") == "completed":
                    for name in result.get("saved_names", []):
                        self.writers[session, name] = result["operation_id"]
                return result
            except Exception as exc:
                event["refused_code"] = getattr(exc, "code", type(exc).__name__)
                raise
            finally:
                # Raw source is restored in both arms; only B drops derived names.
                saved, after = self._set_source(owner, thread, session, evict=self.arm == "B")
                event.update(committed=metadata(saved), after=metadata(after))
                cleanup = time.monotonic()
                event["sandbox_recycled"] = self.manager.recycle(owner)
                event["cleanup_seconds"] = time.monotonic() - cleanup
                self.events.append(event)


def summary(rows):
    result = {}
    for arm in ARMS:
        selected = [r for r in rows if r["arm"] == arm]
        turns = [t for r in selected for t in r["turns"]]
        finals = [r["turns"][-1] for r in selected]
        result[arm] = {
            "trajectories": len(selected),
            "whole_success": sum(all(t["grade"]["business_success"] for t in r["turns"]) for r in selected),
            "final_success": sum(t["grade"]["business_success"] for t in finals),
            "final_mean_score": sum(t["grade"]["score"] for t in finals) / max(1, len(finals)),
            "model_calls": sum(t["metrics"]["episode_model_calls"] for t in turns),
            "input_tokens": sum(t["metrics"]["input_tokens"] for t in turns),
            "output_tokens": sum(t["metrics"]["output_tokens"] for t in turns),
            "computation_calls": sum(len(r["events"]) for r in selected),
            "code_bytes": sum(e["code_bytes"] for r in selected for e in r["events"]),
            "elapsed_seconds": sum(t["elapsed_seconds"] for t in turns),
            "execution_seconds": sum(e.get("execution_seconds", 0) for r in selected for e in r["events"]),
            "sandbox_startup_seconds": sum(e.get("startup_seconds", 0) for r in selected for e in r["events"]),
            "sandbox_cleanup_seconds": sum(e.get("cleanup_seconds", 0) for r in selected for e in r["events"]),
            "reuse_calls": sum(e["result"].get("status") == "completed" and
                              any(writer for writer in e["prior_writers"].values())
                              for r in selected for e in r["events"] if e.get("result")),
        }
    paired = {}
    for row in rows:
        paired.setdefault((row["task_id"], row["repeat"]), {})[row["arm"]] = row
    differences = []
    for (tid, repeat), pair in paired.items():
        if set(pair) == set(ARMS):
            a, b = pair["A"]["turns"][-1], pair["B"]["turns"][-1]
            differences.append({"task_id": tid, "repeat": repeat,
                                "final_score_delta": a["grade"]["score"] - b["grade"]["score"],
                                "whole_success_delta": int(all(t["grade"]["business_success"] for t in pair["A"]["turns"])) -
                                                       int(all(t["grade"]["business_success"] for t in pair["B"]["turns"]))})
    result["paired"] = differences
    return result


def prepare(output=DEFAULT, repeats=3):
    output = output.resolve()
    if output.exists():
        raise FileExistsError(output)
    public, tasks, policies, _ = load_inputs()
    output.mkdir(parents=True)
    write(output / "inputs.json", {"environment": public["environment"], "policies": policies,
                                  "tasks": tasks, "stages": {t["task_id"]: stages(t) for t in tasks}})
    frozen = {}
    for name in SOURCE_FILES:
        data = (ROOT / name).read_bytes()
        (output / "frozen" / name).parent.mkdir(parents=True, exist_ok=True)
        (output / "frozen" / name).write_bytes(data)
        frozen[name] = hashlib.sha256(data).hexdigest()
    write(output / "protocol.json", {
        "kind": "persistent-computation-paired-ablation-v1", "taskset": "synthetic-v3-development-test",
        "task_count": 20, "repeats": repeats, "turns_per_trajectory": 3,
        "arm_order": pair_schedule(tasks, repeats), "arms": list(ARMS),
        "curator": False, "learning": False, "selector": False, "learned_skills": False,
        "source_name": "task", "B_eviction": "after each computation_execute, derived JSON only",
        "container_policy": "replace in both arms after every computation_execute",
        "model_call_limit": 30, "tool_call_limit": 36, "recursion_limit": 120,
        "source_hashes": frozen, "input_hash": hash_value(json.loads((output / "inputs.json").read_text())),
        "production_assignment_changed": False, "sealed_test": False,
        "evaluation_scope": "single-repeat diagnostic" if repeats == 1 else "three-repeat development evaluation",
    })
    return output


def runtime_imports():
    for folder in (ROOT, ROOT / "src", ROOT / "tests", Path("/mnt/c/dev/rsi-eval")):
        if str(folder) not in sys.path:
            sys.path.insert(0, str(folder))


def trajectory(stack, model, output, arm, task, tasks, environment, policies, rubric, partial=None):
    from deepagents import (create_deep_agent, HarnessProfile, GeneralPurposeSubagentProfile,
                            register_harness_profile)
    from deepagents.backends import StateBackend
    from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
    from langchain_core.messages import HumanMessage
    from agent.planning.computation import ComputationService
    from agent.persistence.repository import ApplicationRepository
    from agent.persistence.scoped_store import UserScopedStore
    from agent.tools.planning_computation import build_computation_tools
    from scripts.planning.gdpevo_tool_diagnostic import _parse, _register_compute_profile, COMPUTE_ONLY_EXCLUDED_TOOLS
    from scripts.planning.gdpevo_calibration import FORMAT
    from scripts.planning.gdpevo_expansion_judge import grade_task
    from scripts.planning.gdpevo_v5_compute_training import UsageCollector

    owner, thread = "t68-" + uuid.uuid4().hex[:16], "t68-" + uuid.uuid4().hex
    ApplicationRepository(stack.database).ensure_thread(owner_user_id=owner, thread_id=thread, title="T68 paired")
    delegate = ComputationService(stack.database, stack.manager.get_or_create)
    service = PairedService(delegate, stack.manager, arm)
    tools = build_computation_tools(service)
    system = ("你是采购决策计算Actor，不下单。每轮使用Python核验当前数据，再直接输出完整JSON。"
              "原始公开来源task每次均可用，用read_names=[\"task\"]读取。"
              "可以自行save_state组织派生计算JSON，不存在固定planner。串行执行计算。"
              "每次Python执行后两组均换干净容器，stdout及历史仍保留。"
              + ("派生JSON会跨调用保留，可load_state复用；修订时自行检查过期依赖。" if arm == "A" else
                 "派生JSON只在本次执行有效，调用后清除，下一次需重建；原始task与历史仍保留。")
              + environment["public_contract"] + "\n" + environment["audit_contract"] + "\n" + FORMAT
              + "\n另外输出freight_audit与erp_claim_conflicts，严格按公开审计合同。"
              + "\n当前企业规则：" + json.dumps(policies, ensure_ascii=False))
    key = _register_compute_profile(model)
    register_harness_profile(key, HarnessProfile(
        excluded_tools=COMPUTE_ONLY_EXCLUDED_TOOLS | {"write_todos"},
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)))
    graph = create_deep_agent(model=model, tools=tools, backend=StateBackend(),
        checkpointer=stack.resources.checkpointer, store=UserScopedStore(stack.store, owner),
        middleware=[ModelCallLimitMiddleware(run_limit=30, exit_behavior="end"),
                    ToolCallLimitMiddleware(run_limit=36, exit_behavior="end")], system_prompt=system)
    output.mkdir(parents=True)
    row = {"arm": arm, "task_id": task["task_id"], "owner": owner, "thread_id": thread,
           "system_prompt": system, "tool_schemas": [t.tool_call_schema.model_json_schema() for t in tools],
           "tool_names": [t.name for t in tools], "turns": [], "events": []}
    if partial is not None:
        if arm != 'B' or len(partial['turns']) != 2 or any(set(e['after']) != {'task'} for e in partial['events']):
            raise ValueError('only a source-only B trajectory can resume from saved conversation')
        from langchain_core.messages import messages_from_dict
        # model_dump records carry message type directly, while deserializer expects
        # the public {type,data} envelope.
        messages = messages_from_dict([{'type':m['type'],'data':m} for m in partial['turns'][-1]['messages']])
        graph.update_state({'configurable':{'owner_user_id':owner,'thread_id':thread}}, {'messages':messages})
        row['turns'] = copy.deepcopy(partial['turns'])
        row['events'] = copy.deepcopy(partial['events'])
        row['resumed_history_from'] = partial['thread_id']
        row['interrupted_inflight_call_not_scored'] = True
    prior_events = list(row['events'])
    for index, current in enumerate(tasks, 1):
        if index <= len(row['turns']):
            continue
        view = {"environment": environment, "task": current}
        service.update_source(owner, thread, view)
        usage = UsageCollector()
        config = {"configurable": {"owner_user_id": owner, "thread_id": thread},
                  "recursion_limit": 120, "callbacks": [usage]}
        turn = {"turn": index, "input": view, "input_hash": hash_value(view),
                "status": "started", "grade": {"score": 0., "business_success": False, "points": {}}}
        before = len(service.events)
        started = time.monotonic()
        instruction = ["请规划当前任务。", "原始资料与已有订单未变化，请复核原决策，仍需Python核验。",
                       "预算已降低且revision递增，已有订单不变；请重新规划，禁止复用旧批准。"] [index - 1]
        try:
            result = graph.invoke({"messages": [HumanMessage(content=instruction + "\n" + json.dumps(view, ensure_ascii=False))]}, config=config)
            decision = _parse(result["messages"][-1].content)
            turn.update(status="scored", decision=decision, grade=grade_task(current, decision, rubric))
        except (ValueError, json.JSONDecodeError) as exc:
            turn.update(status="format_failed", error=type(exc).__name__)
        except Exception as exc:
            turn.update(status="environment_failed", error=type(exc).__name__)
        turn.update(metrics=usage.metrics(), elapsed_seconds=time.monotonic() - started,
                    computation_calls=len(service.events) - before)
        snapshot = graph.get_state(config)
        turn["messages"] = [m.model_dump(mode="json") for m in snapshot.values.get("messages", [])]
        turn["state"] = service.status(owner, thread)
        row["turns"].append(turn)
        row["events"] = prior_events + list(service.events)
        write(output / "result.json", row)
    row["executions"] = list(delegate.executions.find({"owner_user_id": owner, "thread_id": thread}, {"_id": 0}))
    stack.manager.recycle(owner)
    write(output / "result.json", row)
    return row


def erp_orders(stack):
    import httpx
    with httpx.Client(trust_env=False, timeout=10) as client:
        response = client.get(stack.erp.url("/api/erp/v1/orders"), headers=stack.erp.headers())
        response.raise_for_status()
        return response.json()


def mechanism_probe(stack, output):
    """Zero-model manipulation check, not a business success or learned result."""
    from agent.planning.computation import ComputationService
    from agent.planning.computation_protocol import ComputationError
    from agent.persistence.repository import ApplicationRepository
    report = {}
    for arm in ARMS:
        owner, thread = "t68-probe-"+arm, "t68-probe-"+uuid.uuid4().hex
        ApplicationRepository(stack.database).ensure_thread(owner_user_id=owner, thread_id=thread, title="zero-model T68 probe")
        service = PairedService(ComputationService(stack.database, stack.manager.get_or_create), stack.manager, arm)
        service.update_source(owner, thread, {"literal":17, "commitments":[{"order_id":"unchanged"}]})
        first = service.execute(owner, thread, 'raw=load_state("task"); save_state("derived",raw["literal"]); print(raw["literal"])', read_names=['task'])
        if first['status'] != 'completed':
            raise RuntimeError("real mechanism probe failed: " + str(first.get('error')))
        if arm == 'A':
            second = service.execute(owner, thread, 'print(load_state("derived"))', read_names=['derived'])
        else:
            try:
                service.execute(owner, thread, 'print(load_state("derived"))', read_names=['derived'])
                raise AssertionError("B retained prior derived data")
            except ComputationError as exc:
                if exc.code != 'STATE_NOT_FOUND':
                    raise
            second = service.execute(owner, thread, 'print(load_state("task")["literal"])', read_names=['task'])
        if second['status'] != 'completed' or second['stdout'].strip() != '17':
            raise RuntimeError("real mechanism probe did not restore/rebuild same result")
        report[arm] = {"first":first,"second":second,"events":service.events}
    write(output / 'mechanism-probe.json', {"status":"passed","model_calls":0,"business_evidence":False,"arms":report})


def run(output=DEFAULT, repeats=3, port=18084, resume_from=None):
    runtime_imports()
    import dataclasses
    from fixtures import agent_protocol_service, sandbox_service
    from live.stack import running_stack
    from procurement_eval.budget_proxy import gateway
    from procurement_eval.services import sandbox_control
    from scripts.planning.gdpevo_v4_training import UnlimitedCalls
    from scripts.planning.gdpevo_v5_compute_training import policy

    output = output.resolve()
    if not output.exists():
        prepare(output, repeats)
    if (output / "model_calls.jsonl").exists():
        raise FileExistsError("attempt already started; retain failures, use a new output")
    protocol = json.loads((output / "protocol.json").read_text())
    for name, expected in protocol["source_hashes"].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError("prepared source changed; create a new prepared output: " + name)
    if repeats != protocol["repeats"]:
        raise ValueError("prepared repeat count differs")
    inputs = json.loads((output / "inputs.json").read_text())
    control = json.loads((output / "frozen" / SOURCE_FILES[2]).read_text())
    caller = UnlimitedCalls()
    os.environ.update({k: v for k, v in caller.env.items() if v is not None})
    agent_protocol_service.ENV_DIR = Path("/mnt/c/dev/rush-harness/.venv-agent-protocol-linux")
    sandbox_service.CONTROL_PORT = port
    protocol.update(model=caller.model.redacted(), temperature=0, thinking="disabled", sandbox_port=port)
    write(output / "protocol.json", protocol)
    rows = []
    partials = {}
    if resume_from is not None:
        source = resume_from.resolve()
        source_protocol = json.loads((source / "protocol.json").read_text())
        source_inputs = json.loads((source / "inputs.json").read_text())
        if source_inputs != inputs or source_protocol.get("kind") != protocol["kind"]:
            raise ValueError("resume source has different paired inputs/protocol")
        if source_protocol["model"] != protocol["model"]:
            raise ValueError("resume model configuration differs")
        for name in SOURCE_FILES:
            if name == "scripts/planning/persistent_computation_ablation.py":
                continue  # only scope/resume handling changed; preserve both sources
            if source_protocol["source_hashes"][name] != protocol["source_hashes"][name]:
                raise ValueError("runtime/task source changed: " + name)
        source_manifest = json.loads((source / "manifest.json").read_text())
        for name, expected in source_manifest.items():
            if hashlib.sha256((source / name).read_bytes()).hexdigest() != expected:
                raise ValueError("resume source evidence changed: " + name)
        completed = json.loads((source / "rows.json").read_text())
        rows = [r for r in completed if r['repeat'] <= repeats and len(r['turns']) == 3]
        for row in rows:
            relative = Path("pairs") / f"repeat-{row['repeat']}" / row['task_id'] / row['arm']
            shutil.copytree(source / relative, output / relative)
        write(output / 'resume-source.json', {"source":str(source),"reused_trajectories":len(rows),
              "reason":"user authorized twenty pairs, one repeat; no best-run selection",
              "source_manifest_sha256":hashlib.sha256((source/'manifest.json').read_bytes()).hexdigest()})
        shutil.copy2(source / 'model_calls.jsonl',output / 'source-model-calls.jsonl')
        shutil.copy2(source / 'protocol.json',output / 'source-protocol.json')
        write(output / 'rows.json',rows)
        for path in (source/'pairs').rglob('result.json'):
            partial = json.loads(path.read_text())
            if len(partial['turns']) == 2 and partial['arm']=='B':
                repeat = int(path.parents[2].name.split('-')[-1])
                if repeat <= repeats:
                    partials[partial['task_id'],repeat,'B'] = partial
    try:
        with (output / "model_calls.jsonl").open("x") as log:
            gateway_policy = policy() | {"max_model_calls": 20 * repeats * 2 * 3 * 30}
            with gateway(caller.model, gateway_policy, log, {"thinking": {"type": "disabled"}}) as (url, budget):
                configured = dataclasses.replace(caller.model, base_url=url, api_key="evaluation-proxy", max_tokens=4096)
                model = configured.create_chat_model()
                with sandbox_control(ROOT, output / "services/sandbox-control", port):
                    with running_stack(model_config=configured, run_dir=output / "services/stack",
                                       database_name="t68-" + uuid.uuid4().hex[:10], preserve_data=True,
                                       warm_pool_size=0, planning_only=True) as stack:
                        write(output / "erp-before.json", erp_orders(stack))
                        mechanism_probe(stack, output)
                        by_id = {t["task_id"]: t for t in inputs["tasks"]}
                        for pair in protocol["arm_order"]:
                            for arm in pair["order"]:
                                if any(r['task_id']==pair['task_id'] and r['repeat']==pair['repeat'] and r['arm']==arm for r in rows):
                                    continue
                                task = by_id[pair["task_id"]]
                                row = trajectory(stack, model, output / "pairs" / f"repeat-{pair['repeat']}" / task["task_id"] / arm,
                                                 arm, task, inputs["stages"][task["task_id"]], inputs["environment"],
                                                 inputs["policies"][task["group_id"]], control["rubrics"][task["task_id"]],
                                                 partials.get((task['task_id'],pair['repeat'],arm)))
                                row["repeat"] = pair["repeat"]
                                rows.append(row)
                                write(output / "rows.json", rows)
                                print(pair["repeat"], task["task_id"], arm,
                                      [t["grade"]["business_success"] for t in row["turns"]], flush=True)
                        write(output / "erp-after.json", erp_orders(stack))
                        write(output / "report.json", {"summary": summary(rows), "usage": budget.summary()})
    finally:
        write(output / "manifest.json", {str(f.relative_to(output)): hashlib.sha256(f.read_bytes()).hexdigest()
                                        for f in output.rglob("*") if f.is_file() and f.name != "manifest.json"})
    return summary(rows)


def verify(output=DEFAULT):
    output = output.resolve()
    manifest_path = output / "manifest.json"
    if not manifest_path.exists() or not (output / "report.json").exists():
        return {"status": "blocked", "reason": "incomplete real attempt"}
    for name, expected in json.loads(manifest_path.read_text()).items():
        if hashlib.sha256((output / name).read_bytes()).hexdigest() != expected:
            raise ValueError("evidence changed: " + name)
    protocol = json.loads((output / "protocol.json").read_text())
    inputs = json.loads((output / "inputs.json").read_text())
    rows = json.loads((output / "rows.json").read_text())
    expected = {(p["task_id"], p["repeat"], a) for p in protocol["arm_order"] for a in ARMS}
    if len(rows) != len(expected) or {(r["task_id"], r["repeat"], r["arm"]) for r in rows} != expected:
        return {"status": "blocked", "reason": "paired matrix incomplete"}
    if protocol["task_count"] != 20 or protocol["repeats"] not in (1,3) or any(protocol[k] for k in ("curator", "learning", "selector", "learned_skills")):
        raise ValueError("scope or learning contamination")
    control = json.loads((output / "frozen" / SOURCE_FILES[2]).read_text())
    runtime_imports()
    from scripts.planning.gdpevo_expansion_judge import grade_task
    pairs = {}
    coverage_gaps = []
    cleanup_gaps = []
    for row in rows:
        if len(row["turns"]) != 3:
            raise ValueError("missing conversation turn")
        pairs.setdefault((row["task_id"], row["repeat"]), {})[row["arm"]] = row
        for turn, task in zip(row["turns"], inputs["stages"][row["task_id"]]):
            view = {"environment": inputs["environment"], "task": task}
            if turn["input"] != view or turn["input_hash"] != hash_value(view):
                raise ValueError("paired input mismatch")
            if turn["status"] == "scored" and turn["grade"] != grade_task(task, turn["decision"], control["rubrics"][task["task_id"]]):
                raise ValueError("grade mismatch")
            if turn["status"] != "scored" and turn["grade"]["score"] != 0:
                raise ValueError("failed turn not in denominator")
        for event in row["events"]:
            if event["sandbox_recycled"] is not True and event.get("refused_code") not in {
                    "STATE_NOT_FOUND", "VERSION_CONFLICT", "DUPLICATE_OPERATION", "COMPUTATION_BUSY"}:
                cleanup_gaps.append({"task_id": row["task_id"], "arm": row["arm"],
                                     "refused_code": event.get("refused_code"),
                                     "sandbox_id_recorded": bool(event.get("sandbox_id"))})
            if row["arm"] == "B" and (set(event["before"]) != {"task"} or set(event["after"]) != {"task"}):
                raise ValueError("B retained derived data")
        completed = [e for e in row.get("executions", []) if e["status"] == "completed"]
        if not completed:
            coverage_gaps.append({"task_id": row["task_id"], "arm": row["arm"]})
    for pair in pairs.values():
        if pair["A"]["tool_names"] != pair["B"]["tool_names"] or pair["A"]["tool_schemas"] != pair["B"]["tool_schemas"]:
            raise ValueError("tool permissions differ")
    report = json.loads((output / "report.json").read_text())
    if report["summary"] != summary(rows):
        raise ValueError("summary does not recompute")
    erp_before = json.loads((output / "erp-before.json").read_text())
    erp_after = json.loads((output / "erp-after.json").read_text())
    # ERP assigns a new request_id to each read; compare the order payload.
    if "data" not in erp_before or "data" not in erp_after or erp_before["data"] != erp_after["data"]:
        raise ValueError("ERP orders changed")
    if coverage_gaps or cleanup_gaps:
        return {"status": "blocked", "reason": "real computation/cleanup evidence incomplete",
                "trajectories": len(rows), "turns": len(rows)*3,
                "without_completed_execution": coverage_gaps,
                "unconfirmed_sandbox_recycle": cleanup_gaps,
                "business_effect_established": False}
    return {"status": "passed", "trajectories": len(rows), "turns": len(rows)*3,
            "A_reuse_calls": report["summary"]["A"]["reuse_calls"], "production_assignment_changed": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "run", "verify"))
    parser.add_argument("--output", type=Path, default=DEFAULT)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--port", type=int, default=18084)
    parser.add_argument("--resume-from",type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        value = {"status": "prepared", "output": str(prepare(args.output, args.repeats)), "model_calls": 0}
    elif args.command == "run":
        value = run(args.output, args.repeats, args.port, args.resume_from)
    else:
        value = verify(args.output)
    print(json.dumps(value, ensure_ascii=False))
    if args.command == "verify" and value["status"] != "passed":
        raise SystemExit(2)
