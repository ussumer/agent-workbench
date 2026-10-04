"""Zero-model proof that the public procurement environment has learning space."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from scripts.planning.judge import grade
from scripts.planning.learning_space import (
    CASE_ATOMS,
    COUNTERFACTUALS,
    RULES,
    _cases,
    _check_matrix,
    _problem,
    build_report,
    counterfactual_problems,
    sanitized_feedback,
)

from agent.planning.models import Demand, Offer, Plan, PlanLine, PlanningProblem

pytestmark = pytest.mark.unit


def test_rule_atoms_have_public_observable_evidence():
    assert len(RULES) == 4
    assert all(rule.rule_id and rule.condition and rule.operation and rule.evidence for rule in RULES)
    assert len({rule.rule_id for rule in RULES}) == len(RULES)


def test_all_rules_have_atomic_training_anchor():
    cases = _cases()
    anchored = {atom for case in cases if len(case.atoms) == 1 for atom in case.atoms}
    assert anchored == {rule.rule_id for rule in RULES}


def test_test_cases_contain_unseen_combinations():
    cases = _cases()
    train = {frozenset(case.atoms) for case in cases if len(case.atoms) <= 1 and case.case_id != "sufficient-budget"}
    combinations = [case for case in cases if len(case.atoms) >= 2]
    assert combinations and all(frozenset(case.atoms) not in train for case in combinations)


def test_matrix_has_distinct_operation_labels_and_control_case():
    cases = _cases()
    assert len({case.expected_operation for case in cases}) >= 6
    assert next(case for case in cases if case.case_id == "sufficient-budget").expected_operation == "do_not_ask_unnecessary"


@pytest.mark.parametrize("pair", COUNTERFACTUALS)
def test_counterfactuals_change_the_required_operation(pair):
    assert pair.left_operation != pair.right_operation
    assert pair.changed
    assert pair.left in CASE_ATOMS and pair.right in CASE_ATOMS


def test_counterfactuals_cover_positive_and_negative_ask_retry_reject():
    operations = {pair.left_operation for pair in COUNTERFACTUALS} | {pair.right_operation for pair in COUNTERFACTUALS}
    assert "clarify_missing_source" in operations
    assert "do_not_ask_unnecessary" in operations
    assert "reject_after_infeasibility" in operations
    assert "recompute_affected_allocation" in operations


def test_independent_environment_distinguishes_missing_from_infeasible():
    missing = build_report()["oracle_status"]["source-missing"]["status"]
    infeasible = build_report()["oracle_status"]["business-infeasible"]["status"]
    assert missing == "unresolved"
    assert infeasible == "infeasible"


def test_report_is_rebuildable_and_hashes_inputs():
    report = build_report()
    assert report["report_sha256"]
    assert set(report["source_hashes"]) == {
        "fixtures/planning/decision-cases-v1.json", "fixtures/planning/goal-v1.json", "fixtures/seed-v1.json"
    }
    assert json.loads(json.dumps(report)) == report


def test_feedback_is_whitelisted_and_has_no_answer_fields():
    problem = _problem("sufficient-budget")
    plan = Plan(goal_id=problem.goal_id, revision=problem.revision, lines=())
    feedback = sanitized_feedback("sufficient-budget", plan)
    assert set(feedback) == {"schema_version", "success", "constraint_status", "environment_status",
                             "error_categories", "rule_ids", "evidence_refs"}
    encoded = json.dumps(feedback, ensure_ascii=False).lower()
    for forbidden in ("offer_id", "order_id", "quantity", "unit_price", "objective", "best"):
        assert '"' + forbidden + '":' not in encoded


def test_feedback_preserves_constraint_and_environment_categories():
    problem = _problem("sufficient-budget")
    plan = Plan(goal_id=problem.goal_id, revision=problem.revision, lines=())
    feedback = sanitized_feedback("sufficient-budget", plan)
    assert feedback["constraint_status"] in {"feasible", "infeasible"}
    assert feedback["environment_status"] in {"feasible", "infeasible", "unresolved"}
    assert feedback["rule_ids"] == []


def test_legal_alternative_plans_are_both_accepted():
    demand = Demand(part_id="A", quantity=2, max_lead_days=3, required=False, allow_partial=True)
    offers = tuple(Offer(offer_id=f"{supplier}:A", part_id="A", supplier_id=supplier,
        supplier_active=True, part_active=True, relationship_active=True, unit_price="1.00", lead_days=2,
        source_ref=f"test:{supplier}", source_status="verified") for supplier in ("X", "Y"))
    problem = PlanningProblem(goal_id="alternatives", budget="5.00", demands=(demand,), offers=offers)
    plans = [Plan(goal_id="alternatives", revision=1, lines=(PlanLine(offer_id="X:A", quantity=2),)),
             Plan(goal_id="alternatives", revision=1, lines=(PlanLine(offer_id="Y:A", quantity=2),))]
    grades = [grade(problem, plan) for plan in plans]
    assert all(item.accepted for item in grades)


def test_invalid_rule_or_missing_case_cannot_be_silently_added():
    cases = _cases()
    assert {case.case_id for case in cases} == set(CASE_ATOMS)
    assert all(case.source.startswith("fixtures/planning/decision-cases-v1.json#") for case in cases)
    with pytest.raises(ValueError, match="unknown rule"):
        _check_matrix([replace(cases[0], atoms=("R-UNKNOWN",)), *cases[1:]])


def test_no_model_calls_or_curator_claim_in_report():
    report = build_report()
    assert report["model_calls"] == 0
    assert report["claims"] == {"proves_condition_sensitivity": True, "proves_actor_learning_space": False,
                                "proves_learning_gain": False, "curator_run": False}


def test_case_problems_keep_public_budget_deadline_and_source_variation():
    baseline = _problem("sufficient-budget")
    changed = _problem("deadline-budget-missing")
    assert baseline.budget != changed.budget
    assert next(d for d in baseline.demands if d.part_id == "P001").max_lead_days != next(d for d in changed.demands if d.part_id == "P001").max_lead_days
    assert any(o.source_status == "missing" for o in changed.offers)


@pytest.mark.parametrize("pair", COUNTERFACTUALS)
def test_counterfactual_changes_only_declared_condition_group(pair):
    first, second = counterfactual_problems(pair)
    left, right = first.model_dump(mode="json"), second.model_dump(mode="json")
    changed = {key for key in left if left[key] != right[key]}
    expected = {"budget": {"budget"}, "deadline": {"demands"},
                "allow_partial": {"demands"}, "source_status": {"offers"}}[pair.changed]
    assert changed == expected
    if pair.changed in {"deadline", "allow_partial"}:
        allowed = "max_lead_days" if pair.changed == "deadline" else "allow_partial"
        assert all({key for key in a if a[key] != b[key]} <= {allowed}
                   for a, b in zip(left["demands"], right["demands"], strict=True))


def test_counterfactuals_change_independent_status_or_objective():
    report = build_report()
    assert len(report["counterfactuals"]) == len(COUNTERFACTUALS)
    for pair in report["counterfactuals"]:
        before, after = pair["left_oracle"], pair["right_oracle"]
        assert before["status"] != after["status"] or before["best"] != after["best"]


def test_feedback_distinguishes_feasible_suboptimal_from_constraint_failure():
    problem = _problem("deadline-budget")
    plan = Plan(goal_id=problem.goal_id, revision=problem.revision, lines=(
        PlanLine(offer_id="S001:P001", quantity=42), PlanLine(offer_id="S001:P003", quantity=15)))
    feedback = sanitized_feedback("deadline-budget", plan)
    assert feedback["constraint_status"] == "feasible" and feedback["success"] is False
    assert feedback["error_categories"] == ["SUBOPTIMAL"]
    empty = sanitized_feedback("deadline-budget", Plan(goal_id=problem.goal_id, revision=1, lines=()))
    assert empty["constraint_status"] == "infeasible" and "QUANTITY" in empty["error_categories"]


def test_feedback_accepts_optimal_candidate_without_returning_its_values():
    problem = _problem("deadline-budget")
    plan = Plan(goal_id=problem.goal_id, revision=1, lines=(
        PlanLine(offer_id="S001:P001", quantity=42), PlanLine(offer_id="S001:P003", quantity=15),
        PlanLine(offer_id="S002:P004", quantity=6)))
    feedback = sanitized_feedback("deadline-budget", plan)
    assert feedback["success"] is True and feedback["error_categories"] == []
    serialized = json.dumps(feedback)
    assert "S001" not in serialized and "S002" not in serialized and "2200" not in serialized


def test_missing_source_feedback_never_labels_environment_as_business_infeasible():
    problem = _problem("source-missing")
    feedback = sanitized_feedback("source-missing", Plan(goal_id=problem.goal_id, revision=1, lines=()))
    assert feedback["environment_status"] == "unresolved"
    assert "SOURCE_UNRESOLVED" in feedback["error_categories"]
