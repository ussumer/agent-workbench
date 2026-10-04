"""Normalize real ERP responses. No seed fallback, cached answer, or price guess."""

from __future__ import annotations

from collections.abc import Mapping

import httpx

from .models import Demand, Offer, PlanningProblem


def from_erp_details(
    *, goal_id: str, budget: str, demands: tuple[Demand, ...],
    details: Mapping[str, dict], source_refs: Mapping[str, str],
) -> PlanningProblem:
    offers = []
    for demand in demands:
        detail = details[demand.part_id]
        part = detail["part"]
        if part["part_id"] != demand.part_id:
            raise ValueError("ERP part identity mismatch")
        for row in detail["available_suppliers"]:
            if row["currency"] != "CNY":
                raise ValueError("planning-v1 only supports CNY")
            price, lead = row.get("catalog_price"), row.get("lead_days")
            offers.append(Offer(
                offer_id=f"{row['supplier_id']}:{demand.part_id}",
                supplier_id=row["supplier_id"], part_id=demand.part_id,
                # ERP part detail only emits active supplying relationships.
                supplier_active=True, relationship_active=True, part_active=part["active"],
                unit_price=price, lead_days=lead, source_ref=source_refs[demand.part_id],
                source_status="verified" if price is not None and lead is not None else "missing",
            ))
    return PlanningProblem(goal_id=goal_id, budget=budget, demands=demands, offers=tuple(offers))


def read_erp_problem(
    client: httpx.Client, *, goal_id: str, budget: str, demands: tuple[Demand, ...],
) -> PlanningProblem:
    """Controller/test adapter; caller supplies authenticated client, never model input tokens."""
    details, sources = {}, {}
    for demand in demands:
        path = f"/api/erp/v1/parts/{demand.part_id}"
        response = client.get(path)
        response.raise_for_status()
        body = response.json()
        details[demand.part_id] = body["data"]
        # Relative references prevent recording token-bearing endpoints/headers.
        sources[demand.part_id] = f"erp:{path}#request_id={body['request_id']}"
    return from_erp_details(
        goal_id=goal_id, budget=budget, demands=demands, details=details, source_refs=sources
    )
