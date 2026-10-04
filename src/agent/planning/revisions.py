"""Pure revision/dependency metadata. Persistent computation comes from the kernel."""

from __future__ import annotations

from dataclasses import dataclass

from .models import Demand, Offer, PlanningProblem


@dataclass(frozen=True)
class Revision:
    problem: PlanningProblem
    affected_parts: tuple[str, ...]
    global_allocation_changed: bool


def revise_problem(
    problem: PlanningProblem,
    *,
    budget: str | None = None,
    demands: tuple[Demand, ...] = (),
    offers: tuple[Offer, ...] = (),
    remove_offer_ids: tuple[str, ...] = (),
) -> Revision:
    demand_map = {d.part_id: d for d in problem.demands}
    offer_map = {o.offer_id: o for o in problem.offers}
    affected: set[str] = set()
    global_change = budget is not None and budget != problem.budget
    if len({d.part_id for d in demands}) != len(demands):
        raise ValueError("duplicate demand update")
    if len({o.offer_id for o in offers}) != len(offers):
        raise ValueError("duplicate offer update")
    if set(remove_offer_ids) & {o.offer_id for o in offers}:
        raise ValueError("cannot update and remove the same offer")
    for demand in demands:
        old = demand_map.get(demand.part_id)
        if old is None:
            raise ValueError("new demand requires a new goal in v1")
        if old != demand:
            affected.add(demand.part_id)
            global_change |= old.priority != demand.priority or old.required != demand.required
            demand_map[demand.part_id] = demand
    for oid in remove_offer_ids:
        if oid not in offer_map:
            raise ValueError(f"unknown offer to remove: {oid}")
        affected.add(offer_map.pop(oid).part_id)
    for offer in offers:
        old_offer = offer_map.get(offer.offer_id)
        if old_offer is not None and (old_offer.part_id, old_offer.supplier_id) != (
            offer.part_id, offer.supplier_id
        ):
            raise ValueError("offer identity cannot change")
        if old_offer != offer:
            affected.add(offer.part_id)
            offer_map[offer.offer_id] = offer
    if global_change:
        affected.update(demand_map)
    # Revalidate even no-op inputs (e.g. malformed supplied budget), rather than
    # relying on model_copy, which bypasses Pydantic field validation.
    revised = PlanningProblem.model_validate({
        **problem.model_dump(),
        "budget": problem.budget if budget is None else budget,
        "revision": problem.revision + int(bool(affected)),
        "demands": tuple(demand_map.values()),
        "offers": tuple(offer_map.values()),
    })
    return Revision(revised, tuple(sorted(affected)), bool(affected))
