"""Full v3 GDPevo/TRACE evolution run with train-only reflection and repeats.

The runner deliberately keeps the private control plane out of Curator input.  Gold
train answers are used only by the explicit few-shot actor arm; they never enter
the learned skill or the test grader's feedback loop.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
EVAL = Path("/mnt/c/dev/rsi-eval")
for folder in (ROOT, ROOT / "src", EVAL):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from procurement_eval.budget_proxy import gateway  # noqa: E402
from scripts.planning.gdpevo_calibration import FORMAT, digest, write  # noqa: E402
from scripts.planning.gdpevo_expansion import actor_view  # noqa: E402
from scripts.planning.gdpevo_expansion_judge import grade_task  # noqa: E402
from scripts.planning.gdpevo_feedback import arithmetic_audit, diagnose_submission  # noqa: E402
from scripts.planning.gdpevo_refinement import summarize  # noqa: E402
from agent.config import ModelConfig  # noqa: E402
from agent.env_utils import load_env, redact, secret_values  # noqa: E402

PUBLIC = ROOT / "fixtures/planning/gdpevo-procurement-v3.json"
TRAINING = ROOT / "fixtures/planning/gdpevo-procurement-v3-training.json"
CONTROL = ROOT / "fixtures/planning/private/gdpevo-procurement-v3-control.json"
ANSWERS = ROOT / "fixtures/planning/private/gdpevo-procurement-v3-answers.json"
SESSION = EVAL / "procurement_eval/runs/planning-session-20261005/attempt-gdpevo-v4-full-20261005"
GROUPS = ("quotes", "packages", "kits", "revisions")
ARMS = ("fixed", "fewshot", "skills", "dynamic")
OUTCOMES = ("procurement", "source_selection", "freight_audit", "commitment_ledger", "approval_boundary", "erp_claim_conflicts")


def spec() -> dict[str, Any]:
    return {
        "total_cny": None,
        "per_attempt_cny": None,
        "enforce_cost_limit": False,
        "max_model_calls": 1,
        "max_output_tokens": 4096,
        "max_request_bytes": 500_000,
        "timeout_seconds": 180,
        "input_cny_per_million": 9.0,
        "output_cny_per_million": 27.0,
        "pricing_kind": "conservative estimate; not provider invoice",
        "pricing_source": "user revoked monetary ceilings; usage remains recorded",
    }


class UnlimitedCalls:
    """One isolated gateway per request; no legacy 2/50 monetary reservation."""

    def __init__(self) -> None:
        import json as _json

        self.env = load_env(path=Path("/mnt/c/dev/rush-harness/.env"))
        config = _json.loads((EVAL / "config.t46.json").read_text())
        self.model = ModelConfig.from_env({**self.env, "MODEL_ID": config["model_id"], "MODEL_TEMPERATURE": "0"})
        self.secrets = secret_values(self.env)

    def call(self, directory: Path, messages: list[dict[str, str]], *, label: str) -> tuple[dict | None, dict]:
        import httpx

        directory.mkdir(parents=True, exist_ok=False)
        payload = {"model": self.model.model_id, "messages": messages, "temperature": 0,
                   "response_format": {"type": "json_object"}, "stream": False, "max_tokens": 4096}
        write(directory / "request.json", payload)
        result: dict[str, Any] = {"label": label, "status": "started", "started_at": time.time(), "group": label}
        response = None
        with (directory / "model_calls.jsonl").open("x") as log:
            try:
                with gateway(self.model, spec(), log, {"thinking": {"type": "disabled"}}) as (url, budget):
                    with httpx.Client(timeout=185, trust_env=False) as client:
                        res = client.post(url + "/chat/completions", json=payload)
                    text = redact(res.text, self.secrets)
                    (directory / "response.txt").write_text(text, encoding="utf-8")
                    result["http_status"] = res.status_code
                    res.raise_for_status()
                    response = res.json()
                    result["status"] = "returned"
                    result["observed_model"] = response.get("model")
            except (httpx.HTTPError, OSError, ValueError, KeyError, IndexError) as exc:
                result["status"] = "environment_failed"
                result["errors"] = [type(exc).__name__, str(exc)[:300]]
            finally:
                result["metrics"] = budget.summary() if "budget" in locals() else {"model_calls": 0}
        result["finished_at"] = time.time()
        write(directory / "call-result.json", result)
        return response, result


def load_inputs() -> tuple[dict, dict, dict, dict, list[dict], list[dict]]:
    public = json.loads(PUBLIC.read_text())
    training = json.loads(TRAINING.read_text())
    control = json.loads(CONTROL.read_text())
    answers = json.loads(ANSWERS.read_text())
    tasks = public["tasks"]
    return public, training, control, answers, [t for t in tasks if t["split"] == "train"], [t for t in tasks if t["split"] == "test"]


def feedback(task: dict, decision: dict | None, grade: dict) -> dict:
    return {"success": bool(grade.get("business_success")),
            "failed_outcomes": [p for p in OUTCOMES if not grade.get("points", {}).get(f"{task['task_id']}-{p}", False)],
            "diagnostics": diagnose_submission(task, decision),
            "arithmetic_audit": arithmetic_audit(task, decision),
            "repair_order": ["hard_constraints", "source_selection", "allocation", "freight_cents", "approval_request"],
            "scope_note": "检查当前候选，不证明全局无解或最优；候选非法不等于整个目标不可行。",
            "feedback_kind": "public-constraint-diagnostic-no-gold"}


def actor_messages(public: dict, training: dict, task: dict, arm: str, *, skills: list[dict] | None = None,
                   examples: list[dict] | None = None, selected: list[dict] | None = None) -> list[dict[str, str]]:
    if arm not in ARMS and arm not in {"repair", "validation"}:
        raise ValueError(f"unknown arm {arm}")
    view = actor_view(public, training, task["task_id"], task["split"])
    system = "你是采购决策助手，只读公开资料，不调用工具、不下单。\n" + public["environment"]["public_contract"] + "\n" + FORMAT
    system += "\n必须先核对适用规则、来源版本、历史承诺、预算/运费、审批边界，再输出完整 JSON；解释与最终字段必须一致。"
    if skills:
        system += "\n当前聚焦技能（只作策略提示，服从当前任务输入）：\n" + "\n".join(
            f"[{s['skill_id']}] {s['description']}\n{s['body']}" for s in skills)
    if selected:
        system += "\n本回合 selector 选择的技能：\n" + "\n".join(
            f"[{s['skill_id']}] {s['description']}\n{s['body']}" for s in selected)
    if examples:
        system += "\n训练期正确示例（只来自其他 train 题，不能复制实例 ID；先抽象规则再处理当前题）：\n" + json.dumps(examples, ensure_ascii=False)
    return [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(view, ensure_ascii=False)}]


def parse_response(response: dict | None) -> dict | None:
    if response is None:
        return None
    content = response["choices"][0]["message"]["content"]
    value = json.loads(content)
    if not isinstance(value, dict):
        raise ValueError("decision must be object")
    return value


def call_actor(caller: UnlimitedCalls, directory: Path, public: dict, training: dict, task: dict, control: dict,
               arm: str, *, skills=None, examples=None, selected=None, messages=None) -> dict:
    messages = messages or actor_messages(public, training, task, arm, skills=skills, examples=examples, selected=selected)
    response, call = caller.call(directory, messages, label=arm)
    row: dict[str, Any] = {"task_id": task["task_id"], "group_id": task["group_id"], "split": task["split"],
                           "arm": arm, "status": "environment_failed" if response is None else "returned",
                           "metrics": call.get("metrics", {"input_tokens": 0, "output_tokens": 0}),
                           "decision": None, "grade": {"score": 0.0, "business_success": False, "points": {}}}
    try:
        value = parse_response(response)
        row["decision"] = value
        row["grade"] = grade_task(task, value, control["rubrics"][task["task_id"]])
        row["status"] = "scored"
        write(directory / "submission.json", value)
        row["feedback"] = feedback(task, value, row["grade"])
    except (ValueError, KeyError, TypeError, IndexError, json.JSONDecodeError) as exc:
        row["status"] = "format_failed" if response is not None else "environment_failed"
        row["errors"] = [type(exc).__name__, str(exc)[:300]]
        row["feedback"] = feedback(task, None, row["grade"])
    write(directory / "result.json", row)
    return row


def instance_tokens(value: Any) -> set[str]:
    text = json.dumps(value, ensure_ascii=False)
    tokens = set(re.findall(r"(?:quotes|packages|kits|revisions)-(?:train|test)-\d+", text))
    tokens.update(re.findall(r"\b(?:P|S|Q|K|R|A|B|C|D|E|F|M|V|X|U|O|I|H)\d{2,}\b", text))
    return tokens


def validate_skill(value: dict, forbidden: set[str]) -> dict:
    if set(value) != {"skill_id", "description", "body"}:
        raise ValueError("skill must contain exactly skill_id, description, body")
    if not all(isinstance(value[k], str) and value[k].strip() for k in value):
        raise ValueError("skill fields must be nonempty strings")
    if len(value["body"]) > 2200:
        raise ValueError("skill body too long")
    visible = value["description"] + "\n" + value["body"]
    if any(token in visible for token in forbidden) or re.search(r"\d+\.\d{2}", visible):
        raise ValueError("skill contains instance answer")
    return value


def group_curator_view(public: dict, training: dict, records: list[dict], group: str) -> dict:
    policies: dict[str, dict] = {}
    rows = []
    for record in records:
        task = record["task"]
        if task["group_id"] != group:
            continue
        evidence = training["tasks"][task["task_id"]]["policy_evidence"]
        for rule in evidence:
            policies[rule["rule_id"]] = {k: rule[k] for k in ("rule_id", "name", "condition", "policy", "fields", "stop")}
        rows.append({"task_id": task["task_id"], "request": task["request"], "input": task["input"],
                     "policy_rule_ids": [r["rule_id"] for r in evidence], "attempts": record["attempts"]})
    return {"group_id": group, "policies": list(policies.values()), "train_records": rows,
            "visibility": "public train input, output, diagnostics and arithmetic only; no gold/rubric/control"}


def curator_prompt(group: str, phase: str) -> str:
    return (f"你是 TRACE/GDPevo Curator，处理采购组 {group} 的 train 资料。当前阶段是 {phase}。"
            "只能使用输入中的公开任务、真实输出、失败字段、诊断和算术审计；不能猜测或输出私有答案。"
            "按可复用操作拆成最多四项聚焦技能，每项输出 id、description、body；body 要写触发条件、执行顺序、例外、停止适用条件和自检。"
            "不要复制任务ID、报价ID、供应商ID、订单ID、价格、数量或答案，不改变权限、工具或审批。"
            "只输出 JSON：{\"skills\":[{\"skill_id\":\"...\",\"description\":\"...\",\"body\":\"...\"}]}。")


def parse_skill_list(response: dict | None, forbidden: set[str]) -> list[dict]:
    if response is None:
        raise RuntimeError("Curator returned no response")
    value = json.loads(response["choices"][0]["message"]["content"])
    if set(value) != {"skills"} or not 1 <= len(value["skills"]) <= 4:
        raise ValueError("Curator skill list bounds invalid")
    result = []
    for skill in value["skills"]:
        normalized = {"skill_id": skill.get("skill_id"), "description": skill.get("description"), "body": skill.get("body")}
        result.append(validate_skill(normalized, forbidden))
    if len({s["skill_id"] for s in result}) != len(result):
        raise ValueError("duplicate skill id")
    return result


def reflect_prompt(group: str) -> str:
    return (f"你是 {group} 采购技能的 reflect-3 修订器。只看 train 公开轨迹和当前技能。"
            "逐项检查：技能是否覆盖失败诊断、是否把候选失败误判为全局无解、是否与其他技能重叠、是否有明确停止条件。"
            "保留有证据的内容，修正或拆分有问题的技能；不引用实例ID、价格、数量、答案、私有rubric。"
            "只输出 JSON {\"skills\":[{\"skill_id\":\"...\",\"description\":\"...\",\"body\":\"...\"}]}。")


def example_bank(answers: dict, train: list[dict], *, exclude: str | None = None) -> list[dict]:
    chosen = []
    for task in train:
        if task["task_id"] == exclude:
            continue
        answers_for = answers["tasks"].get(task["task_id"], [])
        if answers_for:
            chosen.append({"task_id": task["task_id"], "request": task["request"], "correct_decision": answers_for[0]})
        if len(chosen) == 4:
            break
    return chosen


def skill_for_task(skills: dict[str, list[dict]], task: dict) -> list[dict]:
    return skills.get(task["group_id"], [])


def write_manifest(output: Path) -> None:
    write(output / "manifest.json", {str(p.relative_to(output)): digest(p) for p in output.rglob("*") if p.is_file() and p.name != "manifest.json"})


def run(output: Path = SESSION, *, test_repeats: int = 3) -> dict:
    if output.exists():
        raise FileExistsError(output)
    if test_repeats < 3:
        raise ValueError("T64 requires at least three held-out repeats")
    public, training, control, answers, train, test = load_inputs()
    output.mkdir(parents=True)
    (output / "frozen").mkdir()
    for path in (PUBLIC, TRAINING, CONTROL, Path(__file__), ROOT / "scripts/planning/gdpevo_feedback.py", ROOT / "scripts/planning/gdpevo_expansion_judge.py"):
        (output / "frozen" / path.name).write_bytes(path.read_bytes())
    caller = UnlimitedCalls()
    protocol = {"kind": "full v3 GDPevo TRACE evolution", "model_id": caller.model.model_id, "temperature": 0,
                "thinking": "disabled", "train_count": len(train), "test_count": len(test), "test_repeats": test_repeats,
                "arms": ARMS, "curator_input": "train public input/output/diagnostics/arithmetic only", "test_feedback": False,
                "production_assignment_changed": False, "learning_gain_proven": False,
                "source_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "source_hashes": {p.name: digest(p) for p in (output / "frozen").iterdir()}}
    write(output / "protocol.json", protocol)
    forbidden = instance_tokens(public)
    records = []
    for task in train:
        folder = output / "train" / task["task_id"] / "attempt-1"
        row = call_actor(caller, folder, public, training, task, control, "fixed")
        attempts = [{"attempt": 1, "decision": row.get("decision"), "grade": row["grade"], "feedback": row["feedback"], "status": row["status"]}]
        if not row["grade"]["business_success"] and row["status"] == "scored":
            repair_messages = actor_messages(public, training, task, "repair") + [
                {"role": "assistant", "content": json.dumps(row["decision"], ensure_ascii=False)},
                {"role": "user", "content": "公开诊断如下。只修复诊断指出的问题，重新输出完整 JSON；不提供参考答案：" + json.dumps(row["feedback"], ensure_ascii=False)}]
            repair = call_actor(caller, output / "train" / task["task_id"] / "attempt-2", public, training, task, control, "repair", messages=repair_messages)
            attempts.append({"attempt": 2, "decision": repair.get("decision"), "grade": repair["grade"], "feedback": repair["feedback"], "status": repair["status"]})
        records.append({"task": {k: task[k] for k in ("task_id", "group_id", "split", "request", "input")}, "attempts": attempts})
        write(output / "training-records.json", records)
    write(output / "training-records.json", records)

    skills: dict[str, list[dict]] = {}
    curator_calls = []
    for group in GROUPS:
        material = group_curator_view(public, training, records, group)
        write(output / "curator-input" / f"{group}.json", material)
        response, call = caller.call(output / "curator" / group / "initial", [{"role": "system", "content": curator_prompt(group, "initial" )}, {"role": "user", "content": json.dumps(material, ensure_ascii=False)}], label=f"curator-{group}")
        curator_calls.append(call)
        current = parse_skill_list(response, forbidden)
        write(output / "curator" / group / "initial-skills.json", current)
        for round_id in range(1, 4):
            response, call = caller.call(output / "curator" / group / f"reflect-{round_id}", [{"role": "system", "content": reflect_prompt(group)}, {"role": "user", "content": json.dumps({"current_skills": current, "train": material}, ensure_ascii=False)}], label=f"reflect-{group}-{round_id}")
            curator_calls.append(call)
            current = parse_skill_list(response, forbidden)
            write(output / "curator" / group / f"reflect-{round_id}.json", current)
        skills[group] = current
    write(output / "skills.json", skills)

    validation = []
    for task in train:
        for arm in ("fixed", "fewshot", "skills"):
            examples = example_bank(answers, train, exclude=task["task_id"]) if arm == "fewshot" else None
            row = call_actor(caller, output / "validation" / arm / task["task_id"], public, training, task, control, arm,
                             skills=skill_for_task(skills, task) if arm == "skills" else None, examples=examples)
            validation.append(row)
        # dynamic selector is train-only and its selected skill is persisted before the actor call.
        selected = skill_for_task(skills, task)[:1]
        write(output / "validation" / "dynamic" / task["task_id"] / "selector.json", {"task_id": task["task_id"], "selected": [s["skill_id"] for s in selected], "source": "group-scoped selector"})
        validation.append(call_actor(caller, output / "validation" / "dynamic" / task["task_id"] / "actor", public, training, task, control, "dynamic", selected=selected))
        write(output / "validation.json", validation)
    write(output / "validation.json", validation)
    def scores(rows):
        return {arm: {"count": len([r for r in rows if r["arm"] == arm]), "mean_score": sum(r["grade"]["score"] for r in rows if r["arm"] == arm) / max(1, len([r for r in rows if r["arm"] == arm])), "business_success": sum(bool(r["grade"]["business_success"]) for r in rows if r["arm"] == arm)} for arm in ARMS}
    validation_summary = scores(validation)
    write(output / "validation-summary.json", validation_summary)

    test_rows: list[dict] = []
    def one_test(repeat: int, arm: str, task: dict) -> dict:
        examples = example_bank(answers, train) if arm == "fewshot" else None
        if arm == "dynamic":
            selected = skill_for_task(skills, task)[:1]
            write(output / "test" / f"repeat-{repeat}" / "dynamic" / task["task_id"] / "selector.json", {"task_id": task["task_id"], "selected": [s["skill_id"] for s in selected], "source": "group-scoped selector"})
            return call_actor(caller, output / "test" / f"repeat-{repeat}" / "dynamic" / task["task_id"] / "actor", public, training, task, control, arm, selected=selected)
        return call_actor(caller, output / "test" / f"repeat-{repeat}" / arm / task["task_id"], public, training, task, control, arm, skills=skill_for_task(skills, task) if arm == "skills" else None, examples=examples)
    for repeat in range(1, test_repeats + 1):
        jobs = [(arm, task) for arm in ARMS for task in test]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(one_test, repeat, arm, task) for arm, task in jobs]
            for future in futures:
                test_rows.append(future.result() | {"repeat": repeat})
        write(output / "test-results.json", test_rows)
    summary = {"validation": validation_summary, "test": scores(test_rows)}
    write(output / "report.json", {"summary": summary, "validation": validation, "test_rows": test_rows,
                                    "curator_calls": curator_calls, "claims": {"test_feedback": False, "production_assignment_changed": False,
                                    "learning_gain_proven": False, "heldout_repeats": test_repeats}})
    write(output / "bank.json", {"scope": "experiment-only", "production_assignment_changed": False, "skills": skills,
                                  "validation_summary": validation_summary})
    write_manifest(output)
    return summary


def verify(output: Path = SESSION) -> dict:
    manifest = json.loads((output / "manifest.json").read_text())
    for relative, expected in manifest.items():
        if digest(output / relative) != expected:
            raise ValueError("evidence hash mismatch: " + relative)
    protocol = json.loads((output / "protocol.json").read_text())
    report = json.loads((output / "report.json").read_text())
    if protocol["train_count"] != 20 or protocol["test_count"] != 20 or protocol["test_repeats"] < 3:
        raise ValueError("full v3 coverage/repeat protocol missing")
    rows = report["test_rows"]
    if len(rows) != 20 * len(ARMS) * protocol["test_repeats"]:
        raise ValueError("held-out arm/repeat matrix incomplete")
    if any(r["split"] != "test" or r["arm"] not in ARMS for r in rows):
        raise ValueError("test row boundary invalid")
    validation = report["validation"]
    if len(validation) != 20 * len(ARMS) or any(r["split"] != "train" for r in validation):
        raise ValueError("train validation matrix incomplete")
    if any(r.get("feedback", {}).get("feedback_kind") != "public-constraint-diagnostic-no-gold" for r in validation):
        raise ValueError("validation feedback boundary changed")
    return {"status": "passed", "summary": report["summary"], "claims": report["claims"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run", "verify"))
    parser.add_argument("--output", type=Path, default=SESSION)
    parser.add_argument("--test-repeats", type=int, default=3)
    args = parser.parse_args()
    result = run(args.output, test_repeats=args.test_repeats) if args.command == "run" else verify(args.output)
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0)
