"""Build operation-level, train-only TRACE feedback from a completed T61 attempt.

This is a zero-model post-processing boundary.  It does not infer answers, rewrite
decisions, or expose the private control file to a future Curator.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.planning.gdpevo_calibration import digest, write

PRIVATE_MARKERS = ("rubrics", "answers", "manual_checks", "gold")
ATTEMPT = Path("/mnt/c/dev/rsi-eval/procurement_eval/runs/planning-session-20261005/attempt-v3-tool-diagnostic-20261005")


def _public_task(rows: list[dict], task_id: str) -> dict:
    row = next(r for r in rows if r["task_id"] == task_id)
    messages = row["messages"]
    return json.loads(messages[1]["content"])["task"]


def _operation(op: dict) -> dict:
    code = op.get("code", "")
    return {"operation_id": op.get("operation_id"), "status": op.get("status"),
            "base_version": op.get("base_version"), "version": op.get("version"),
            "read_names": op.get("read_names", []), "saved_names": op.get("saved_names", []),
            "code_sha256": hashlib.sha256(code.encode()).hexdigest() if code else None,
            "code": code}


def prepare(output: Path, *, attempt: Path = ATTEMPT) -> dict:
    if output.exists():
        raise FileExistsError(output)
    result = json.loads((attempt / "result.json").read_text())
    manifest = json.loads((attempt / "manifest.json").read_text())
    for relative, expected in manifest.items():
        if digest(attempt / relative) != expected:
            raise ValueError("historical evidence changed: " + relative)
    if result.get("status") != "completed" or len(result.get("rows", [])) != 4:
        raise ValueError("T61 attempt is not a complete four-row comparison")
    public = json.loads((attempt / "frozen/gdpevo-procurement-v3.json").read_text())
    tasks = {task["task_id"]: task for task in public["tasks"]}
    records: list[dict[str, Any]] = []
    for row in result["rows"]:
        task = _public_task(result["rows"], row["task_id"])
        if row["task_id"] not in tasks or tasks[row["task_id"]]["split"] != "train":
            raise ValueError("feedback requires train-only tasks")
        record: dict[str, Any] = {
            "task_id": row["task_id"], "group_id": tasks[row["task_id"]]["group_id"],
            "arm": row["arm"], "task": task, "decision": row["decision"],
            "grade": row["grade"], "feedback": {
                "failed_outcomes": [k for k, passed in row["grade"]["points"].items() if not passed],
                "business_success": row["grade"]["business_success"],
            },
        }
        if row["arm"] == "compute":
            trace = json.loads((attempt / f"{row['task_id']}-compute/compute-trace.json").read_text())
            operations = [_operation(op) for op in trace["executions"]]
            if not operations or any(op["status"] != "completed" for op in operations):
                raise ValueError("compute operation did not complete")
            actor_ops = operations[1:]  # first operation seeds public task state
            if not actor_ops or any("task" not in op["read_names"] for op in actor_ops):
                raise ValueError("compute Actor did not explicitly read persistent task")
            versions = [op["base_version"] for op in operations]
            if versions != list(range(len(versions))):
                raise ValueError("computation version chain is not contiguous")
            record["operations"] = operations
            record["feedback"].update({
                "persistent_task_loaded": True,
                "operation_count": len(actor_ops),
                "completed_operation_count": sum(op["status"] == "completed" for op in actor_ops),
            })
        else:
            if "operations" in record:
                raise ValueError("text arm must not contain tool operations")
            record["feedback"]["persistent_task_loaded"] = False
        records.append(record)
    payload = {
        "schema_version": 1, "kind": "trace-operation-feedback", "model_calls": 0,
        "production_assignment_changed": False, "learning_gain_proven": False,
        "source_attempt": str(attempt), "source_manifest_sha256": digest(attempt / "manifest.json"),
        "records": records,
        "curator_visibility": "public train task, Actor output, independent score fields and typed execution trace; no private rubric or answer",
    }
    serialized = json.dumps(payload, ensure_ascii=False)
    if any(marker in serialized.lower() for marker in PRIVATE_MARKERS):
        raise ValueError("private control marker leaked into Curator input")
    output.mkdir(parents=True)
    write(output / "curator-input.json", {"messages": [
        {"role": "system", "content": "你是操作级反馈Curator。只提炼可迁移检查，不记忆实例答案；输入仅含公开train轨迹。"},
        {"role": "user", "content": serialized},
    ]})
    write(output / "report.json", {k: payload[k] for k in (
        "schema_version", "kind", "model_calls", "production_assignment_changed",
        "learning_gain_proven", "source_attempt", "source_manifest_sha256", "curator_visibility")})
    write(output / "manifest.json", {p.name: digest(p) for p in output.iterdir() if p.is_file()})
    return payload


def verify(output: Path) -> dict:
    manifest = json.loads((output / "manifest.json").read_text())
    for relative, expected in manifest.items():
        if digest(output / relative) != expected:
            raise ValueError("feedback artifact hash mismatch: " + relative)
    report = json.loads((output / "report.json").read_text())
    if report["model_calls"] != 0 or report["learning_gain_proven"]:
        raise ValueError("feedback preparation must be zero-model and non-learning claim")
    return {"status": "passed", "model_calls": 0, "learning_gain_proven": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "verify"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.output) if args.command == "prepare" else verify(args.output)
    print(json.dumps(result, ensure_ascii=False))
