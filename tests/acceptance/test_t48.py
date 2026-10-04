from __future__ import annotations

import pytest
import test_t39 as mongo_fixtures

from agent.approval.models import ApprovalError
from agent.planning.decisions import PlanningDecision, record_decision, validate_decision
from agent.planning.models import Demand, Offer, PlanningProblem
from agent.planning.orders import PlanningOrders

db = mongo_fixtures.db


def problem(revision=1):
    return PlanningProblem(
        goal_id="g",
        revision=revision,
        budget="10.00",
        demands=(Demand(part_id="P1", quantity=1, max_lead_days=2, required=True),),
        offers=(
            Offer(
                offer_id="O1",
                supplier_id="S1",
                part_id="P1",
                supplier_active=True,
                part_active=True,
                relationship_active=True,
                unit_price=None,
                lead_days=None,
                source_ref="erp:P1",
                source_status="missing",
            ),
        ),
    )


@pytest.mark.parametrize("status", ["needs_information", "infeasible"])
def test_decision_is_public_claim(status):
    d = PlanningDecision(
        revision=1,
        status=status,
        reason="evidence check",
        part_ids=("P1",),
        source_refs=("erp:P1",),
    )
    validate_decision(problem(), d)


def test_stale_revision_rejected():
    d = PlanningDecision(
        revision=1,
        status="needs_information",
        reason="x",
        part_ids=("P1",),
        source_refs=("erp:P1",),
    )
    with pytest.raises(ApprovalError, match="stale"):
        validate_decision(problem(2), d)


@pytest.mark.parametrize("part_ids", [("P2",), ("P1", "P1")])
def test_unknown_or_duplicate_parts_rejected(part_ids):
    d = PlanningDecision(
        revision=1,
        status="needs_information",
        reason="x",
        part_ids=part_ids,
        source_refs=("erp:P1",),
    )
    with pytest.raises(ApprovalError):
        validate_decision(problem(), d)


def test_unknown_source_rejected():
    d = PlanningDecision(
        revision=1,
        status="needs_information",
        reason="x",
        part_ids=("P1",),
        source_refs=("erp:P2",),
    )
    with pytest.raises(ApprovalError):
        validate_decision(problem(), d)


def test_empty_reason_rejected():
    with pytest.raises(ValueError):
        PlanningDecision(
            revision=1, status="infeasible", reason="", part_ids=("P1",), source_refs=("erp:P1",)
        )


def test_decision_does_not_select_orders():
    schema = PlanningDecision.model_json_schema()
    assert "offer_id" not in str(schema)


def test_contrast_includes_real_failure_observation_not_only_operation_name():
    import json

    from agent.evolution.curator import contrastive_operations

    result = {
        "model_calls": 1,
        "episode_export": {
            "episode": {"_id": "episode"},
            "events": [
                {"kind": "turn_grounded", "payload": {"selection": []}},
                {
                    "kind": "tool_requested",
                    "payload": {
                        "call": {
                            "id": "call",
                            "name": "computation_execute",
                            "args": {"code": "print(x)"},
                        }
                    },
                },
                {
                    "kind": "tool_observed",
                    "event_id": "event",
                    "payload": {
                        "tool_call_id": "call",
                        "name": "computation_execute",
                        "raw_result": {
                            "content": json.dumps(
                                {
                                    "ok": False,
                                    "data": {
                                        "status": "failed",
                                        "stderr": "NameError: x is not defined",
                                        "base_version": 1,
                                        "version": 1,
                                    },
                                }
                            )
                        },
                    },
                },
            ],
        },
    }
    records = contrastive_operations(result)
    assert records[0]["input"]["code"] == "print(x)"
    assert records[0]["outcome"] == "failed"
    assert "NameError" in records[0]["observations"]["stderr"]["excerpt"]
    assert records[0]["mode"] == "cold-start-mining"


def test_unpaired_observation_is_rejected():
    from agent.evolution.curator import contrastive_operations

    with pytest.raises(ValueError, match="unpaired"):
        contrastive_operations(
            {
                "model_calls": 1,
                "episode_export": {
                    "events": [
                        {
                            "kind": "tool_observed",
                            "payload": {"name": "computation_execute", "tool_call_id": "missing"},
                        }
                    ]
                },
            }
        )


@pytest.fixture
def persisted_goal(db):
    db.planning_goals.delete_many({})
    orders = PlanningOrders(db, grant_secret="t48-test-secret")
    orders.create("owner", "g", problem())
    return orders


def decision():
    return PlanningDecision(
        revision=1,
        status="needs_information",
        reason="Missing source",
        part_ids=("P1",),
        source_refs=("erp:P1",),
    )


def test_record_persists_without_approval_and_revision_clears_it(persisted_goal):
    orders = persisted_goal
    saved = record_decision(orders, "owner", "g", decision())
    row = orders.load("owner", "g", "g")
    assert row["decision"] == saved
    assert row["proposal"] is None and row["orders"] == []
    assert orders.approvals.store.list_for_thread("owner", "g") == []
    orders.revise("owner", "g", problem(2))
    assert "decision" not in orders.load("owner", "g", "g")


@pytest.mark.parametrize("owner,thread", [("foreign", "g"), ("owner", "foreign")])
def test_record_rejects_foreign_scope(persisted_goal, owner, thread):
    with pytest.raises(ApprovalError, match="no planning goal"):
        record_decision(persisted_goal, owner, thread, decision())
    assert "decision" not in persisted_goal.load("owner", "g", "g")


def test_execution_lease_prevents_decision_commit(persisted_goal):
    persisted_goal.goals.update_one({"goal_id": "g"}, {"$set": {"execution": {"id": "active"}}})
    with pytest.raises(ApprovalError, match="changed"):
        record_decision(persisted_goal, "owner", "g", decision())
    assert "decision" not in persisted_goal.load("owner", "g", "g")
