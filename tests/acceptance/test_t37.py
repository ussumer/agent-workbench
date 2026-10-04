"""T37: environment business invariants, alternatives and independent oracle tests.

Seed is deterministic test input, never the runtime ERP implementation. Generated
cases use a separate budget-state dynamic program as an oracle cross-check.
"""

from __future__ import annotations

import json
import random
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError
from scripts.planning.judge import EnumerationLimit, Objective, grade, solve

from agent.planning.checker import check_plan, order_drafts
from agent.planning.erp import from_erp_details
from agent.planning.models import Demand, Offer, Plan, PlanLine, PlanningProblem
from agent.planning.revisions import revise_problem

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def goal() -> dict:
    return json.loads((ROOT / "fixtures/planning/goal-v1.json").read_text(encoding="utf-8"))


def seeded_problem() -> PlanningProblem:
    seed = json.loads((ROOT / "fixtures/seed-v1.json").read_text(encoding="utf-8"))
    public = goal()
    suppliers = {row["supplier_id"]: row for row in seed["suppliers"]}
    parts = {row["part_id"]: row for row in seed["parts"]}
    details = {}
    for demand in public["demands"]:
        part = demand["part_id"]
        details[part] = {"part": parts[part], "available_suppliers": [
            row for row in seed["supplier_parts"]
            if row["part_id"] == part and suppliers[row["supplier_id"]]["active"]
        ]}
    return from_erp_details(
        goal_id=public["goal_id"], budget=public["budget"],
        demands=tuple(Demand.model_validate(d) for d in public["demands"]),
        details=details, source_refs={part: f"fixture:seed-v1:{part}" for part in details},
    )


def plan(problem: PlanningProblem, *lines: tuple[str, int]) -> Plan:
    return Plan(goal_id=problem.goal_id, revision=problem.revision,
                lines=tuple(PlanLine(offer_id=oid, quantity=q) for oid, q in lines))


def baseline(problem: PlanningProblem) -> Plan:
    # Test candidate from the public worked example, never used by the solver.
    return plan(problem, ("S001:P001", 42), ("S001:P003", 15), ("S002:P004", 23))


def update(model, **fields):
    return type(model).model_validate({**model.model_dump(), **fields})


def small(*, budget="5.00", demands=None, offers=None) -> PlanningProblem:
    d = Demand(part_id="A", quantity=2, max_lead_days=3, required=False, allow_partial=True)
    o = Offer(offer_id="X:A", part_id="A", supplier_id="X", unit_price="1.00", lead_days=2,
              supplier_active=True, part_active=True, relationship_active=True,
              source_ref="test:quote:1", source_status="verified")
    return PlanningProblem(goal_id="small", budget=budget,
                           demands=demands or (d,), offers=(o,) if offers is None else offers)


def test_proposal_arithmetic_shortage_and_two_order_drafts():
    p = seeded_problem()
    report = check_plan(p, baseline(p))
    assert report.feasible
    assert report.total_cost == "2493.50"
    assert report.shortage == {"P001": 0, "P003": 0, "P004": 7}
    assert solve(p).best == Objective((23,), -249350)
    assert grade(p, baseline(p)).accepted
    drafts = order_drafts(p, baseline(p))
    assert [d["supplier_id"] for d in drafts] == ["S001", "S002"]
    assert drafts[0]["lines"] == [
        {"part_id": "P001", "quantity": 42, "unit_price": "25.50"},
        {"part_id": "P003", "quantity": 15, "unit_price": "68.00"},
    ]


def test_requirement_quantities_match_real_seed_inventory_targets():
    inventory = json.loads((ROOT / "fixtures/seed-v1.json").read_text(encoding="utf-8"))["inventory"]
    rows = {i["part_id"]: i for i in inventory}
    for d in seeded_problem().demands:
        assert d.quantity == rows[d.part_id]["target_stock"] - rows[d.part_id]["on_hand"]


def test_equal_objective_accepts_different_suppliers_and_split_allocations():
    p = small()
    p = revise_problem(p, offers=(update(p.offers[0], offer_id="Y:A", supplier_id="Y"),),
                       demands=(update(p.demands[0], allow_supplier_split=True),)).problem
    alternatives = [plan(p, ("X:A", 2)), plan(p, ("Y:A", 2)), plan(p, ("X:A", 1), ("Y:A", 1))]
    assert all(grade(p, candidate).accepted for candidate in alternatives)


def test_feasible_but_nonoptimal_is_reported_separately():
    p = seeded_problem()
    candidate = plan(p, ("S001:P001", 42), ("S001:P003", 15), ("S001:P004", 22))
    result = grade(p, candidate)
    assert result.feasible and not result.optimal and not result.accepted
    assert result.objective == Objective((22,), -248700)


@pytest.mark.parametrize(("change", "code"), [
    ({"supplier_active": False}, "UNAVAILABLE_RELATIONSHIP"),
    ({"part_active": False}, "UNAVAILABLE_RELATIONSHIP"),
    ({"relationship_active": False}, "UNAVAILABLE_RELATIONSHIP"),
    ({"lead_days": 4}, "DEADLINE"),
    ({"unit_price": None, "source_status": "missing"}, "MISSING_PRICE"),
    ({"source_status": "conflict"}, "UNVERIFIED_SOURCE"),
])
def test_invalid_offer_never_authorizes_a_plan(change, code):
    p = small()
    p = revise_problem(p, offers=(update(p.offers[0], **change),)).problem
    candidate = plan(p, ("X:A", 2))
    assert code in {v.code for v in check_plan(p, candidate).violations}
    assert not grade(p, candidate).feasible
    with pytest.raises(ValueError, match="invalid plan"):
        order_drafts(p, candidate)


def test_cheapest_late_supplier_is_not_accepted():
    p = seeded_problem()
    candidate = plan(p, ("S002:P001", 42), ("S001:P003", 15), ("S002:P004", 26))
    assert "DEADLINE" in {v.code for v in check_plan(p, candidate).violations}
    assert not grade(p, candidate).accepted


@pytest.mark.parametrize("lines,code", [
    ((("S001:P001", 41), ("S001:P003", 15)), "REQUIRED_QUANTITY"),
    ((("S001:P001", 43), ("S001:P003", 15)), "OVERBUY"),
    ((("S001:P001", 42), ("S001:P003", 15), ("S002:P004", 24)), "BUDGET"),
    ((("invented", 1),), "UNKNOWN_OFFER"),
])
def test_candidate_quantity_budget_and_evidence_violations(lines, code):
    p = seeded_problem()
    candidate = plan(p, *lines)
    assert code in {v.code for v in check_plan(p, candidate).violations}
    assert not grade(p, candidate).feasible


def test_partial_and_supplier_split_are_distinct_constraints():
    p = small(budget="1.00")
    assert grade(p, plan(p, ("X:A", 1))).accepted
    p = revise_problem(p, demands=(update(p.demands[0], allow_partial=False),)).problem
    assert grade(p, plan(p)).accepted
    assert not grade(p, plan(p, ("X:A", 1))).feasible
    p = revise_problem(p, budget="5.00",
                       offers=(update(p.offers[0], offer_id="Y:A", supplier_id="Y"),)).problem
    candidate = plan(p, ("X:A", 1), ("Y:A", 1))
    assert "SUPPLIER_SPLIT_FORBIDDEN" in {v.code for v in check_plan(p, candidate).violations}
    assert not grade(p, candidate).feasible


def test_priority_coverage_precedes_lower_price_or_more_low_priority_units():
    a = small().demands[0]
    b = update(a, part_id="B", quantity=10, priority=1)
    x = update(small().offers[0], unit_price="3.00")
    y = update(x, part_id="B", offer_id="X:B", unit_price="0.10")
    p = small(budget="3.00", demands=(a, b), offers=(x, y))
    assert solve(p).best == Objective((1, 0), -300)
    assert grade(p, plan(p, ("X:A", 1))).accepted
    assert not grade(p, plan(p, ("X:B", 10))).optimal


def test_same_priority_sums_coverage_before_cost():
    a = small().demands[0]
    b = update(a, part_id="B", quantity=3)
    x = update(small().offers[0], unit_price="2.00")
    y = update(x, part_id="B", offer_id="X:B", unit_price="1.00")
    p = small(budget="3.00", demands=(a, b), offers=(x, y))
    assert grade(p, plan(p, ("X:B", 3))).accepted
    assert solve(p).best == Objective((3,), -300)


def test_required_budget_shortfall_is_business_infeasibility():
    p = seeded_problem()
    p = revise_problem(p, budget="2090.99").problem
    assert solve(p).status == "infeasible"


@pytest.mark.parametrize("status", ["missing", "conflict"])
def test_source_uncertainty_differs_from_business_conflict(status):
    p = seeded_problem()
    offer = next(o for o in p.offers if o.part_id == "P003")
    p = revise_problem(p, offers=(update(offer, unit_price=None, source_status=status),)).problem
    result = solve(p)
    assert result.status == "unresolved"
    assert result.best is None
    assert result.source_issues == ("S001:P003",)


def test_safe_feasible_plan_does_not_prove_optimality_with_missing_competitor_price():
    p = small()
    unknown = update(p.offers[0], offer_id="Y:A", supplier_id="Y",
                     unit_price=None, source_status="missing")
    p = revise_problem(p, offers=(unknown,)).problem
    result = grade(p, plan(p, ("X:A", 2)))
    assert result.feasible and result.optimal is None and not result.accepted
    assert result.environment_status == "unresolved"


@pytest.mark.parametrize("change", [{"supplier_active": False}, {"lead_days": 99}])
def test_known_irrelevant_missing_source_does_not_force_clarification(change):
    p = small()
    unknown = update(p.offers[0], offer_id="Y:A", supplier_id="Y",
                     unit_price=None, source_status="missing", **change)
    p = revise_problem(p, offers=(unknown,)).problem
    assert solve(p).status == "feasible"
    assert grade(p, plan(p, ("X:A", 2))).accepted


def test_unknown_irrelevant_optional_cannot_hide_proven_required_infeasibility():
    a = update(small().demands[0], required=True, allow_partial=False)
    b = update(a, part_id="B", required=False, allow_partial=True)
    o = small().offers[0]
    unknown = update(o, offer_id="X:B", part_id="B", unit_price=None, source_status="missing")
    p = small(budget="1.99", demands=(a, b), offers=(o, unknown))
    assert solve(p).status == "infeasible"


def test_missing_lead_time_can_be_resolved_by_known_dominated_price():
    p = small()
    unknown = update(p.offers[0], offer_id="Y:A", supplier_id="Y",
                     unit_price="9.00", lead_days=None, source_status="missing")
    p = revise_problem(p, offers=(unknown,)).problem
    assert grade(p, plan(p, ("X:A", 2))).accepted


def test_conflicting_lead_time_must_not_be_assumed_late():
    p = small()
    conflict = update(p.offers[0], offer_id="Y:A", supplier_id="Y",
                      unit_price="0.50", lead_days=99, source_status="conflict")
    p = revise_problem(p, offers=(conflict,)).problem
    assert solve(p).status == "unresolved"


def test_budget_change_recomputes_objective_and_rejects_old_plan():
    p = seeded_problem()
    old = baseline(p)
    revision = revise_problem(p, budget="2200.00")
    assert revision.affected_parts == ("P001", "P003", "P004")
    assert revision.global_allocation_changed
    assert solve(revision.problem).best == Objective((6,), -219600)
    assert "STALE_PLAN" in {v.code for v in check_plan(revision.problem, old).violations}
    assert not grade(revision.problem, old).accepted


def test_local_source_change_marks_affected_input_but_global_allocation_needs_checking():
    p = seeded_problem()
    offer = next(o for o in p.offers if o.offer_id == "S002:P004")
    revision = revise_problem(p, offers=(update(offer, unit_price="20.00", source_ref="test:new"),))
    assert revision.affected_parts == ("P004",)
    assert revision.global_allocation_changed
    assert solve(revision.problem).best == Objective((22,), -248700)
    assert not grade(revision.problem, baseline(p)).accepted


def test_deadline_change_and_supplier_removal_recompute_without_fixed_answer():
    p = seeded_problem()
    p = revise_problem(p, demands=(update(p.demands[0], max_lead_days=4),)).problem
    assert solve(p).best == Objective((26,), -248300)
    p = revise_problem(p, remove_offer_ids=("S002:P001",)).problem
    assert solve(p).best == Objective((23,), -249350)


def test_sufficient_budget_does_not_truncate_or_demand_clarification():
    p = revise_problem(seeded_problem(), budget="3000.00").problem
    assert solve(p).best == Objective((30,), -261600)
    assert grade(p, plan(p, ("S001:P001", 42), ("S001:P003", 15), ("S002:P004", 30))).accepted


def test_noop_revision_keeps_identity_and_priority_update_affects_all_parts():
    p = seeded_problem()
    assert revise_problem(p, budget=p.budget, demands=p.demands, offers=p.offers) == replace(
        revise_problem(p), problem=p
    )
    revision = revise_problem(p, demands=(update(p.demands[2], priority=7),))
    assert revision.problem.revision == 2
    assert revision.affected_parts == ("P001", "P003", "P004")


@pytest.mark.parametrize("field,value", [
    ("budget", 5.0), ("budget", "1e2"), ("budget", "-1.00"),
    ("budget", "1.001"), ("revision", True), ("currency", "USD"),
])
def test_strict_problem_fields_reject_ambiguous_values(field, value):
    with pytest.raises(ValidationError):
        update(small(), **{field: value})


def test_offer_and_candidate_schema_reject_forged_prices_and_duplicate_evidence():
    p = small()
    with pytest.raises(ValidationError):
        update(p.offers[0], unit_price="0.00")
    with pytest.raises(ValidationError):
        update(p.offers[0], unit_price=None)
    with pytest.raises(ValidationError):
        update(p.offers[0], source_status="missing")
    with pytest.raises(ValidationError):
        update(p.demands[0], quantity=True)
    with pytest.raises(ValidationError):
        update(p.demands[0], required=True)
    with pytest.raises(ValidationError):
        plan(p, ("X:A", 1), ("X:A", 1))
    with pytest.raises(ValidationError):
        Plan.model_validate({"goal_id": "small", "revision": 1, "lines": [
            {"offer_id": "X:A", "quantity": 2, "unit_price": "0.01"}
        ]})
    with pytest.raises(ValidationError):
        update(p, offers=(p.offers[0], update(p.offers[0], offer_id="other")))


def test_invalid_revision_edits_are_rejected():
    p = small()
    with pytest.raises(ValueError, match="identity"):
        revise_problem(p, offers=(update(p.offers[0], supplier_id="Y"),))
    with pytest.raises(ValueError, match="unknown offer"):
        revise_problem(p, remove_offer_ids=("invented",))
    with pytest.raises(ValueError, match="update and remove"):
        revise_problem(p, offers=p.offers, remove_offer_ids=("X:A",))
    with pytest.raises(ValidationError):
        revise_problem(p, budget="bad")


def test_no_offers_is_infeasible_only_when_required_and_zero_budget_optional_is_valid():
    p = small(offers=(), budget="0.00")
    assert grade(p, plan(p)).accepted
    p = revise_problem(p, demands=(update(p.demands[0], required=True, allow_partial=False),)).problem
    assert solve(p).status == "infeasible"


def test_exhaustive_limit_never_claims_optimality_on_partial_search():
    with pytest.raises(EnumerationLimit):
        solve(seeded_problem(), max_states=2)


def test_foreign_goal_cannot_create_drafts():
    p = small()
    candidate = update(plan(p, ("X:A", 2)), goal_id="other")
    assert "FOREIGN_GOAL" in {v.code for v in check_plan(p, candidate).violations}
    assert not grade(p, candidate).feasible


def test_generated_small_instances_match_independent_budget_dynamic_program():
    rng = random.Random(37103)
    for case in range(80):
        demands, offers, options = [], [], []
        budget_cents = rng.randrange(0, 16)
        for index in range(3):
            quantity = rng.randrange(1, 5)
            required = index == 0 and rng.choice([False, True])
            partial = not required and rng.choice([False, True])
            d = Demand(part_id=str(index), quantity=quantity, max_lead_days=3,
                       required=required, allow_partial=partial, priority=index % 2)
            demands.append(d)
            prices = []
            for supplier in range(2):
                price, days = rng.randrange(1, 8), rng.randrange(1, 6)
                o = update(small().offers[0], offer_id=f"{supplier}:{index}",
                           part_id=str(index), supplier_id=str(supplier),
                           unit_price=f"0.{price:02d}", lead_days=days)
                offers.append(o)
                if days <= 3:
                    prices.append(price)
            # Independent DP uses the cheapest eligible supplier and budget bins,
            # rather than composing every offer vector like the exhaustive judge.
            quantities = ([quantity] if required else list(range(quantity + 1))
                          if partial else [0, quantity])
            options.append([(qty, min(prices) * qty if qty else 0)
                            for qty in quantities if qty == 0 or prices])
        public = small(budget=f"0.{budget_cents:02d}", demands=tuple(demands), offers=tuple(offers))
        priorities = sorted({d.priority for d in demands if not d.required})
        states = {0: (0,) * len(priorities)}
        for d, opts in zip(demands, options, strict=True):
            next_states = {}
            for used, counts in states.items():
                for qty, cost in opts:
                    if used + cost <= budget_cents:
                        coverage = list(counts)
                        if not d.required:
                            coverage[priorities.index(d.priority)] += qty
                        key = used + cost
                        next_states[key] = max(next_states.get(key, tuple(coverage)), tuple(coverage))
            states = next_states
        expected = max((Objective(counts, -cost) for cost, counts in states.items()), default=None)
        oracle = solve(public)
        assert oracle.best == expected, case
        assert oracle.status == ("feasible" if expected is not None else "infeasible"), case


def test_judge_is_not_among_actor_tools_or_synced_skills():
    # Static registration boundary only; OS isolation/sealed data requires live
    # coverage in later packages, which this assertion does not claim to prove.
    for root in (ROOT / "src/skills", ROOT / "src/agent/subagents/configs"):
        for path in root.rglob("*"):
            if path.is_file():
                assert "scripts.planning.judge" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("case_id,status,coverage,cost", [
    ("deadline-conflict", "feasible", 30, 261600),
    ("budget-partial", "feasible", 15, 229050),
    ("source-missing", "unresolved", None, None),
    ("deadline-budget", "feasible", 6, 219600),
    ("deadline-budget-missing", "unresolved", None, None),
    ("sufficient-budget", "feasible", 30, 255300),
    ("no-partial", "feasible", 0, 209100),
    ("business-infeasible", "infeasible", None, None),
])
def test_public_decision_conditions_have_distinct_counterfactual_results(case_id, status, coverage, cost):
    cases = json.loads((ROOT / "fixtures/planning/decision-cases-v1.json").read_text(encoding="utf-8"))
    case = next(c for c in cases["cases"] if c["case_id"] == case_id)
    p = seeded_problem()
    revised_offers = ()
    if case["source_status"] == "missing":
        offer = next(o for o in p.offers if o.part_id == "P003")
        revised_offers = (update(offer, unit_price=None, source_status="missing"),)
    p = revise_problem(p, budget=case["budget"], offers=revised_offers, demands=(
        update(p.demands[0], max_lead_days=case["p001_deadline"]),
        update(p.demands[2], allow_partial=case["allow_partial"]),
    )).problem
    oracle = solve(p)
    assert oracle.status == status
    assert oracle.best == (Objective((coverage,), -cost) if coverage is not None else None)
