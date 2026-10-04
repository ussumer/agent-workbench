"""Independent bounded exhaustive oracle for small procurement tasks.

The public checker is deliberately not imported. These answers belong to the
evaluation controller, and must not become an Actor tool or sandbox asset.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Literal

from agent.planning.models import Demand, Offer, Plan, PlanningProblem


def _price(value: str) -> int:
    whole, fractional = value.split(".")
    return 100 * int(whole) + int(fractional)


@dataclass(frozen=True, order=True)
class Objective:
    coverage: tuple[int, ...]
    negative_cost: int


@dataclass(frozen=True)
class Oracle:
    status: Literal["feasible", "infeasible", "unresolved"]
    best: Objective | None
    source_issues: tuple[str, ...]
    states_examined: int


@dataclass(frozen=True)
class Grade:
    feasible: bool
    optimal: bool | None
    accepted: bool
    objective: Objective | None
    violations: tuple[str, ...]
    environment_status: str


class EnumerationLimit(RuntimeError):
    pass


class _Counter:
    def __init__(self, limit: int):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("state limit must be a positive integer")
        self.limit, self.visited = limit, 0

    def tick(self) -> None:
        self.visited += 1
        if self.visited > self.limit:
            raise EnumerationLimit(f"exhaustive judge exceeded {self.limit} states")


def _usable(offer: Offer, demand: Demand, *, optimistic: bool) -> bool:
    if not (offer.supplier_active and offer.part_active and offer.relationship_active):
        return False
    if offer.source_status == "conflict":
        return optimistic
    if offer.lead_days is not None and offer.lead_days > demand.max_lead_days:
        return False
    return optimistic or offer.source_status == "verified"


def _effective_price(offer: Offer, optimistic: bool) -> int:
    if optimistic and (offer.source_status == "conflict" or offer.unit_price is None):
        return 1  # smallest admissible price, for a lower bound, never a real quotation
    assert offer.unit_price is not None
    return _price(offer.unit_price)


def _options(demand: Demand, offers: list[Offer], counter: _Counter, committed: int = 0):
    """Enumerate quantity vectors, not one fixed desired plan."""
    remaining = demand.quantity - committed
    if remaining < 0:
        return
    if remaining == 0 or (not demand.required and (demand.allow_partial or committed == 0)):
        counter.tick()
        yield (0,) * len(offers)
    if remaining == 0 or not offers:
        return
    totals = range(1, remaining + 1) if demand.allow_partial else (remaining,)
    for total in totals:
        if not demand.allow_supplier_split:
            for index in range(len(offers)):
                counter.tick()
                yield tuple(total if i == index else 0 for i in range(len(offers)))
        else:
            # Enumerate compositions recursively; avoid (quantity+1)^supplier_count
            # intermediate products, while enforcing the same explicit state cap.
            def distribute(remaining: int, count: int):
                counter.tick()
                if count == 1:
                    yield (remaining,)
                else:
                    for qty in range(remaining + 1):
                        for tail in distribute(remaining - qty, count - 1):
                            yield (qty, *tail)
            yield from distribute(total, len(offers))


def _best(problem: PlanningProblem, *, optimistic: bool, counter: _Counter) -> Objective | None:
    priorities = sorted({d.priority for d in problem.demands if not d.required})
    local = []
    if any(c.part_id not in {d.part_id for d in problem.demands} for c in problem.commitments):
        return None
    for demand in problem.demands:
        committed = [c for c in problem.commitments if c.part_id == demand.part_id]
        fixed_quantity = sum(c.quantity for c in committed)
        fixed_cost = sum(c.quantity * _price(c.unit_price) for c in committed)
        fixed_suppliers = {c.supplier_id for c in committed}
        if (any(c.lead_days > demand.max_lead_days for c in committed)
                or (not demand.allow_supplier_split and len(fixed_suppliers) > 1)):
            return None
        offers = [o for o in problem.offers if o.part_id == demand.part_id
                  and _usable(o, demand, optimistic=optimistic)
                  and (demand.allow_supplier_split or not fixed_suppliers or o.supplier_id in fixed_suppliers)]
        options = []
        for quantities in _options(demand, offers, counter, fixed_quantity):
            cost = fixed_cost + sum(q * _effective_price(o, optimistic) for o, q in zip(offers, quantities, strict=True))
            if cost <= _price(problem.budget):
                options.append((cost, fixed_quantity + sum(quantities)))
        if not options:
            return None
        local.append(options)
    best = None
    for allocation in product(*local):
        counter.tick()
        cost = sum(item[0] for item in allocation)
        if cost > _price(problem.budget):
            continue
        coverage = tuple(sum(
            item[1] for demand, item in zip(problem.demands, allocation, strict=True)
            if not demand.required and demand.priority == priority
        ) for priority in priorities)
        objective = Objective(coverage, -cost)
        if best is None or objective > best:
            best = objective
    return best


def solve(problem: PlanningProblem, *, max_states: int = 1_000_000) -> Oracle:
    counter = _Counter(max_states)
    known = _best(problem, optimistic=False, counter=counter)
    demand_map = {d.part_id: d for d in problem.demands}
    issues = tuple(sorted(o.offer_id for o in problem.offers
                          if o.source_status != "verified"
                          and sum(c.quantity for c in problem.commitments if c.part_id == o.part_id) < demand_map[o.part_id].quantity
                          and (demand_map[o.part_id].allow_supplier_split or not any(c.part_id == o.part_id for c in problem.commitments)
                               or any(c.part_id == o.part_id and c.supplier_id == o.supplier_id for c in problem.commitments))
                          and _usable(o, demand_map[o.part_id], optimistic=True)))
    upper = _best(problem, optimistic=True, counter=counter) if issues else known
    status: Literal["feasible", "infeasible", "unresolved"]
    if upper is None:
        status = "infeasible"
    elif known is None or upper > known:
        status = "unresolved"
    else:
        status = "feasible"
    return Oracle(status, known, issues, counter.visited)


def grade(problem: PlanningProblem, plan: Plan, *, max_states: int = 1_000_000) -> Grade:
    """Evaluate candidate independently of the production checker's arithmetic."""
    oracle = solve(problem, max_states=max_states)
    violations = []
    if plan.goal_id != problem.goal_id or plan.revision != problem.revision:
        violations.append("STALE_OR_FOREIGN_PLAN")
    by_id = {o.offer_id: o for o in problem.offers}
    by_part = {d.part_id: d for d in problem.demands}
    quantities = dict.fromkeys(by_part, 0)
    suppliers: dict[str, set[str]] = {part: set() for part in by_part}
    cost = 0
    for committed in problem.commitments:
        cost += committed.quantity * _price(committed.unit_price)
        if committed.part_id not in by_part:
            violations.append("COMMITTED_PART_REMOVED")
            continue
        quantities[committed.part_id] += committed.quantity
        suppliers[committed.part_id].add(committed.supplier_id)
        if committed.lead_days > by_part[committed.part_id].max_lead_days:
            violations.append("COMMITTED_DEADLINE")
    for line in plan.lines:
        offer = by_id.get(line.offer_id)
        if offer is None:
            violations.append("UNKNOWN_OFFER")
            continue
        demand = by_part[offer.part_id]
        if not _usable(offer, demand, optimistic=False):
            violations.append("UNUSABLE_OFFER")
        quantities[offer.part_id] += line.quantity
        suppliers[offer.part_id].add(offer.supplier_id)
        if offer.unit_price is None:
            violations.append("UNKNOWN_PRICE")
        else:
            cost += line.quantity * _price(offer.unit_price)
    for part, demand in by_part.items():
        total = quantities[part]
        if total > demand.quantity or (demand.required and total != demand.quantity):
            violations.append("QUANTITY")
        if not demand.required and not demand.allow_partial and total not in (0, demand.quantity):
            violations.append("PARTIAL")
        if not demand.allow_supplier_split and len(suppliers[part]) > 1:
            violations.append("SUPPLIER_SPLIT")
    if cost > _price(problem.budget):
        violations.append("BUDGET")
    feasible = not violations
    objective = Objective(tuple(sum(
        quantities[d.part_id] for d in problem.demands if not d.required and d.priority == priority
    ) for priority in sorted({d.priority for d in problem.demands if not d.required})), -cost) if feasible else None
    optimal = objective == oracle.best if feasible and oracle.status == "feasible" else None
    return Grade(feasible, optimal, bool(feasible and optimal), objective,
                 tuple(violations), oracle.status)
