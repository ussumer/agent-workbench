from copy import deepcopy

import pytest
from scripts.planning.gdpevo_calibration import messages_for, normalized, score_response, summarize
from scripts.planning.gdpevo_oracle import solve_task
from scripts.planning.gdpevo_taskset import load_taskset

pytestmark = pytest.mark.unit


def test_base_messages_exclude_training_and_other_tasks():
    public, _ = load_taskset()
    task = public["tasks"][5]
    messages = messages_for(public, task, "base")
    assert len(messages) == 2
    assert "policy-train" not in str(messages)
    assert "pit_stop每个物料本轮盈余最多1件" not in str(messages)
    assert "test-02" not in str(messages)


def test_policy_control_exposes_training_policies_without_gold():
    public, _ = load_taskset()
    task = public["tasks"][5]
    messages = messages_for(public, task, "policy-visible")
    assert "pit_stop每个物料本轮盈余最多1件" in str(messages)
    assert "point_id" not in str(messages) and "t2-pad" not in str(messages)


def test_order_does_not_change_scoring():
    public, control = load_taskset()
    task = public["tasks"][6]
    expected = solve_task(task)["answers"][0]
    reverse = deepcopy(expected)
    reverse["allocation"].reverse()
    reverse["approval_request"]["lines"].reverse()
    assert normalized(reverse) == normalized(expected)


def test_bool_not_allowed_as_packs():
    public, _ = load_taskset()
    value = solve_task(public["tasks"][5])["answers"][0]
    value["allocation"][0]["packs"] = True
    with pytest.raises(ValueError):
        normalized(value)


def test_real_wrong_disposition_scores_as_failure():
    import json

    public, control = load_taskset()
    task = public["tasks"][7]
    value = solve_task(task)["answers"][0]
    value["disposition"] = "infeasible"
    response = {"choices": [{"message": {"content": json.dumps(value)}}]}
    grade, _ = score_response(task, control["rubrics"][task["task_id"]], response)
    assert grade["business_success"] is False and grade["score"] < 1


def test_failed_attempts_remain_in_denominator():
    rows = [
        {
            "group": "base",
            "grade": {"score": 1, "business_success": True},
            "metrics": {"model_calls": 1, "reserved_upper_cny": 0.1},
        },
        {
            "group": "base",
            "grade": {"score": 0, "business_success": False},
            "metrics": {"model_calls": 1, "reserved_upper_cny": 0.2},
        },
    ]
    result = summarize(rows)["base"]
    assert result["attempts"] == 2 and result["all_correct"] == 1 and result["mean_score"] == 0.5
