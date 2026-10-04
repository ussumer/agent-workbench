"""Record Actor claims without solving the planning problem."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from agent.approval.models import ApprovalError
from agent.planning.models import Contract, Identifier, PlanningProblem, PositiveInt


class PlanningDecision(Contract):
    revision: PositiveInt
    status: Literal["needs_information", "infeasible"]
    reason: str = Field(strict=True, min_length=1, max_length=3000)
    part_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=32)
    source_refs: tuple[Identifier, ...] = Field(min_length=1, max_length=32)


def validate_decision(problem: PlanningProblem, decision: PlanningDecision) -> None:
    if decision.revision != problem.revision:
        raise ApprovalError("STALE_PLAN", "decision revision is stale")
    parts = {d.part_id for d in problem.demands}
    refs = {o.source_ref for o in problem.offers}
    if (
        len(set(decision.part_ids)) != len(decision.part_ids)
        or not set(decision.part_ids) <= parts
        or len(set(decision.source_refs)) != len(decision.source_refs)
        or not set(decision.source_refs) <= refs
    ):
        raise ApprovalError(
            "DECISION_EVIDENCE_INVALID", "cite existing parts and source references"
        )
    # A claim is not a private oracle verdict. Its substantive correctness is
    # evaluated independently; even incorrect claims remain reviewable evidence.


def record_decision(orders: Any, owner: str, thread: str, decision: PlanningDecision) -> dict:
    row = orders.load(owner, thread, thread)
    validate_decision(PlanningProblem.model_validate(row["problem"]), decision)
    if row["proposal"] is not None or row["orders"]:
        raise ApprovalError(
            "DECISION_CONFLICT", "cannot replace a proposed or committed purchase with a claim"
        )
    written = orders.goals.update_one(
        {
            **orders.scope(owner, thread, thread),
            "_id": row["_id"],
            "revision": decision.revision,
            "execution": None,
            "proposal": None,
            "orders": [],
        },
        {"$set": {"decision": decision.model_dump(mode="json")}},
    )
    if written.matched_count != 1:
        raise ApprovalError("REVISION_CONFLICT", "goal changed while recording the decision")
    return decision.model_dump(mode="json")
