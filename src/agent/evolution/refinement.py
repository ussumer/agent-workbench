"""Select a text-skill candidate using matched training trials only."""
from __future__ import annotations

from typing import Any


def select_candidate(baseline: list[dict[str, Any]], candidate: list[dict[str, Any]],
                     *, policy: str = "aggregate-v2") -> dict[str, Any]:
    # per-task-v1 is only for replaying immutable historical experiment decisions.
    if policy not in {"aggregate-v2", "per-task-v1"}:
        raise ValueError("unknown selection policy")
    if not baseline or len(baseline) != len(candidate):
        raise ValueError("matched non-empty training trials required")
    before = {(r["task_id"], r.get("repeat", 1)): r for r in baseline}
    after = {(r["task_id"], r.get("repeat", 1)): r for r in candidate}
    if len(before) != len(baseline) or set(before) != set(after):
        raise ValueError("duplicate or mismatched training tasks")
    repeats = {repeat for _, repeat in before}
    if any(type(repeat) is not int or repeat < 1 for repeat in repeats):
        raise ValueError("invalid repeat")
    if any({r for t, r in before if t == task} != repeats for task, _ in before):
        raise ValueError("incomplete training repeat matrix")
    if any(r.get("split") != "train" for r in baseline + candidate):
        raise ValueError("candidate selection cannot use test outcomes")
    if any(r.get("status") != "scored" for r in baseline + candidate):
        return {"promote": False, "reason": "incomplete_or_failed_trial"}
    from math import isfinite

    for row in baseline + candidate:
        grade, metrics = row["grade"], row["metrics"]
        score = grade["score"]
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not isfinite(score) or not 0 <= score <= 1:
            raise ValueError("invalid measured score")
        if type(grade["business_success"]) is not bool:
            raise ValueError("invalid success label")
        if any(type(metrics[k]) is not int or metrics[k] < 0 for k in ("input_tokens", "output_tokens")):
            raise ValueError("invalid token measurement")
    regressions = list(dict.fromkeys(t[0] for t in before
                                    if after[t]["grade"]["score"] < before[t]["grade"]["score"]))
    before_score = sum(r["grade"]["score"] for r in baseline) / len(baseline)
    after_score = sum(r["grade"]["score"] for r in candidate) / len(candidate)
    before_success = sum(r["grade"]["business_success"] for r in baseline)
    after_success = sum(r["grade"]["business_success"] for r in candidate)
    before_tokens = sum(r["metrics"]["input_tokens"] + r["metrics"]["output_tokens"] for r in baseline)
    after_tokens = sum(r["metrics"]["input_tokens"] + r["metrics"]["output_tokens"] for r in candidate)
    performance_gain = after_score > before_score or after_success > before_success
    efficiency_gain = before_tokens > 0 and after_tokens <= before_tokens * 0.9
    aggregate_regression = after_score < before_score or after_success < before_success
    regression = bool(regressions) if policy == "per-task-v1" else aggregate_regression
    promote = not regression and after_success >= before_success and (performance_gain or efficiency_gain)
    result = {"promote": promote, "reason": "performance" if promote and performance_gain else
            "efficiency" if promote else "regression" if regression else "no_measured_gain",
            "regressions": regressions, "baseline_mean": before_score, "candidate_mean": after_score,
            "baseline_success": before_success, "candidate_success": after_success,
            "baseline_tokens": before_tokens, "candidate_tokens": after_tokens,
            "selection_split": "train", "test_seen": False}
    if policy == "aggregate-v2":
        result.update(policy=policy, trial_count=len(before), repeat_count=len(repeats),
                      per_task_veto=False)
    return result
