"""Zero-model business assertions for the GDPevo procurement task group."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest
from scripts.planning.gdpevo_oracle import solve_task
from scripts.planning.gdpevo_taskset import (
    CONTROL_PATH,
    PUBLIC_PATH,
    _check_counterfactuals,
    actor_view,
    load_taskset,
    validate_taskset,
)

pytestmark = pytest.mark.unit


def test_group_has_five_train_and_five_held_out_tasks():
    public, _ = load_taskset()
    assert [t["split"] for t in public["tasks"]].count("train") == 5
    assert [t["split"] for t in public["tasks"]].count("test") == 5
    assert len({t["task_id"] for t in public["tasks"]}) == 10


def test_rules_are_scoped_and_stop_conditions_are_explicit():
    _, control = load_taskset()
    assert len(control["rules"]) == 6
    for rule in control["rules"]:
        assert rule["condition"] and rule["policy"] and rule["stop"]
        assert rule["policy"] and rule["stop"]


def test_every_rule_has_a_training_anchor():
    public, control = load_taskset()
    train = {t["task_id"] for t in public["tasks"] if t["split"] == "train"}
    learned = {rule for task_id in train for rule in control["matrix"][task_id]["rule_ids"]}
    assert learned == {rule["rule_id"] for rule in control["rules"]}


def test_test_combinations_are_hybridized_not_copies_of_train():
    public, control = load_taskset()
    train = {t["task_id"] for t in public["tasks"] if t["split"] == "train"}
    train_combos = {frozenset(control["matrix"][task]["rule_ids"]) for task in train}
    test = {t["task_id"] for t in public["tasks"] if t["split"] == "test"}
    unseen = [task for task in test if frozenset(control["matrix"][task]["rule_ids"]) not in train_combos]
    assert len(unseen) >= 3


def test_test_rules_are_all_transferable_from_training():
    public, control = load_taskset()
    train_rules = {
        rule for task in public["tasks"] if task["split"] == "train"
        for rule in control["matrix"][task["task_id"]]["rule_ids"]
    }
    for task in public["tasks"]:
        if task["split"] == "test":
            assert set(control["matrix"][task["task_id"]]["rule_ids"]) <= train_rules


def test_each_test_rubric_has_six_to_ten_distinct_business_outcomes():
    public, control = load_taskset()
    test_ids = {t["task_id"] for t in public["tasks"] if t["split"] == "test"}
    assert set(control["rubrics"]) == test_ids
    for points in control["rubrics"].values():
        assert 6 <= len(points) <= 10
        assert len({point["outcome"] for point in points}) == len(points)
        assert all(point["weight"] in {1, 2, 3} for point in points)


def test_rubric_points_have_real_training_anchors():
    public, control = load_taskset()
    train = {t["task_id"] for t in public["tasks"] if t["split"] == "train"}
    for points in control["rubrics"].values():
        for point in points:
            anchors = {task for group in point["rule_anchors"].values() for task in group}
            assert anchors and anchors <= train


def test_counterfactuals_change_one_declared_group_and_operation():
    public, control = load_taskset()
    counterfactuals = _check_counterfactuals(public, control)
    assert len(counterfactuals) == 8
    assert len({item["field_group"] for item in counterfactuals}) >= 5
    assert all(item["path"] for item in counterfactuals)


def test_actor_view_excludes_private_rubric_and_expected_answers():
    public, control = load_taskset()
    encoded = json.dumps(actor_view(public), ensure_ascii=False).lower()
    private = json.dumps(control, ensure_ascii=False).lower()
    assert "rubrics" not in encoded
    assert '"expected"' not in encoded
    assert "private_judge" not in encoded
    assert "control.json" not in encoded
    assert "rubrics" in private and "point_id" in private


def test_validator_proves_structure_without_model_or_learning_claim():
    report = validate_taskset()
    assert report["model_calls"] == 0
    assert report["claims"]["calibrated"] is False
    assert report["claims"]["learning_gain_proven"] is False
    assert report["claims"]["production_adapter_ready"] is False
    assert report["public_sha256"] and report["control_sha256"]


def test_report_names_all_task_ids_and_rubric_counts():
    report = validate_taskset()
    assert report["train_tasks"] == ["train-01", "train-02", "train-03", "train-04", "train-05"]
    assert report["test_tasks"] == ["test-01", "test-02", "test-03", "test-04", "test-05"]
    assert set(report["rubric_points"].values()) == {6}
    assert report["unseen_test_combinations"] >= 3


def test_public_tasks_have_evidence_and_no_private_answer_keys():
    public, _ = load_taskset()
    for task in public["tasks"]:
        assert task["request"] and len(task["input"]["evidence_refs"]) >= 3
        encoded = json.dumps(task, ensure_ascii=False).lower()
        assert '"expected"' not in encoded
        assert '"rubric"' not in encoded
        for quote in task["input"]["quotes"]:
            assert quote["pack_size"] >= 1
            assert quote["status"] in {"signed", "pending", "draft", "revoked"}


def test_required_and_optional_demands_are_mixed_in_difficult_cases():
    public, _ = load_taskset()
    difficult = [task for task in public["tasks"] if task["split"] == "test"]
    assert all(any(d["required"] for d in task["input"]["demands"]) for task in difficult)
    assert any(any(not d["required"] for d in task["input"]["demands"]) for task in difficult)


def test_test_inputs_change_entities_or_state_beyond_rule_combination():
    public, _ = load_taskset()
    signatures = {
        json.dumps(task["input"], sort_keys=True, ensure_ascii=False)
        for task in public["tasks"] if task["split"] == "test"
    }
    assert len(signatures) == 5
    assert all(task["input"]["evidence_refs"] for task in public["tasks"])


def test_source_uncertainty_has_both_optional_and_required_scope_cases():
    public, _ = load_taskset()
    statuses = []
    for task in public["tasks"]:
        required_parts = {d["part_id"] for d in task["input"]["demands"] if d["required"]}
        for quote in task["input"]["quotes"]:
            if quote["status"] == "pending":
                statuses.append(quote["part_id"] in required_parts)
    assert True in statuses and False in statuses


def test_commitments_are_present_in_training_and_test_but_not_every_task():
    public, _ = load_taskset()
    has_commitment = [bool(task["input"]["commitments"]) for task in public["tasks"]]
    assert any(has_commitment) and not all(has_commitment)
    assert sum(has_commitment) >= 2


def test_fixture_files_are_distinct_control_plane_artifacts():
    assert PUBLIC_PATH != CONTROL_PATH
    assert PUBLIC_PATH.read_bytes() != CONTROL_PATH.read_bytes()


def test_private_oracle_respects_formal_quote_revision_and_erp_scope():
    public, _ = load_taskset()
    train = next(task for task in public["tasks"] if task["task_id"] == "train-01")
    result = solve_task(train)
    assert result["disposition"] == "execute"
    assert result["answers"][0]["allocation"] == [{"quote_id": "a-final", "packs": 10}]


def test_private_oracle_exposes_whole_pack_and_supplier_freight_trap():
    public, _ = load_taskset()
    task = next(task for task in public["tasks"] if task["task_id"] == "test-02")
    result = solve_task(task)
    assert result["objective"] == ((56,), -128200)
    assert result["answers"][0]["freight_cents"] == {"S002": 800}
    assert result["answers"][0]["approval_request"]["reuse_prior_approval"] is False


def test_private_oracle_distinguishes_decision_relevant_and_dominated_pending_quotes():
    public, _ = load_taskset()
    relevant = next(task for task in public["tasks"] if task["task_id"] == "test-03")
    dominated = next(task for task in public["tasks"] if task["task_id"] == "test-04")
    assert solve_task(relevant)["disposition"] == "needs_information"
    assert solve_task(dominated)["disposition"] == "execute"


def test_private_oracle_keeps_committed_cost_and_line_out_of_new_allocation():
    public, _ = load_taskset()
    task = next(task for task in public["tasks"] if task["task_id"] == "test-05")
    result = solve_task(task)
    assert result["answers"][0]["commitment_ledger"][0]["order_id"] == "O-T5"
    assert all(line["quote_id"] != "t5-chain" for line in result["answers"][0]["allocation"])


def test_private_oracle_has_non_greedy_optional_coverage():
    public, _ = load_taskset()
    task = next(task for task in public["tasks"] if task["task_id"] == "test-02")
    result = solve_task(task)
    selected = {line["quote_id"] for line in result["answers"][0]["allocation"]}
    assert selected == {"t2-pad", "t2-plug"}
    assert "t2-filter" not in selected


def test_every_declared_counterfactual_changes_private_oracle_result():
    public, control = load_taskset()
    tasks = {task["task_id"]: task for task in public["tasks"]}
    for item in control["counterfactuals"]:
        changed = deepcopy(tasks[item["task_id"]])
        change = item["changes"][0]
        target = changed
        for part in change["path"][:-1]:
            target = target[part]
        target[change["path"][-1]] = change["value"]
        assert solve_task(tasks[item["task_id"]])["answers"] != solve_task(changed)["answers"], item["id"]
