"""Check an Actor's proposed plan, without making procurement decisions for it."""

from __future__ import annotations

from dataclasses import dataclass

from .models import Plan, PlanningProblem, cents, money


@dataclass(frozen=True)
class Violation:
    code: str
    reference: str


@dataclass(frozen=True)
class PlanCheck:
    feasible: bool
    violations: tuple[Violation, ...]
    total_cost: str
    shortage: dict[str, int]
    optional_coverage: tuple[tuple[int, int], ...]


def check_plan(problem: PlanningProblem, plan: Plan) -> PlanCheck:
    errors: list[Violation] = []
    if plan.goal_id != problem.goal_id:
        errors.append(Violation("FOREIGN_GOAL", plan.goal_id))
    if plan.revision != problem.revision:
        errors.append(Violation("STALE_PLAN", str(plan.revision)))
    offers = {o.offer_id: o for o in problem.offers}
    demands = {d.part_id: d for d in problem.demands}
    purchased = dict.fromkeys(demands, 0)
    suppliers: dict[str, set[str]] = {part: set() for part in demands}
    cost = 0
    for committed in problem.commitments:
        cost += cents(committed.unit_price) * committed.quantity
        if committed.part_id not in demands:
            errors.append(Violation("COMMITTED_PART_REMOVED", committed.part_id))
            continue
        purchased[committed.part_id] += committed.quantity
        suppliers[committed.part_id].add(committed.supplier_id)
        if committed.lead_days > demands[committed.part_id].max_lead_days:
            errors.append(Violation("COMMITTED_DEADLINE", committed.order_id))
    for line in plan.lines:
        offer = offers.get(line.offer_id)
        if offer is None:
            errors.append(Violation("UNKNOWN_OFFER", line.offer_id))
            continue
        demand = demands[offer.part_id]
        purchased[offer.part_id] += line.quantity
        suppliers[offer.part_id].add(offer.supplier_id)
        if not (offer.supplier_active and offer.part_active and offer.relationship_active):
            errors.append(Violation("UNAVAILABLE_RELATIONSHIP", offer.offer_id))
        if offer.source_status != "verified":
            errors.append(Violation("UNVERIFIED_SOURCE", offer.offer_id))
        if offer.lead_days is None or offer.lead_days > demand.max_lead_days:
            errors.append(Violation("DEADLINE", offer.offer_id))
        if offer.unit_price is None:
            errors.append(Violation("MISSING_PRICE", offer.offer_id))
        else:
            cost += cents(offer.unit_price) * line.quantity
    coverage: dict[int, int] = {}
    shortage = {}
    for part, demand in demands.items():
        qty = purchased[part]
        shortage[part] = max(0, demand.quantity - qty)
        if qty > demand.quantity:
            errors.append(Violation("OVERBUY", part))
        if demand.required and qty != demand.quantity:
            errors.append(Violation("REQUIRED_QUANTITY", part))
        if not demand.required and not demand.allow_partial and qty not in (0, demand.quantity):
            errors.append(Violation("PARTIAL_FORBIDDEN", part))
        if not demand.allow_supplier_split and len(suppliers[part]) > 1:
            errors.append(Violation("SUPPLIER_SPLIT_FORBIDDEN", part))
        if not demand.required:
            coverage[demand.priority] = coverage.get(demand.priority, 0) + qty
    if cost > cents(problem.budget):
        errors.append(Violation("BUDGET", problem.goal_id))
    return PlanCheck(
        not errors, tuple(errors), money(cost), shortage, tuple(sorted(coverage.items()))
    )


def order_drafts(problem: PlanningProblem, plan: Plan) -> tuple[dict, ...]:
    """ERP-shaped drafts only. Existing write tools still require separate approval."""
    result = check_plan(problem, plan)
    if not result.feasible:
        raise ValueError(f"invalid plan: {[v.code for v in result.violations]}")
    offers = {offer.offer_id: offer for offer in problem.offers}
    groups: dict[str, list[dict]] = {}
    for line in plan.lines:
        offer = offers[line.offer_id]
        groups.setdefault(offer.supplier_id, []).append({
            "part_id": offer.part_id, "quantity": line.quantity, "unit_price": offer.unit_price
        })
    return tuple({
        "supplier_id": supplier,
        "currency": problem.currency,
        "lines": sorted(lines, key=lambda item: item["part_id"]),
        "note": f"planning:{problem.goal_id}:revision:{problem.revision}",
    } for supplier, lines in sorted(groups.items()))
