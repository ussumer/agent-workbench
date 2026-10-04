"""Verify actual ERP orders before anchoring a new revision's incremental plan.

No optimizer, cancellation, repricing, hidden scoring or host Actor execution.
"""

from __future__ import annotations

from typing import Any

import httpx

from agent.approval.models import ApprovalError

from .models import CommittedLine, PlanningProblem, cents, money
from .revisions import Revision


def committed_lines(problem: PlanningProblem, plan: Any, supplier: str,
                    order_id: str) -> list[dict[str, Any]]:
    offers = {o.offer_id: o for o in problem.offers}
    result = []
    for line in plan.lines:
        offer = offers[line.offer_id]
        if offer.supplier_id != supplier:
            continue
        if offer.unit_price is None or offer.lead_days is None:
            raise ApprovalError("COMMITMENT_EVIDENCE_MISSING", "approved quote has no price or lead time")
        result.append(CommittedLine(order_id=order_id, offer_id=offer.offer_id,
            supplier_id=supplier, part_id=offer.part_id, quantity=line.quantity,
            unit_price=offer.unit_price, lead_days=offer.lead_days,
            source_ref=offer.source_ref).model_dump(mode="json"))
    return result


def verify_orders(client: httpx.Client, row: dict[str, Any], owner: str) -> tuple[CommittedLine, ...]:
    commitments: list[CommittedLine] = []
    seen = set()
    for outcome in row["orders"]:
        data = outcome["result"]["data"]
        order_id = data["order_id"]
        if order_id in seen or not outcome.get("commitments") or not outcome.get("approved_payload"):
            raise ApprovalError("COMMITMENT_EVIDENCE_MISSING", "order lacks its original approved quote evidence")
        seen.add(order_id)
        response = client.get("/api/erp/v1/orders", params={"order_id": order_id, "page": 1, "page_size": 1})
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or not isinstance(body.get("data"), dict) or not body.get("request_id"):
            raise ApprovalError("ERP_ORDER_UNVERIFIED", "ERP did not verify this order")
        page = body["data"]
        items = page["items"]
        if page["total"] != 1 or len(items) != 1:
            raise ApprovalError("ERP_ORDER_UNVERIFIED", "saved order missing or not uniquely visible")
        actual = items[0]
        payload = outcome["approved_payload"]
        lines = [{k: line[k] for k in ("part_id", "quantity", "unit_price")} for line in actual["lines"]]
        expected_total = money(sum(cents(line["unit_price"]) * line["quantity"] for line in payload["lines"]))
        if (actual.get("owner_user_id") != owner or actual.get("order_id") != order_id
                or actual.get("supplier_id") != payload["supplier_id"]
                or actual.get("currency") != payload["currency"]
                or actual.get("version") != data["version"]
                or actual.get("note") != payload["note"]
                or actual.get("total_amount") != expected_total
                or sorted(lines, key=lambda v: v["part_id"]) != payload["lines"]):
            raise ApprovalError("ERP_ORDER_CHANGED", "actual order differs from its approved planning write")
        frozen = tuple(CommittedLine.model_validate(c) for c in outcome["commitments"])
        if (any(c.order_id != order_id or c.supplier_id != payload["supplier_id"] for c in frozen)
                or [{"part_id": c.part_id, "quantity": c.quantity, "unit_price": c.unit_price}
                    for c in sorted(frozen, key=lambda c: c.part_id)] != payload["lines"]):
            raise ApprovalError("COMMITMENT_EVIDENCE_MISSING", "committed facts do not match the approved write")
        commitments.extend(frozen)
    return tuple(commitments)


def with_commitments(revision: Revision, previous: PlanningProblem,
                     commitments: tuple[CommittedLine, ...]) -> Revision:
    changed = commitments != previous.commitments
    affected = set(revision.affected_parts)
    if changed:
        affected.update(c.part_id for c in commitments)
    problem = PlanningProblem.model_validate({**revision.problem.model_dump(mode="json"),
        "schema_version": 2 if commitments else previous.schema_version,
        "revision": previous.revision + int(bool(affected)),
        "commitments": [c.model_dump(mode="json") for c in commitments]})
    return Revision(problem, tuple(sorted(affected)), bool(affected))


def revision_metadata(revision: Revision) -> dict[str, Any]:
    return {"affected_parts": list(revision.affected_parts),
            "global_allocation_changed": revision.global_allocation_changed}
