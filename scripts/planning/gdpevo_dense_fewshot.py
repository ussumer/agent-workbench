"""Train-only dense few-shot ablation for the v3 procurement set.

This is an experiment arm, not a production skill installation.  It uses all five correct
train examples from the current business group, excluding the task itself during train
validation.  Held-out tasks receive train examples only.  No test answer or test feedback is
ever placed in the prompt; every response is scored by the independent private judge.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
EVAL = Path("/mnt/c/dev/rsi-eval")
for folder in (ROOT, ROOT / "src", EVAL):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from scripts.planning.gdpevo_calibration import digest, write  # noqa: E402
from scripts.planning.gdpevo_expansion_judge import grade_task  # noqa: E402
from scripts.planning.gdpevo_v4_training import (  # noqa: E402
    UnlimitedCalls,
    actor_messages,
    fresh_call_directory,
    load_inputs,
    parse_response,
)


def examples_for(task: dict, train: list[dict], answers: dict, *, exclude_self: bool) -> list[dict]:
    """Return only same-group train examples, never a held-out answer."""
    result = []
    for candidate in train:
        if exclude_self and candidate["task_id"] == task["task_id"]:
            continue
        answer = answers["tasks"].get(candidate["task_id"], [])
        if not answer:
            continue
        result.append({"task_id": candidate["task_id"], "request": candidate["request"],
                       "correct_decision": answer[0]})
    return result


def run_one(caller: UnlimitedCalls, output: Path, public: dict, training: dict,
            control: dict, task: dict, examples: list[dict], label: str) -> dict:
    directory = fresh_call_directory(output)
    started = time.time()
    response, call = caller.call(directory, actor_messages(public, training, task, "fewshot",
                                                            examples=examples), label=label)
    row: dict[str, Any] = {"task_id": task["task_id"], "group_id": task["group_id"],
                           "split": task["split"], "arm": "dense-fewshot",
                           "status": "environment_failed" if response is None else "returned",
                           "metrics": call.get("metrics", {}), "examples_count": len(examples)}
    try:
        decision = parse_response(response)
        row["decision"] = decision
        row["grade"] = grade_task(task, decision, control["rubrics"][task["task_id"]])
        row["status"] = "scored"
        write(directory / "submission.json", decision)
    except Exception as exc:  # preserve format/environment failures in the denominator
        row["status"] = "format_failed" if response is not None else "environment_failed"
        row["error"] = {"type": type(exc).__name__, "message": str(exc)[:500]}
        row["grade"] = {"score": 0.0, "business_success": False, "points": {}}
    row["elapsed_seconds"] = round(time.time() - started, 3)
    write(directory / "result.json", row)
    return row


def summary(rows: list[dict]) -> dict[str, Any]:
    scored = [row for row in rows if row["status"] == "scored"]
    return {"rows": len(rows), "scored": len(scored),
            "mean_score": sum(row["grade"]["score"] for row in scored) / max(1, len(scored)),
            "business_success": sum(bool(row["grade"]["business_success"]) for row in scored),
            "denominator_mean": sum(row["grade"]["score"] for row in scored) / max(1, len(rows))}


def run(output: Path, repeats: int) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(output)
    public, training, control, answers, train, test = load_inputs()
    output.mkdir(parents=True)
    caller = UnlimitedCalls()
    protocol = {"kind": "dense train-only few-shot ablation", "source_revision": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "train_count": len(train), "test_count": len(test), "test_repeats": repeats,
        "examples": "all same-group train examples; validation excludes current task",
        "test_answers_sent": False, "test_feedback": False, "production_assignment_changed": False,
        "learning_gain_proven": False}
    write(output / "protocol.json", protocol)
    rows: list[dict] = []
    for task in train:
        rows.append(run_one(caller, output / "validation" / task["task_id"], public, training,
                            control, task, examples_for(task, train, answers, exclude_self=True),
                            "dense-fewshot-validation"))
    write(output / "validation.json", rows)
    for repeat in range(1, repeats + 1):
        test_rows = []
        for task in test:
            test_rows.append(run_one(caller, output / "test" / f"repeat-{repeat}" / task["task_id"],
                                     public, training, control, task,
                                     examples_for(task, train, answers, exclude_self=False),
                                     "dense-fewshot-test"))
        write(output / f"test-{repeat}.json", test_rows)
    validation = json.loads((output / "validation.json").read_text())
    tests = [json.loads((output / f"test-{repeat}.json").read_text()) for repeat in range(1, repeats + 1)]
    report = {"validation": summary(validation),
              "test": {str(i + 1): summary(rows) for i, rows in enumerate(tests)},
              "claims": {"test_feedback": False, "production_assignment_changed": False,
                         "learning_gain_proven": False}}
    write(output / "report.json", report)
    write(output / "manifest.json", {str(p.relative_to(output)): digest(p)
                                      for p in output.rglob("*") if p.is_file() and p.name != "manifest.json"})
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    print(json.dumps(run(args.output, args.repeats), ensure_ascii=False))
