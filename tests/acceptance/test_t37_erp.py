"""Real Java/H2 read boundary. Zero model calls; never substitutes seed responses."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from scripts.planning.judge import Objective, solve

from agent.planning.erp import read_erp_problem
from agent.planning.models import Demand

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fixtures import erp_service, loader  # noqa: E402

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def demands():
    public = json.loads((ROOT / "fixtures/planning/goal-v1.json").read_text(encoding="utf-8"))
    return tuple(Demand.model_validate(row) for row in public["demands"])


def test_planning_reads_real_java_relations_prices_and_deadlines(tmp_path):
    with erp_service.running_erp(tmp_path / "baseline", seed_path=loader.SEED_PATH) as erp:
        with erp.client() as client:
            p = read_erp_problem(client, goal_id="real-erp", budget="2500.00", demands=demands())
            assert solve(p).best == Objective((23,), -249350)
            assert len(p.offers) == 5
            assert all(o.supplier_id != "S003" for o in p.offers)
            assert all(o.source_ref.startswith("erp:/api/erp/v1/parts/") for o in p.offers)
            orders = client.get("/api/erp/v1/orders")
            assert orders.status_code == 200
            assert orders.json()["data"]["total"] == 0


def test_changed_java_data_changes_optimization_and_reads_actual_order_state(tmp_path):
    seed = json.loads(loader.SEED_PATH.read_text(encoding="utf-8"))
    seed["seed_id"] = "planning-t37-independent-test"
    for row in seed["supplier_parts"]:
        if row["supplier_id"] == "S002" and row["part_id"] == "P001":
            row["lead_days"] = 2
        if row["supplier_id"] == "S002" and row["part_id"] == "P004":
            row["catalog_price"] = "16.00"
    seed_path = tmp_path / "changed-seed.json"
    seed_path.write_text(json.dumps(seed), encoding="utf-8")
    with erp_service.running_erp(tmp_path / "changed", seed_path=seed_path) as erp:
        with erp.client() as client:
            p = read_erp_problem(client, goal_id="changed-erp", budget="2500.00", demands=demands())
            assert solve(p).best == Objective((29,), -249200)
            # Direct Java write is test observation, not an Actor approval bypass.
            response = client.post("/api/erp/v1/orders", headers={"X-Operation-Id": "t37-order"},
                                   json={"supplier_id": "S002", "currency": "CNY", "lines": [
                                       {"part_id": "P004", "quantity": 1, "unit_price": "16.00"}
                                   ], "note": "isolated test"})
            assert response.status_code == 201, response.text
            orders = client.get("/api/erp/v1/orders").json()["data"]
            assert orders["total"] == 1
            assert orders["items"][0]["lines"][0]["unit_price"] == "16.00"
            p_after = read_erp_problem(client, goal_id="changed-erp", budget="2500.00", demands=demands())
            assert p_after.offers != ()
            assert solve(p_after).best == solve(p).best  # ordering doesn't receive stock
