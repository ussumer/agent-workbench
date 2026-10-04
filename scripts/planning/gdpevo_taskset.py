"""Zero-model validator for the GDPevo-inspired procurement task group."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_PATH = ROOT / "fixtures/planning/gdpevo-procurement-v2.json"
CONTROL_PATH = ROOT / "fixtures/planning/gdpevo-procurement-v2-control.json"


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_taskset() -> tuple[dict[str, Any], dict[str, Any]]:
    public, control = _read(PUBLIC_PATH), _read(CONTROL_PATH)
    if public.get("task_group_id") != control.get("task_group_id"):
        raise ValueError("public/control task group mismatch")
    if public.get("schema_version") != 2 or control.get("schema_version") != 2:
        raise ValueError("unsupported task-set schema")
    return public, control


def _check_rules(public: dict[str, Any], control: dict[str, Any]) -> set[str]:
    rules = control.get("rules", [])
    ids = [rule.get("rule_id") for rule in rules]
    if len(ids) != len(set(ids)) or not ids:
        raise ValueError("rule IDs must be unique and non-empty")
    for rule in rules:
        for field in ("condition", "policy", "stop"):
            if not rule.get(field):
                raise ValueError(f"rule {rule.get('rule_id')} lacks {field}")
    if not public["environment"].get("public_contract"):
        raise ValueError("public contract is missing")
    return set(ids)


def _check_matrix(public: dict[str, Any], control: dict[str, Any], rule_ids: set[str]) -> tuple[dict[str, dict[str, Any]], set[str], set[str]]:
    tasks = public.get("tasks", [])
    by_id = {task.get("task_id"): task for task in tasks}
    matrix = control.get("matrix", {})
    if len(by_id) != len(tasks) or set(by_id) != set(matrix):
        raise ValueError("public tasks and control matrix differ")
    train = {task_id for task_id, task in by_id.items() if task.get("split") == "train"}
    test = {task_id for task_id, task in by_id.items() if task.get("split") == "test"}
    if (len(train), len(test)) != (5, 5) or train & test or train | test != set(by_id):
        raise ValueError("task group must contain exactly 5 train and 5 test tasks")
    train_combos: set[frozenset[str]] = set()
    trained_rules: set[str] = set()
    for task_id, row in matrix.items():
        subset = row.get("rule_ids", [])
        if not subset or len(subset) != len(set(subset)) or not set(subset) <= rule_ids:
            raise ValueError(f"invalid rule subset for {task_id}")
        if task_id in train:
            train_combos.add(frozenset(subset))
            trained_rules.update(subset)
    if trained_rules != rule_ids:
        raise ValueError("every rule must be exposed by training material")
    unseen = {frozenset(matrix[task_id]["rule_ids"]) for task_id in test} - train_combos
    if len(unseen) < 3:
        raise ValueError("at least three held-out rule combinations must be unseen")
    for task_id in train:
        materials = by_id[task_id].get("training_materials", [])
        if not materials:
            raise ValueError(f"training task {task_id} has no policy evidence")
        for rule_id in matrix[task_id]["rule_ids"]:
            if not any(item.get("document_id", "").endswith(f"-{rule_id}") and item.get("text") for item in materials):
                raise ValueError(f"training task {task_id} lacks evidence for rule {rule_id}")
    return matrix, train, test


def _check_rubrics(control: dict[str, Any], matrix: dict[str, dict[str, Any]], train: set[str], test: set[str]) -> dict[str, int]:
    rubrics = control.get("rubrics", {})
    if set(rubrics) != test:
        raise ValueError("rubrics must cover held-out tasks exactly")
    seen: set[str] = set()
    counts: dict[str, int] = {}
    for task_id in test:
        points = rubrics[task_id]
        if not 6 <= len(points) <= 10:
            raise ValueError(f"{task_id} must have 6-10 scoring points")
        outcomes: set[str] = set()
        for point in points:
            point_id, outcome = point.get("point_id"), point.get("outcome")
            if not point_id or point_id in seen or not outcome or outcome in outcomes:
                raise ValueError(f"duplicate rubric point/outcome in {task_id}")
            if point.get("weight") not in {1, 2, 3} or not point.get("field"):
                raise ValueError(f"invalid weighted point {point_id}")
            anchors = point.get("rule_anchors", {})
            flattened = {task for group in anchors.values() for task in group}
            if not flattened or not flattened <= train:
                raise ValueError(f"{point_id} lacks training anchor")
            if set(anchors) - set(matrix[task_id]["rule_ids"]):
                raise ValueError(f"{point_id} anchors rule outside test task")
            seen.add(point_id)
            outcomes.add(outcome)
        counts[task_id] = len(points)
    return counts


def _check_counterfactuals(public: dict[str, Any], control: dict[str, Any]) -> list[dict[str, Any]]:
    def at(value: Any, path: list[Any]) -> Any:
        for part in path:
            value = value[part]
        return value

    by_id = {task["task_id"]: task for task in public["tasks"]}
    results = []
    for item in control.get("counterfactuals", []):
        task_id = item.get("task_id")
        if task_id not in by_id or by_id[task_id].get("split") != "test":
            raise ValueError(f"counterfactual must target test task: {task_id}")
        changes = item.get("changes", [])
        if len(changes) != 1 or not item.get("group"):
            raise ValueError(f"{item.get('id')} must change one declared field group")
        path = changes[0].get("path", [])
        if not path or ("value" not in changes[0]):
            raise ValueError(f"{item.get('id')} has an invalid change path/value")
        before = at(by_id[task_id], path)
        after = changes[0]["value"]
        if before == after:
            raise ValueError(f"counterfactual {item.get('id')} does not change its field")
        results.append({"id": item["id"], "task_id": task_id, "field_group": item["group"], "path": path})
    if len(results) < 8:
        raise ValueError("at least eight single-factor counterfactuals are required")
    return results


def actor_view(public: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(public)


def validate_taskset() -> dict[str, Any]:
    public, control = load_taskset()
    rule_ids = _check_rules(public, control)
    matrix, train, test = _check_matrix(public, control, rule_ids)
    rubric_points = _check_rubrics(control, matrix, train, test)
    counterfactuals = _check_counterfactuals(public, control)
    encoded = json.dumps(actor_view(public), ensure_ascii=False, sort_keys=True).lower()
    if any(token in encoded for token in ('"expected"', '"rubrics"', "private_judge", "control.json")):
        raise ValueError("actor view contains control-plane material")
    train_combos = {frozenset(matrix[task_id]["rule_ids"]) for task_id in train}
    return {
        "schema_version": 2,
        "task_group_id": public["task_group_id"],
        "train_tasks": sorted(train),
        "test_tasks": sorted(test),
        "rule_count": len(rule_ids),
        "unseen_test_combinations": sum(frozenset(matrix[task_id]["rule_ids"]) not in train_combos for task_id in test),
        "rubric_points": dict(sorted(rubric_points.items())),
        "counterfactuals": counterfactuals,
        "public_sha256": _sha(PUBLIC_PATH),
        "control_sha256": _sha(CONTROL_PATH),
        "model_calls": 0,
        "claims": control["claims"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["validate"])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    payload = json.dumps(validate_taskset(), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        sys.stdout.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
