"""Incremental planning and revisions: real ERP/Mongo/MCP; declared scripted Actor.

No paid model calls or kernel execution. Independent exhaustive judge never enters Actor.
"""
from __future__ import annotations

import random
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from itertools import product

import httpx
import pytest
import test_t39 as services
import test_t40 as wiring
import test_t41 as trace
from pydantic import ValidationError
from scripts.planning.judge import Objective, grade, solve
from test_t12 import tool_call
from test_t39 import OWNER, actual_orders, approve, execute

from agent.approval.models import ApprovalError
from agent.planning.checker import check_plan
from agent.planning.models import CommittedLine, Demand, Offer, Plan, PlanLine, PlanningProblem
from agent.planning.reconciliation import verify_orders, with_commitments
from agent.planning.revisions import revise_problem
from api_view.api.planning import GoalRevisionInput

db = services.db
stack = services.stack
scenario = services.scenario
resources = wiring.resources
actor = wiring.actor
traced = trace.traced
pytestmark = pytest.mark.integration


def patch(actor, *, expected_revision=1, **changes):
    client = actor[0]
    response = client.patch(f"/api/planning/{actor[-1][1]}/goal", json={
        "expected_revision": expected_revision, **changes})
    return response


def reconcile(scenario, *, budget="3000.00", demands=(), offers=()):
    orders, thread, _, _, erp, _ = scenario
    row = orders.load(OWNER, thread, thread)
    previous = PlanningProblem.model_validate(row["problem"])
    with erp.client() as client:
        frozen = verify_orders(client, row, OWNER)
    revision = with_commitments(revise_problem(previous, budget=budget, demands=demands,
                                             offers=offers), previous, frozen)
    return orders.revise_reconciled(OWNER, thread, row, revision, sources=row.get("sources", []))


def write_first(scenario):
    orders, thread, _, plan, _, channel = scenario
    first = orders.prepare(OWNER, thread, plan)[0]
    approve(orders, thread, first)
    result = execute(orders, thread, first, channel)
    assert result["ok"], result
    return first, result


def test_real_committed_core_then_incremental_optional_order_without_rebuy(scenario):
    orders, thread, _, _, erp, channel = scenario
    before = actual_orders(erp)["total"]
    _, original = write_first(scenario)
    row = reconcile(scenario, budget="2600.00")
    problem = PlanningProblem.model_validate(row["problem"])
    assert problem.schema_version == 2
    assert {(c.part_id, c.quantity) for c in problem.commitments} == {("P001", 42), ("P003", 15)}
    plan = Plan(goal_id=thread, revision=2, lines=(PlanLine(offer_id="S002:P004", quantity=29),))
    assert check_plan(problem, plan).total_cost == "2598.50"
    assert grade(problem, plan).accepted
    actions = orders.prepare(OWNER, thread, plan)
    assert len(actions) == 1 and actions[0].payload["lines"] == [
        {"part_id": "P004", "quantity": 29, "unit_price": "17.50"}]
    approve(orders, thread, actions[0])
    added = execute(orders, thread, actions[0], channel)
    assert added["ok"]
    ids = {original["data"]["order_id"], added["data"]["order_id"]}
    actual = [o for o in actual_orders(erp)["items"] if o["order_id"] in ids]
    quantities = Counter()
    for o in actual:
        for line in o["lines"]:
            quantities[line["part_id"]] += line["quantity"]
    assert quantities == {"P001": 42, "P003": 15, "P004": 29}
    assert actual_orders(erp)["total"] == before + 2
    assert execute(orders, thread, actions[0], channel) == added
    assert actual_orders(erp)["total"] == before + 2


def test_empty_append_after_full_coverage_is_feasible_and_writes_nothing(scenario):
    orders, thread, _, _, erp, _ = scenario
    write_first(scenario)
    row = reconcile(scenario, budget="2091.00", demands=(
        Demand(part_id="P004", quantity=30, max_lead_days=7, required=False, allow_partial=True),))
    plan = Plan(goal_id=thread, revision=2, lines=())
    problem = PlanningProblem.model_validate(row["problem"])
    assert check_plan(problem, plan).feasible and grade(problem, plan).accepted
    before = actual_orders(erp)["total"]
    assert orders.prepare(OWNER, thread, plan) == ()
    assert actual_orders(erp)["total"] == before


@pytest.mark.parametrize("change,code", [
    ({"budget": "2000.00"}, "BUDGET"),
    ({"demands": (Demand(part_id="P001", quantity=41, max_lead_days=3, required=True),)}, "OVERBUY"),
    ({"demands": (Demand(part_id="P001", quantity=42, max_lead_days=2, required=True),)}, "COMMITTED_DEADLINE"),
])
def test_new_constraints_cannot_erase_committed_cost_quantities_or_deadline(scenario, change, code):
    orders, thread, _, _, erp, _ = scenario
    _, original = write_first(scenario)
    row = reconcile(scenario, **change)
    problem = PlanningProblem.model_validate(row["problem"])
    plan = Plan(goal_id=thread, revision=2, lines=())
    result = check_plan(problem, plan)
    assert code in {v.code for v in result.violations}
    assert solve(problem).status == "infeasible" and not grade(problem, plan).accepted
    assert orders.load(OWNER, thread, thread)["orders"][0]["result"] == original
    with erp.client() as client:
        frozen = verify_orders(client, row, OWNER)
    assert sum(c.quantity for c in frozen if c.part_id == "P001") == 42


def test_source_reprice_and_supplier_retirement_do_not_change_historical_cost(scenario):
    orders, thread, problem, _, _, _ = scenario
    write_first(scenario)
    offer = next(o for o in problem.offers if o.offer_id == "S001:P001")
    changed = Offer.model_validate({**offer.model_dump(), "unit_price": "0.01", "supplier_active": False})
    row = reconcile(scenario, budget="2091.00", offers=(changed,))
    problem = PlanningProblem.model_validate(row["problem"])
    plan = Plan(goal_id=thread, revision=2, lines=())
    assert check_plan(problem, plan).feasible and check_plan(problem, plan).total_cost == "2091.00"
    assert grade(problem, plan).accepted
    assert next(c for c in problem.commitments if c.part_id == "P001").unit_price == "25.50"


def test_actual_erp_modification_refuses_revision_and_preserves_prior_goal(actor):
    write_first(actor[-1])
    orders, thread, _, _, erp, _ = actor[-1]
    row = orders.load(OWNER, thread, thread)
    outcome = row["orders"][0]
    payload = dict(outcome["approved_payload"])
    payload.update(expected_version=outcome["result"]["data"]["version"], note="external modification test")
    with erp.client() as client:
        response = client.put(f"/api/erp/v1/orders/{outcome['result']['data']['order_id']}",
            json=payload, headers={"X-Operation-Id": "t43-external-" + uuid.uuid4().hex})
        assert response.status_code == 200, response.text
    result = patch(actor, budget="3000.00")
    assert result.status_code == 409 and "ERP_ORDER_CHANGED" in result.text
    assert orders.load(OWNER, thread, thread) == row


def test_legacy_missing_quote_evidence_is_refused_without_inventing_history(actor):
    write_first(actor[-1])
    orders, thread = actor[-1][:2]
    orders.goals.update_one({"owner_user_id": OWNER, "thread_id": thread}, {"$unset": {"orders.0.commitments": ""}})
    assert "COMMITMENT_EVIDENCE_MISSING" in patch(actor, budget="3000.00").text
    assert orders.load(OWNER, thread, thread)["revision"] == 1


def test_actual_erp_read_failure_does_not_advance_revision(actor, monkeypatch):
    write_first(actor[-1])
    def unavailable():
        raise httpx.ConnectError("test ERP unavailable")
    actor[1].extra["planning_erp_client"] = unavailable
    response = patch(actor, budget="3000.00")
    assert response.status_code == 503
    orders, thread = actor[-1][:2]
    assert orders.load(OWNER, thread, thread)["revision"] == 1


def test_public_patch_refreshes_only_named_parts_and_archives_history(actor):
    client, _, _, _, _, scenario = actor
    thread = "t43-source-refresh-" + uuid.uuid4().hex
    # Record original real sources through POST rather than fabricate them.
    response = client.post(f"/api/planning/{thread}/goal", json={"budget": "2500.00",
        "demands": [d.model_dump(mode="json") for d in scenario[2].demands]})
    assert response.status_code == 200, response.text
    before = scenario[0].load(OWNER, thread, thread)
    response = client.patch(f"/api/planning/{thread}/goal", json={
        "expected_revision": 1, "refresh_part_ids": ["P004"]})
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["revision_change"] == {"affected_parts": ["P004"], "global_allocation_changed": True}
    after = scenario[0].load(OWNER, thread, thread)
    assert after["revision_history"][0]["problem"] == before["problem"]
    assert after["revision_history"][0]["sources"] == before["sources"]
    assert [s for s in after["sources"] if s["part_id"] != "P004"] == [
        s for s in before["sources"] if s["part_id"] != "P004"]
    assert next(s for s in after["sources"] if s["part_id"] == "P004") != next(
        s for s in before["sources"] if s["part_id"] == "P004")


def test_formal_stale_approval_can_replan_without_rebuy_in_same_episode(traced):
    actor, store, model, _ = traced
    client = actor[0]
    orders, thread, _, _, erp, _ = actor[-1]
    before = actual_orders(erp)["total"]
    wiring.start(actor)
    assert wiring.resume(actor)[-1]["payload"]["status"] == "interrupted"
    old = wiring.pending(actor)
    episode = trace.exported(traced)["episode"]
    response = patch(actor, budget="3000.00")
    assert response.status_code == 200, response.text
    bad = client.post(f"/api/chat/{thread}/resume", json={"request_id": uuid.uuid4().hex,
        "interrupt_id": old["interrupt_id"], "resume": {"decisions": [{"type": "approve"}]}})
    assert bad.status_code == 400 and "STALE_PLAN" in bad.text, bad.text
    assert actual_orders(erp)["total"] == before + 1
    new = Plan(goal_id=thread, revision=2, lines=(PlanLine(offer_id="S002:P004", quantity=30),))
    model.script = [tool_call("planning_submit", {"plan": new.model_dump(mode="json")}, "revised-submit")]
    events = wiring.frames(client.post("/api/chat/stream", json={"thread_id": thread,
        "request_id": uuid.uuid4().hex, "message": "预算调整后按新目标继续规划，仅追加未采购物料"}))
    assert events[-1]["payload"]["status"] == "interrupted", events
    # A real provider requires all tool results immediately after their AI
    # message. Scripted models otherwise accept the invalid AI/Human/Tool order.
    for turn in model.seen:
        outstanding = set()
        for message in turn:
            if outstanding:
                assert message["type"] == "tool", message
            if message["type"] == "ai":
                outstanding.update(c["id"] for c in message.get("tool_calls", []))
            elif message["type"] == "tool":
                assert message["tool_call_id"] in outstanding
                outstanding.remove(message["tool_call_id"])
        assert not outstanding
    current = wiring.pending(actor)
    assert current["interrupt_id"] != old["interrupt_id"]
    assert wiring.resume(actor)[-1]["payload"]["status"] == "completed"
    assert actual_orders(erp)["total"] == before + 2
    saved = store.export(OWNER, episode["_id"])
    assert saved["episode"]["bank_id"] == episode["bank_id"]
    assert len(saved["episode"]["run_ids"]) == 4
    ids = {o["result"]["data"]["order_id"] for o in orders.load(OWNER, thread, thread)["orders"]}
    actual = [o for o in actual_orders(erp)["items"] if o["order_id"] in ids]
    assert sum(line["quantity"] for o in actual for line in o["lines"] if line["part_id"] == "P001") == 42


def test_goal_revision_compare_and_swap_has_one_winner(scenario):
    orders, thread, previous, _, _, _ = scenario
    row = orders.load(OWNER, thread, thread)
    revision = with_commitments(revise_problem(previous, budget="3000.00"), previous, ())
    def update(_):
        try:
            return orders.revise_reconciled(OWNER, thread, row, revision, sources=[])["revision"]
        except ApprovalError as failure:
            return failure.code
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(update, range(4)))
    assert results.count(2) == 1 and results.count("REVISION_CONFLICT") == 3


def test_revision_cas_refuses_order_written_after_the_reconciliation_snapshot(scenario):
    orders, thread, problem, _, _, _ = scenario
    previous = orders.load(OWNER, thread, thread)
    revision = with_commitments(revise_problem(problem, budget="3000.00"), problem, ())
    write_first(scenario)
    with pytest.raises(ApprovalError) as failure:
        orders.revise_reconciled(OWNER, thread, previous, revision, sources=[])
    assert failure.value.code == "REVISION_CONFLICT"
    current = orders.load(OWNER, thread, thread)
    assert current["revision"] == 1 and len(current["orders"]) == 1


def test_unknown_saved_order_id_is_not_treated_as_zero_committed_quantity(actor):
    write_first(actor[-1])
    orders, thread = actor[-1][:2]
    orders.goals.update_one({"owner_user_id": OWNER, "thread_id": thread}, {"$set": {
        "orders.0.result.data.order_id": "missing-" + uuid.uuid4().hex}})
    response = patch(actor, budget="3000.00")
    assert response.status_code == 409 and "ERP_ORDER_UNVERIFIED" in response.text
    assert orders.load(OWNER, thread, thread)["revision"] == 1


def test_same_budget_reconciliation_advances_once_then_noop_is_idempotent(actor):
    write_first(actor[-1])
    first = patch(actor, budget="2500.00")
    assert first.status_code == 200, first.text
    assert first.json()["data"]["problem"]["revision"] == 2
    orders, thread = actor[-1][:2]
    row = orders.load(OWNER, thread, thread)
    second = patch(actor, expected_revision=2, budget="2500.00")
    assert second.status_code == 200, second.text
    assert second.json()["data"]["problem"]["revision"] == 2
    assert orders.load(OWNER, thread, thread) == row


@pytest.mark.parametrize("phase", ["inflight", "uncertain"])
def test_execution_lease_blocks_goal_change(actor, phase):
    orders, thread = actor[-1][:2]
    orders.goals.update_one({"owner_user_id": OWNER, "thread_id": thread}, {"$set": {
        "execution": {"phase": phase, "token": "test-held", "interrupt_id": "test"}}})
    response = patch(actor, budget="3000.00")
    assert response.status_code == 409 and "GOAL_BUSY" in response.text
    assert orders.load(OWNER, thread, thread)["revision"] == 1


def test_revision_schema_and_owner_refuse_actor_fabricated_committed_or_quote_data(actor):
    with pytest.raises(ValidationError):
        GoalRevisionInput.model_validate({"expected_revision": 1, "commitments": []})
    with pytest.raises(ValidationError):
        GoalRevisionInput.model_validate({"expected_revision": 1, "offers": []})
    assert patch(actor, expected_revision=2, budget="3000.00").status_code == 409
    actor[0].post("/api/demo/session", json={"user_id": "demo-b"})
    response = actor[0].patch(f"/api/planning/{actor[-1][1]}/goal",
                             json={"expected_revision": 1, "budget": "3000.00"})
    assert response.status_code == 404
    actor[0].post("/api/demo/session", json={"user_id": OWNER})
    assert patch(actor, refresh_part_ids=["P005"]).status_code == 422


def tiny(*, split=True, partial=True):
    demand = Demand(part_id="x", quantity=4, max_lead_days=3, required=False,
                    allow_partial=partial, allow_supplier_split=split)
    offers = tuple(Offer(offer_id=s, supplier_id=s, part_id="x", unit_price="1.00", lead_days=1,
                        source_ref="test", source_status="verified", supplier_active=True,
                        part_active=True, relationship_active=True) for s in ("a", "b"))
    commitment = CommittedLine(order_id="old", offer_id="a", supplier_id="a", part_id="x", quantity=2,
                               unit_price="2.00", lead_days=2, source_ref="original-test")
    return PlanningProblem(schema_version=2, goal_id="tiny", budget="6.00", demands=(demand,),
                           offers=offers, commitments=(commitment,))


def test_cumulative_supplier_split_and_partial_checks_and_equal_optimal_appends():
    problem = tiny()
    for supplier in ("a", "b"):
        plan = Plan(goal_id="tiny", revision=1, lines=(PlanLine(offer_id=supplier, quantity=2),))
        assert check_plan(problem, plan).feasible and grade(problem, plan).accepted
    plan = Plan(goal_id="tiny", revision=1, lines=(PlanLine(offer_id="b", quantity=1),))
    assert "SUPPLIER_SPLIT_FORBIDDEN" in {v.code for v in check_plan(tiny(split=False), plan).violations}
    assert "PARTIAL_FORBIDDEN" in {v.code for v in check_plan(tiny(partial=False), plan).violations}
    assert not grade(tiny(partial=False), plan).feasible


def test_independent_oracle_matches_separate_allocation_enumeration_on_committed_small_cases():
    rng = random.Random(43)
    for _ in range(60):
        quantity, old = rng.randint(1, 5), rng.randint(1, 4)
        budget, price_a, price_b, past_price = rng.randint(1, 16), rng.randint(1, 4), rng.randint(1, 4), rng.randint(1, 4)
        split, partial = rng.choice([True, False]), rng.choice([True, False])
        problem = tiny(split=split, partial=partial)
        problem = PlanningProblem.model_validate({**problem.model_dump(mode="json"),
            "budget": f"{budget}.00", "demands": [{**problem.demands[0].model_dump(), "quantity": quantity}],
            "offers": [{**o.model_dump(), "unit_price": f"{price_a if o.offer_id == 'a' else price_b}.00"} for o in problem.offers],
            "commitments": [{**problem.commitments[0].model_dump(), "quantity": old, "unit_price": f"{past_price}.00"}]})
        expected = None
        for a, b in product(range(quantity + 1), repeat=2):
            total, cost = old + a + b, old * past_price + a * price_a + b * price_b
            feasible = (total <= quantity and cost <= budget and (split or b == 0)
                        and (partial or total == quantity))
            plan = Plan(goal_id="tiny", revision=1, lines=tuple(
                PlanLine(offer_id=s, quantity=q) for s, q in (("a", a), ("b", b)) if q))
            assert check_plan(problem, plan).feasible == feasible
            if feasible:
                objective = Objective((total,), -100 * cost)
                expected = max(expected, objective) if expected else objective
        oracle = solve(problem)
        assert oracle.best == expected
        assert oracle.status == ("feasible" if expected else "infeasible")


def test_fully_committed_part_does_not_become_unresolved_due_to_new_missing_quote():
    problem = tiny()
    problem = PlanningProblem.model_validate({**problem.model_dump(mode="json"),
        "budget": "4.00", "demands": [{**problem.demands[0].model_dump(), "quantity": 2}],
        "offers": [{**o.model_dump(), "unit_price": None, "source_status": "missing"} for o in problem.offers]})
    plan = Plan(goal_id="tiny", revision=1, lines=())
    assert grade(problem, plan).accepted and solve(problem).source_issues == ()
