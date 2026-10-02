"""T02 acceptance: catalog queries and inventory warnings against real Java.

The suite launches the packaged Spring Boot application as its own process, on
its own file-backed H2 database, and talks to it over HTTP with the internal
service token. Two properties are proven that a Python re-implementation could
never prove:

* results come from the database (changing the data changes the answers), and
* the H2 file survives a restart while startup does not re-seed.

Integration mode: a real service is required. If it cannot start, the failure is
reported rather than skipped.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures import erp_service, loader  # noqa: E402

pytestmark = pytest.mark.integration

MONEY_RE = re.compile(r"^(?:0\.(?:0[1-9]|[1-9][0-9])|[1-9][0-9]{0,5}\.[0-9]{2})$")


@pytest.fixture(scope="module")
def erp(tmp_path_factory):
    run_dir = tmp_path_factory.mktemp("erp-baseline")
    with erp_service.running_erp(run_dir, seed_path=loader.SEED_PATH) as service:
        yield service


@pytest.fixture()
def client(erp):
    with erp.client() as http:
        yield http


def data_of(response: httpx.Response) -> dict:
    assert response.status_code == 200, f"{response.request.url} -> {response.status_code}: {response.text}"
    body = response.json()
    assert "data" in body and "request_id" in body, body
    return body


# --------------------------------------------------------------------------- #
# health and authentication
# --------------------------------------------------------------------------- #


def test_health_is_open_and_readiness_checks_the_database(erp):
    with httpx.Client(base_url=erp.base_url, timeout=20) as raw:
        live = raw.get("/api/erp/v1/health")
        ready = raw.get("/api/erp/v1/health/ready")
    assert live.status_code == 200
    assert live.json()["data"]["status"] == "ok"
    assert ready.status_code == 200
    assert ready.json()["data"]["database"] == "up"


def test_requests_without_the_service_token_are_rejected(erp):
    with httpx.Client(base_url=erp.base_url, timeout=20) as raw:
        missing = raw.get("/api/erp/v1/suppliers")
        wrong = raw.get("/api/erp/v1/suppliers", headers={"X-Service-Token": "not-the-token"})

    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "UNAUTHORIZED"
    assert wrong.status_code == 401


def test_response_envelope_echoes_the_request_id(client, erp):
    response = client.get("/api/erp/v1/suppliers", headers={"X-Request-Id": "req-acceptance-1"})
    assert response.headers.get("X-Request-Id") == "req-acceptance-1"
    assert data_of(response)["request_id"] == "req-acceptance-1"


# --------------------------------------------------------------------------- #
# suppliers
# --------------------------------------------------------------------------- #


def test_suppliers_match_the_seed(client):
    payload = data_of(client.get("/api/erp/v1/suppliers"))
    seed = loader.seed()

    assert payload["data"]["total"] == len(seed["suppliers"])
    assert [item["supplier_id"] for item in payload["data"]["items"]] == [
        supplier["supplier_id"] for supplier in seed["suppliers"]
    ]
    assert [item["name"] for item in payload["data"]["items"]] == [
        supplier["name"] for supplier in seed["suppliers"]
    ]
    assert [item["active"] for item in payload["data"]["items"]] == [True, True, False]


def test_active_filter_excludes_the_stopped_supplier(client):
    payload = data_of(client.get("/api/erp/v1/suppliers", params={"active": "true"}))
    ids = [item["supplier_id"] for item in payload["data"]["items"]]
    assert ids == ["S001", "S002"]
    assert payload["data"]["total"] == 2


def test_supplier_search_matches_a_name_substring(client):
    payload = data_of(client.get("/api/erp/v1/suppliers", params={"q": "远航"}))
    assert payload["data"]["total"] == 1
    assert payload["data"]["items"][0]["supplier_id"] == "S002"


# --------------------------------------------------------------------------- #
# parts and supply relations
# --------------------------------------------------------------------------- #


def test_parts_can_be_searched_by_sku(client):
    payload = data_of(client.get("/api/erp/v1/parts", params={"q": "CHAIN"}))
    assert payload["data"]["total"] == 1
    assert payload["data"]["items"][0]["part_id"] == "P003"


def test_part_detail_lists_only_active_suppliers_cheapest_first(client):
    payload = data_of(client.get("/api/erp/v1/parts/P001"))
    offers = payload["data"]["available_suppliers"]

    assert payload["data"]["part"]["sku"] == "BRAKE-01"
    # S003 also sells P001 but is stopped, so it must not be offered.
    assert [offer["supplier_id"] for offer in offers] == ["S002", "S001"]
    assert [offer["catalog_price"] for offer in offers] == ["24.00", "25.50"]


def test_part_detail_for_a_part_with_one_supplier(client):
    payload = data_of(client.get("/api/erp/v1/parts/P003"))
    offers = payload["data"]["available_suppliers"]
    assert [offer["supplier_id"] for offer in offers] == ["S001"]
    assert offers[0]["catalog_price"] == "68.00"


def test_unknown_part_returns_404_part_not_found(client):
    response = client.get("/api/erp/v1/parts/P999")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PART_NOT_FOUND"


def test_unknown_supplier_returns_404_supplier_not_found(client):
    response = client.get("/api/erp/v1/suppliers/S999/parts")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SUPPLIER_NOT_FOUND"


def test_supplier_parts_exclude_parts_without_a_supply_relation(client):
    payload = data_of(client.get("/api/erp/v1/suppliers/S002/parts"))
    part_ids = [item["part_id"] for item in payload["data"]["items"]]

    seed_pairs = {
        (row["supplier_id"], row["part_id"]) for row in loader.seed()["supplier_parts"]
    }
    expected = sorted(
        part_id for supplier_id, part_id in seed_pairs if supplier_id == "S002"
    )
    assert part_ids == expected
    assert "P003" not in part_ids, "S002 与 P003 没有供货关系"


# --------------------------------------------------------------------------- #
# inventory warnings
# --------------------------------------------------------------------------- #


def test_inventory_warnings_match_the_fixed_expectations(client):
    payload = data_of(client.get("/api/erp/v1/inventory/warnings"))
    expected = loader.expected()["inventory_warning"]

    items = payload["data"]["items"]
    assert payload["data"]["total"] == 3
    assert [item["part_id"] for item in items] == expected["part_ids"]
    assert {item["part_id"]: item["suggested_quantity"] for item in items} == (
        expected["suggested_quantity"]
    )


def test_warning_rows_below_target_but_not_warned_are_absent(client):
    """P002 and P005 sit below target stock yet are not warnings; suggestions follow warnings."""
    payload = data_of(client.get("/api/erp/v1/inventory/warnings"))
    reported = {item["part_id"] for item in payload["data"]["items"]}
    seed = loader.seed()

    below_target_but_healthy = {
        row["part_id"]
        for row in seed["inventory"]
        if row["on_hand"] >= row["warning_threshold"] and row["on_hand"] < row["target_stock"]
    }
    assert below_target_but_healthy, "seed 应保留至少一个不预警但低于目标的物料"
    assert not (reported & below_target_but_healthy)


def test_warning_rows_carry_the_inventory_fields(client):
    payload = data_of(client.get("/api/erp/v1/inventory/warnings"))
    seed_index = {row["part_id"]: row for row in loader.seed()["inventory"]}

    for item in payload["data"]["items"]:
        seed_row = seed_index[item["part_id"]]
        assert item["on_hand"] == seed_row["on_hand"]
        assert item["warning_threshold"] == seed_row["warning_threshold"]
        assert item["target_stock"] == seed_row["target_stock"]
        assert item["suggested_quantity"] == max(
            seed_row["target_stock"] - seed_row["on_hand"], 0
        )


# --------------------------------------------------------------------------- #
# pagination and money encoding
# --------------------------------------------------------------------------- #


def test_pagination_is_deterministic_and_reports_the_full_total(client):
    first = data_of(client.get("/api/erp/v1/parts", params={"page": 1, "page_size": 2}))
    second = data_of(client.get("/api/erp/v1/parts", params={"page": 2, "page_size": 2}))
    beyond = data_of(client.get("/api/erp/v1/parts", params={"page": 9, "page_size": 2}))

    assert [item["part_id"] for item in first["data"]["items"]] == ["P001", "P002"]
    assert [item["part_id"] for item in second["data"]["items"]] == ["P003", "P004"]
    assert first["data"]["total"] == second["data"]["total"] == 5
    assert beyond["data"]["items"] == []
    assert beyond["data"]["total"] == 5


def test_page_size_boundaries_are_rejected(client):
    for params in ({"page": 0}, {"page": -3}, {"page_size": 0}, {"page_size": 101}):
        response = client.get("/api/erp/v1/parts", params=params)
        assert response.status_code == 400, params
        assert response.json()["error"]["code"] == "INVALID_ARGUMENT"


def test_money_fields_are_two_decimal_strings(client):
    for part_id in ("P001", "P002", "P003", "P004"):
        payload = data_of(client.get(f"/api/erp/v1/parts/{part_id}"))
        for offer in payload["data"]["available_suppliers"]:
            assert isinstance(offer["catalog_price"], str), offer
            assert MONEY_RE.match(offer["catalog_price"]), offer

    page = data_of(client.get("/api/erp/v1/suppliers/S001/parts"))
    for item in page["data"]["items"]:
        assert MONEY_RE.match(item["catalog_price"]), item


# --------------------------------------------------------------------------- #
# the data really comes from the database
# --------------------------------------------------------------------------- #


def test_warnings_track_the_seeded_data_rather_than_a_fixed_reply(tmp_path: Path):
    """Seed the same schema with different stock and watch the warning set move.

    A hardcoded reply would keep returning P001/P003/P004 regardless of the data.
    """
    modified = json.loads(loader.SEED_PATH.read_text(encoding="utf-8"))
    for row in modified["inventory"]:
        if row["part_id"] == "P002":
            row["on_hand"] = 5  # threshold 15, target 50 -> new warning
        if row["part_id"] == "P001":
            row["on_hand"] = 500  # no longer a warning
    modified["seed_id"] = "seed-v1-modified-for-t02"
    seed_file = tmp_path / "modified-seed.json"
    seed_file.write_text(json.dumps(modified, ensure_ascii=False), encoding="utf-8")

    with erp_service.running_erp(tmp_path / "run", seed_path=seed_file) as service:
        with service.client() as http:
            payload = data_of(http.get("/api/erp/v1/inventory/warnings"))

    items = payload["data"]["items"]
    assert [item["part_id"] for item in items] == ["P002", "P003", "P004"]
    by_id = {item["part_id"]: item["suggested_quantity"] for item in items}
    assert by_id["P002"] == 45
    assert "P001" not in by_id


def test_data_survives_a_restart_and_startup_does_not_reseed(tmp_path: Path):
    """Restart with a different seed file: the existing database must win.

    If startup re-seeded, the renamed supplier would appear. If the database did
    not persist, it would be empty or recreated.
    """
    run_dir = tmp_path / "restart"
    modified = json.loads(loader.SEED_PATH.read_text(encoding="utf-8"))
    for supplier in modified["suppliers"]:
        supplier["name"] = "重启后被改写的名称-" + supplier["supplier_id"]
    modified["seed_id"] = "seed-v1-should-be-ignored"
    changed_seed = tmp_path / "changed-seed.json"
    changed_seed.write_text(json.dumps(modified, ensure_ascii=False), encoding="utf-8")

    with erp_service.running_erp(run_dir, seed_path=loader.SEED_PATH) as first:
        with first.client() as http:
            before = data_of(http.get("/api/erp/v1/suppliers"))
        assert first.database_files(), f"H2 文件库应当真实落盘: {first.db_path}"

    with erp_service.running_erp(run_dir, seed_path=changed_seed) as second:
        with second.client() as http:
            after = data_of(http.get("/api/erp/v1/suppliers"))

    baseline_names = [supplier["name"] for supplier in loader.seed()["suppliers"]]
    assert [item["name"] for item in before["data"]["items"]] == baseline_names
    assert [item["name"] for item in after["data"]["items"]] == baseline_names, (
        "重启后被重新播种，说明启动重置了数据"
    )
    assert after["data"]["total"] == before["data"]["total"] == 3


def test_acceptance_never_uses_the_developer_database(erp):
    """The suite must own its database file, never the one under erp/data."""
    developer_data_dir = (Path(erp_service.ERP_DIR) / "data").resolve()
    assert not erp.db_path.resolve().is_relative_to(developer_data_dir), (
        f"验收入口使用了开发库路径 {erp.db_path}"
    )
    assert erp.database_files(), "测试库应当真实落盘，而不是内存库"
