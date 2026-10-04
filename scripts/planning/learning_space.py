"""Control-plane learning-space and sanitized feedback validator.

This module is deliberately outside ``src`` and is never uploaded to an Actor
sandbox. It describes observable rule conditions, then asks the independent
judge for environment status. It does not generate an answer for an Actor.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# The report is also a documented CLI, so make direct ``python scripts/...``
# execution use the repository source tree without changing the host environment.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from scripts.planning.judge import grade, solve  # noqa: E402

from agent.planning.models import Demand, Offer, Plan, PlanningProblem  # noqa: E402

CASE_PATH = ROOT / "fixtures/planning/decision-cases-v1.json"
GOAL_PATH = ROOT / "fixtures/planning/goal-v1.json"
SEED_PATH = ROOT / "fixtures/seed-v1.json"


@dataclass(frozen=True)
class RuleAtom:
    rule_id: str
    condition: str
    operation: str
    evidence: str


@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    atoms: tuple[str, ...]
    expected_operation: str
    source: str


@dataclass(frozen=True)
class Counterfactual:
    pair_id: str
    left: str
    right: str
    changed: str
    left_operation: str
    right_operation: str


RULES = (
    RuleAtom("R-DEADLINE", "price and lead time conflict", "check_feasibility_before_price", "erp:lead_days"),
    RuleAtom("R-BUDGET", "budget limits optional coverage", "recompute_affected_allocation", "goal:budget"),
    RuleAtom("R-SOURCE", "required evidence is missing", "clarify_missing_source", "offer:source_status"),
    RuleAtom("R-PARTIAL", "partial fulfilment is forbidden", "reject_partial_quantity", "goal:allow_partial"),
)

CASE_ATOMS: dict[str, tuple[str, ...]] = {
    "deadline-conflict": ("R-DEADLINE",),
    "budget-partial": ("R-BUDGET",),
    "source-missing": ("R-SOURCE",),
    "no-partial": ("R-PARTIAL",),
    "deadline-budget": ("R-DEADLINE", "R-BUDGET"),
    "deadline-budget-missing": ("R-DEADLINE", "R-BUDGET", "R-SOURCE"),
    "sufficient-budget": (),
    "business-infeasible": ("R-BUDGET", "R-DEADLINE"),
}

EXPECTED_OPERATION = {
    "deadline-conflict": "check_feasibility_before_price",
    "budget-partial": "recompute_affected_allocation",
    "source-missing": "clarify_missing_source",
    "no-partial": "reject_partial_quantity",
    "deadline-budget": "recompute_affected_allocation",
    "deadline-budget-missing": "clarify_missing_source",
    "sufficient-budget": "do_not_ask_unnecessary",
    "business-infeasible": "reject_after_infeasibility",
}

COUNTERFACTUALS = (
    Counterfactual("cf-source", "source-missing", "sufficient-budget", "source_status", "clarify_missing_source", "do_not_ask_unnecessary"),
    Counterfactual("cf-budget", "budget-partial", "sufficient-budget", "budget", "recompute_affected_allocation", "do_not_ask_unnecessary"),
    Counterfactual("cf-partial", "no-partial", "budget-partial", "allow_partial", "reject_partial_quantity", "recompute_affected_allocation"),
    Counterfactual("cf-feasibility", "business-infeasible", "sufficient-budget", "budget", "reject_after_infeasibility", "do_not_ask_unnecessary"),
    Counterfactual("cf-deadline", "deadline-conflict", "sufficient-budget", "deadline", "check_feasibility_before_price", "do_not_ask_unnecessary"),
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cases() -> list[CaseSpec]:
    raw = json.loads(CASE_PATH.read_text(encoding="utf-8"))
    ids = {item["case_id"] for item in raw["cases"]}
    if ids != set(CASE_ATOMS) or ids != set(EXPECTED_OPERATION):
        raise ValueError("decision case registry and learning matrix differ")
    return [CaseSpec(item["case_id"], CASE_ATOMS[item["case_id"]],
                     EXPECTED_OPERATION[item["case_id"]], f"fixtures/planning/decision-cases-v1.json#{item['case_id']}")
            for item in raw["cases"]]


def _problem(case_id: str) -> PlanningProblem:
    public = json.loads(GOAL_PATH.read_text(encoding="utf-8"))
    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    suppliers = {row["supplier_id"]: row for row in seed["suppliers"]}
    parts = {row["part_id"]: row for row in seed["parts"]}
    case = next(item for item in json.loads(CASE_PATH.read_text(encoding="utf-8"))["cases"] if item["case_id"] == case_id)
    demands = []
    for item in public["demands"]:
        item = dict(item)
        if item["part_id"] == "P001":
            item["max_lead_days"] = case["p001_deadline"]
        if not item["required"]:
            item["allow_partial"] = case["allow_partial"]
        demands.append(Demand.model_validate(item))
    details = {}
    for demand in demands:
        details[demand.part_id] = {"part": parts[demand.part_id], "available_suppliers": [
            row for row in seed["supplier_parts"] if row["part_id"] == demand.part_id
            and suppliers[row["supplier_id"]]["active"]
        ]}
    offers = []
    for demand in demands:
        for row in details[demand.part_id]["available_suppliers"]:
            row = dict(row)
            if case["source_status"] == "missing" and demand.part_id == "P001":
                row["catalog_price"], row["lead_days"] = None, None
            offers.append(Offer(
                offer_id=f"{row['supplier_id']}:{demand.part_id}", supplier_id=row["supplier_id"],
                part_id=demand.part_id, supplier_active=True, relationship_active=True,
                part_active=parts[demand.part_id]["active"], unit_price=row.get("catalog_price"),
                lead_days=row.get("lead_days"), source_ref=f"fixture:decision:{case_id}:{demand.part_id}",
                source_status="missing" if row.get("catalog_price") is None or row.get("lead_days") is None else "verified"))
    return PlanningProblem(goal_id=public["goal_id"], revision=1,
        budget=case["budget"], demands=tuple(demands), offers=tuple(offers))


def _check_matrix(cases: list[CaseSpec]) -> None:
    known = {rule.rule_id for rule in RULES}
    if len({case.case_id for case in cases}) != len(cases) or any(
        atom not in known for case in cases for atom in case.atoms
    ):
        raise ValueError("duplicate case or unknown rule atom")
    train = [c for c in cases if len(c.atoms) <= 1 and c.case_id != "sufficient-budget"]
    test = [c for c in cases if c not in train]
    train_sets = {frozenset(c.atoms) for c in train}
    if {frozenset(c.atoms) for c in test if len(c.atoms) >= 2} & train_sets:
        raise ValueError("test combination appeared in training")
    if {a.rule_id for a in RULES} - {a for c in train for a in c.atoms}:
        raise ValueError("training does not cover every rule atom")
    if len({c.expected_operation for c in cases}) < 6:
        raise ValueError("operation labels do not distinguish the task space")


def counterfactual_problems(pair: Counterfactual) -> tuple[PlanningProblem, PlanningProblem]:
    """Change one declared condition group and preserve every other public field."""
    left = _problem(pair.left)
    reference = _problem(pair.right)
    data = left.model_dump(mode="json")
    if pair.changed == "budget":
        data["budget"] = reference.budget
    elif pair.changed == "deadline":
        by_part = {d.part_id: d for d in reference.demands}
        data["demands"] = [{**d, "max_lead_days": by_part[d["part_id"]].max_lead_days}
                           for d in data["demands"]]
    elif pair.changed == "allow_partial":
        data["demands"] = [{**d, "allow_partial": True} if not d["required"] else d
                           for d in data["demands"]]
    elif pair.changed == "source_status":
        by_id = {offer.offer_id: offer for offer in reference.offers}
        data["offers"] = [{**o, "source_status": by_id[o["offer_id"]].source_status,
            "unit_price": by_id[o["offer_id"]].unit_price,
            "lead_days": by_id[o["offer_id"]].lead_days} for o in data["offers"]]
    else:
        raise ValueError("unknown counterfactual condition group")
    right = PlanningProblem.model_validate(data)
    if left == right:
        raise ValueError("counterfactual did not change the environment")
    return left, right


def sanitized_feedback(case_id: str, plan: Plan) -> dict[str, Any]:
    """Return only a whitelist; never return the private objective or answer."""
    result = grade(_problem(case_id), plan)
    categories = set(result.violations)
    if result.feasible and result.optimal is False:
        categories.add("SUBOPTIMAL")
    if result.environment_status == "unresolved":
        categories.add("SOURCE_UNRESOLVED")
    return {
        "schema_version": 1, "success": result.accepted,
        "constraint_status": "feasible" if result.feasible else "infeasible",
        "environment_status": result.environment_status,
        "error_categories": sorted(categories),
        "rule_ids": list(CASE_ATOMS[case_id]),
        "evidence_refs": [f"fixtures/planning/decision-cases-v1.json#{case_id}"],
    }


def build_report() -> dict[str, Any]:
    cases = _cases()
    _check_matrix(cases)
    oracle_status = {c.case_id: asdict(solve(_problem(c.case_id))) for c in cases}
    counterfactuals = []
    for pair in COUNTERFACTUALS:
        left, right = counterfactual_problems(pair)
        first, second = solve(left), solve(right)
        if first.status == second.status and first.best == second.best:
            raise ValueError("counterfactual does not change independent status or optimum")
        counterfactuals.append({**asdict(pair), "left_oracle": asdict(first), "right_oracle": asdict(second),
            "left_problem_sha256": hashlib.sha256(left.model_dump_json().encode()).hexdigest(),
            "right_problem_sha256": hashlib.sha256(right.model_dump_json().encode()).hexdigest()})
    report = {
        "schema_version": 1, "kind": "learning-space-baseline", "model_calls": 0,
        "rules": [asdict(rule) for rule in RULES],
        "train": [asdict(c) for c in cases if len(c.atoms) <= 1 and c.case_id != "sufficient-budget"],
        "test": [asdict(c) for c in cases if not (len(c.atoms) <= 1 and c.case_id != "sufficient-budget")],
        "counterfactuals": counterfactuals,
        "oracle_status": oracle_status,
        "source_hashes": {path.relative_to(ROOT).as_posix(): _digest(path) for path in (CASE_PATH, GOAL_PATH, SEED_PATH)},
        "feedback_whitelist": ["success", "constraint_status", "environment_status", "error_categories", "rule_ids", "evidence_refs"],
        "claims": {"proves_condition_sensitivity": True, "proves_actor_learning_space": False,
                   "proves_learning_gain": False, "curator_run": False},
    }
    # Store exactly the JSON shape that the CLI writes; tuple/list differences
    # must not make a report change when rebuilt on another process.
    report = json.loads(json.dumps(report, ensure_ascii=False))
    report["report_sha256"] = hashlib.sha256(json.dumps(report, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("validate",))
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = build_report()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "report": str(output), "report_sha256": report["report_sha256"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
