"""Group-scoped TRACE-style Curator and ablation on frozen T57 trajectories."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
EVAL = Path("/mnt/c/dev/rsi-eval")
for folder in (ROOT, ROOT / "src", EVAL):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from agent.evolution.curator import validate_curated  # noqa: E402
from agent.evolution.refinement import select_candidate  # noqa: E402
from scripts.planning.gdpevo_calibration import digest, write  # noqa: E402
from scripts.planning.gdpevo_expansion import actor_view  # noqa: E402
from scripts.planning.gdpevo_expansion_judge import grade_task  # noqa: E402
from scripts.planning.gdpevo_refinement import RealCalls  # noqa: E402
from scripts.planning.gdpevo_v3_closed_loop import build_curator_records  # noqa: E402
from scripts.planning.gdpevo_v3_training import actor_messages, call_one, summarize  # noqa: E402

BASE = EVAL / "procurement_eval/runs/planning-session-20261004/attempt-gdpevo-v3-closed-loop-20261004"
SESSION = EVAL / "procurement_eval/runs/planning-session-20261005/attempt-gdpevo-v3-scoped-20261005"
GROUPS = ("quotes", "packages", "kits", "revisions")


def scoped_curator_input(training: dict[str, Any], records: list[dict[str, Any]], group: str,
                         *, version: int = 2) -> dict[str, Any]:
    """v2 preserves original tasks/diagnostics; v1 replays frozen T58 evidence."""
    if type(version) is not int or version not in (1, 2):
        raise ValueError('unsupported Curator input version')
    if group not in GROUPS:
        raise ValueError('unknown business group')
    joined = build_curator_records(training, records)
    selected = [row for row in joined if row["group_id"] == group]
    if len(selected) != 5:
        raise ValueError(f"{group}: Curator requires five train records")
    rules: dict[str, dict[str, Any]] = {}
    compact: list[dict[str, Any]] = []
    for row in selected:
        rule_ids = []
        for rule in row["policy_evidence"]:
            rule_ids.append(rule["rule_id"])
            rules[rule["rule_id"]] = {k: rule[k] for k in ("rule_id", "name", "condition", "policy", "fields", "stop")}
        entry = {"task_id": row["task_id"], "rule_ids": rule_ids, "attempts": [
            {"decision": a["decision"], "grade": a["grade"],
             "failed_outcomes": a["feedback"].get("failed_outcomes", [])}
            for a in row["attempts"]
        ]}
        if version == 2:
            entry['actor_task'] = row['actor_task']
            for original, attempt in zip(row['attempts'], entry['attempts'], strict=True):
                attempt['feedback'] = deepcopy(original['feedback'])
        compact.append(entry)
    material = {"group_id": group, "policies": list(rules.values()), "train_records": compact}
    if version == 2:
        material['schema_version'] = 2
    return material


def _curator_prompt(group: str) -> str:
    return (
        f"你是GDPevo Curator，只处理采购业务组 {group}。输入只有该组五道train的公开政策、真实尝试和失败字段；"
        "不看test、不看私有答案。按同一业务操作归纳条件、步骤、例外和停止条件，不能记忆实例ID、报价ID、供应商ID、价格、数量或答案。"
        "只输出JSON三个字段：skill_id、description、body。skill_id必须是 "
        f"planning_{group}_strategy，body只写该组规则且不超过1600字，不改变权限、工具或审批。"
    )


def run() -> None:
    from agent.env_utils import load_env
    for key, value in load_env(path=Path("/mnt/c/dev/rush-harness/.env")).items():
        os.environ.setdefault(key, value)
    if SESSION.exists():
        raise FileExistsError(SESSION)
    public = json.loads((BASE / "frozen/gdpevo-procurement-v3.json").read_text())
    training = json.loads((BASE / "frozen/gdpevo-procurement-v3-training.json").read_text())
    control = json.loads((BASE / "frozen/gdpevo-procurement-v3-control.json").read_text())
    records = json.loads((BASE / "training-records.json").read_text())
    tasks = public["tasks"]
    train = [t for t in tasks if t["split"] == "train"]
    test = [t for t in tasks if t["split"] == "test"]
    SESSION.mkdir(parents=True)
    shutil.copytree(BASE / "frozen", SESSION / "frozen")
    (SESSION / "frozen" / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    shutil.copy2(BASE / "training-records.json", SESSION / "training-records.json")
    caller = RealCalls()
    skills: dict[str, dict[str, Any]] = {}
    for group in GROUPS:
        material = scoped_curator_input(training, records, group)
        write(SESSION / f"curator-input-{group}.json", material)
        response, row = caller.call(SESSION / "curator" / group, [
            {"role": "system", "content": _curator_prompt(group)},
            {"role": "user", "content": json.dumps(material, ensure_ascii=False)},
        ])
        if response is None:
            raise RuntimeError(f"Curator failed for {group}; evidence is preserved")
        skill = validate_curated(response["choices"][0]["message"]["content"]).model_dump(mode="json")
        if skill["skill_id"] != f"planning_{group}_strategy":
            raise ValueError(f"Curator scope mismatch for {group}")
        skills[group] = skill
        write(SESSION / "curator" / group / "skill.json", skill)
    write(SESSION / "skills.json", skills)

    base_report = json.loads((BASE / "report.json").read_text())
    fixed_train = [r for r in json.loads((BASE / "validation.json").read_text()) if r["group"] == "fixed-train"]
    if len(fixed_train) != 20:
        raise ValueError("fixed train control incomplete")
    write(SESSION / "fixed-validation.json", fixed_train)
    candidate_train = []
    for task in train:
        row, _ = call_one(caller, SESSION / "validation" / task["task_id"],
                          actor_messages(public, training, task, "curated-v3", skills[task["group_id"]]),
                          task, control["rubrics"][task["task_id"]], "scoped-train")
        candidate_train.append(row)
        write(SESSION / "validation.json", candidate_train)
    selection = select_candidate(fixed_train, candidate_train)
    write(SESSION / "selection.json", selection)

    fixed_test = [r for r in base_report["rows"] if r["group"] == "fixed-closed-test"]
    rows = list(fixed_test)
    for task in test:
        row, _ = call_one(caller, SESSION / "test" / task["task_id"],
                          actor_messages(public, training, task, "curated-v3", skills[task["group_id"]]),
                          task, control["rubrics"][task["task_id"]], "scoped-test")
        rows.append(row)
        write(SESSION / "report.json", {"groups": summarize(rows), "rows": rows,
            "selection": selection, "claims": {"production_actor_run": False,
            "test_feedback": False, "selection_split": "train", "production_assignment_changed": False}})
    write(SESSION / "bank.json", {"scope": "experiment-only", "production_assignment_changed": False,
        "candidate_promoted": selection["promote"], "selected_arm": "scoped" if selection["promote"] else "fixed",
        "selection": selection, "skills": skills,
        "skill_hashes": {g: hashlib.sha256(s["body"].encode()).hexdigest() for g, s in skills.items()}})
    write(SESSION / "protocol.json", {"kind": "GDPevo v3 scoped skill bank", "model_id": caller.model.model_id,
        "groups": GROUPS, "selection_split": "train", "test_feedback": False,
        "production_assignment_changed": False, "source": str(BASE),
        "source_manifest_sha256": digest(BASE / "manifest.json")})
    write(SESSION / "manifest.json", {str(p.relative_to(SESSION)): digest(p)
        for p in SESSION.rglob("*") if p.is_file() and p.name != "manifest.json"})
    print(json.dumps({"selection": selection, "groups": summarize(rows)}, ensure_ascii=False))


def verify() -> None:
    manifest = json.loads((SESSION / "manifest.json").read_text())
    for relative, expected in manifest.items():
        if digest(SESSION / relative) != expected:
            raise ValueError("evidence hash mismatch: " + relative)
    report = json.loads((SESSION / "report.json").read_text())
    if len(report["rows"]) != 40:
        raise ValueError("fixed/scoped test arms incomplete")
    skills = json.loads((SESSION / "skills.json").read_text())
    if set(skills) != set(GROUPS) or any(s["skill_id"] != f"planning_{g}_strategy" for g, s in skills.items()):
        raise ValueError("scoped skill bank mismatch")
    print(json.dumps({"groups": report["groups"], "selection": report["selection"]}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run", "verify"))
    args = parser.parse_args()
    run() if args.command == "run" else verify()
