import json
from copy import deepcopy

import pytest
from scripts.planning.gdpevo_feedback import arithmetic_audit, diagnose_submission, feedback_for
from scripts.planning.gdpevo_oracle import solve_task
from scripts.planning.gdpevo_refinement import actor_messages, curator_messages, parse_decision
from scripts.planning.gdpevo_taskset import load_taskset

from agent.evolution.refinement import select_candidate

pytestmark = pytest.mark.unit


def task(index):
    return deepcopy(load_taskset()[0]["tasks"][index])


def answer(t):
    return deepcopy(solve_task(t)["answers"][0])


def codes(t, value):
    return {i["code"] for i in diagnose_submission(t, value)}


def test_freight_boundary_is_diagnosed_without_replacing_submission():
    t = task(5)
    value = answer(t)
    assert not diagnose_submission(t, value)
    value["freight_cents"]["S001"] = 300
    original = deepcopy(value)
    assert "freight_mismatch" in codes(t, value)
    assert value == original


def test_budget_violation_cannot_be_excused_by_pending_approval():
    t = task(6)
    value = answer(t)
    value["allocation"].append({"quote_id": "t2-filter", "packs": 10})
    value["approval_request"]["lines"] = deepcopy(value["allocation"])
    assert {"budget_exceeded", "disposition_conflict"} <= codes(t, value)


def test_kit_partial_and_site_surplus_are_actionable():
    t = task(3)
    value = answer(t)
    value["allocation"] = [{"quote_id": "d-left", "packs": 2}]
    assert "kit_partial" in codes(t, value)
    value["allocation"].append({"quote_id": "d-right", "packs": 2})
    assert "site_surplus" in codes(t, value)


def test_approval_payload_must_follow_repaired_allocation():
    t = task(4)
    value = answer(t)
    value["approval_request"]["lines"] = [{"quote_id": "e-other", "packs": 7}]
    assert "approval_lines_mismatch" in codes(t, value)


def test_arithmetic_audit_exposes_bad_price_comparison_without_optimized_plan():
    t = task(0)
    audit = arithmetic_audit(t, None)
    rows = {r["quote_id"]: r for r in audit["quote_arithmetic"]}
    assert rows["a-final"]["goods_cost_bounds_cents"] == [1100]
    assert rows["a-pending"]["goods_cost_bounds_cents"] == [1200, 1800]
    assert "allocation" not in audit and "answer" not in audit


def test_learning_feedback_refuses_test_and_returns_concrete_diagnostics():
    t = task(4)
    value = answer(t)
    value["approval_request"]["revision"] = 99
    result = feedback_for(t, {"business_success": False, "points": {}}, value)
    assert any(i["code"] == "approval_binding" for i in result["diagnostics"])
    assert "expected" not in json.dumps(result)
    with pytest.raises(ValueError, match="train-only"):
        feedback_for(task(5), {"business_success": False}, {})


def test_valid_information_request_is_not_marked_required_shortage():
    t = task(0)
    value = answer(t)
    value["disposition"] = "needs_information"
    value["allocation"] = []
    value["freight_cents"] = {}
    value["approval_request"]["lines"] = []
    assert "required_shortage" not in codes(t, value)


def test_final_decision_is_extracted_after_analysis_without_interpreting_prose():
    value = answer(task(5))
    parsed = parse_decision(json.dumps({"analysis": "先算再提交", "decision": value}))
    assert parsed == value
    with pytest.raises(ValueError, match="before analysis"):
        parse_decision(json.dumps({"decision": value, "analysis": "晚来的修正"}))


def test_all_arms_share_output_protocol_and_only_memory_changes():
    public, _ = load_taskset()
    t = task(5)
    records = [{"task": task(i), "attempts": []} for i in range(5)]
    fixed = actor_messages(public, t, "fixed-v2")
    raw = actor_messages(public, t, "raw-v2", records)
    cur = actor_messages(public, t, "curated-v2", {"body": "先检查结构"})
    assert fixed[1] == raw[1] == cur[1]
    assert raw[0]["content"].startswith(fixed[0]["content"])
    assert cur[0]["content"].startswith(fixed[0]["content"])
    records[0]["task"] = t
    with pytest.raises(ValueError):
        actor_messages(public, t, "raw-v2", records)
    with pytest.raises(ValueError):
        curator_messages(records)


def trial(score, task_id="train-01", tokens=100):
    return {"task_id": task_id, "split": "train", "status": "scored",
            "grade": {"score": score, "business_success": score == 1},
            "metrics": {"input_tokens": tokens, "output_tokens": 50}}


def test_candidate_promoted_for_real_training_improvement():
    selection = select_candidate([trial(.4)], [trial(1)])
    assert selection["promote"] and selection["reason"] == "performance"


def test_aggregate_gain_cannot_hide_per_case_regression():
    before = [trial(1), trial(.2, "train-02")]
    after = [trial(.8), trial(1, "train-02")]
    selection = select_candidate(before, after)
    assert not selection["promote"] and selection["regressions"] == ["train-01"]


def test_t64_aggregate_policy_allows_local_tradeoff():
    before = [trial(1), trial(.2, "train-02")]
    after = [trial(.8), trial(1, "train-02")]
    selection = select_candidate(before, after, policy="aggregate-v2")
    assert selection["promote"] and selection["per_task_veto"] is False


def test_tied_verbosity_is_not_gain_but_measured_efficiency_can_be():
    assert not select_candidate([trial(1)], [trial(1)])["promote"]
    selection = select_candidate([trial(1, tokens=200)], [trial(1, tokens=100)])
    assert selection["promote"] and selection["reason"] == "efficiency"


def test_selection_never_uses_test_or_incomplete_trials():
    leaked = trial(1)
    leaked["split"] = "test"
    with pytest.raises(ValueError, match="test outcomes"):
        select_candidate([trial(.4)], [leaked])
    failed = trial(1)
    failed["status"] = "format_failed"
    assert not select_candidate([trial(.4)], [failed])["promote"]
