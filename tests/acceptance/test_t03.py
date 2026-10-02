"""T03 acceptance: order transactions, optimistic versioning and idempotent replay.

The suite drives the packaged Java service over HTTP. Each test uses its own
actor id, so "exactly one order" assertions stay local and independent of test
order. Concurrency is exercised with real overlapping threads and a barrier —
never with sleeps that hope the race happened.
"""

from __future__ import annotations

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures import erp_service, loader  # noqa: E402

pytestmark = pytest.mark.integration

ORDERS = "/api/erp/v1/orders"


@pytest.fixture(scope="module")
def erp(tmp_path_factory):
    run_dir = tmp_path_factory.mktemp("erp-orders")
    with erp_service.running_erp(run_dir, seed_path=loader.SEED_PATH) as service:
        yield service


def create_body(
    *,
    supplier_id: str = "S001",
    part_id: str = "P001",
    quantity: int = 50,
    unit_price: str = "25.50",
    note: str = "演示采购",
) -> bytes:
    payload = {
        "supplier_id": supplier_id,
        "currency": "CNY",
        "lines": [{"part_id": part_id, "quantity": quantity, "unit_price": unit_price}],
        "note": note,
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def update_body(*, expected_version: int = 1, quantity: int = 60) -> bytes:
    payload = {
        "expected_version": expected_version,
        "supplier_id": "S001",
        "currency": "CNY",
        "lines": [{"part_id": "P001", "quantity": quantity, "unit_price": "25.50"}],
        "note": "调整数量",
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def call(
    erp: erp_service.ErpService,
    method: str,
    path: str,
    *,
    actor: str,
    operation_id: str | None = None,
    body: bytes | None = None,
    params: dict | None = None,
    request_id: str | None = None,
) -> httpx.Response:
    """One request with its own client, so threads never share connection state."""
    headers = erp.headers(actor=actor, request_id=request_id)
    if operation_id is not None:
        headers["X-Operation-Id"] = operation_id
    if body is not None:
        headers["Content-Type"] = "application/json"
    with httpx.Client(base_url=erp.base_url, timeout=httpx.Timeout(60.0, connect=10.0)) as http:
        return http.request(method, path, headers=headers, content=body, params=params)


def post_order(erp, actor: str, operation_id: str, body: bytes) -> httpx.Response:
    return call(erp, "POST", ORDERS, actor=actor, operation_id=operation_id, body=body)


def put_order(erp, actor: str, operation_id: str, order_id: str, body: bytes) -> httpx.Response:
    return call(erp, "PUT", f"{ORDERS}/{order_id}", actor=actor, operation_id=operation_id, body=body)


def order_count(erp, actor: str) -> int:
    response = call(erp, "GET", ORDERS, actor=actor)
    assert response.status_code == 200, response.text
    return response.json()["data"]["total"]


def error_code(response: httpx.Response) -> str:
    return response.json()["error"]["code"]


def inventory_on_hand(erp, part_id: str) -> int:
    """Read stock for a part that the seed places in the warning set (P001 is one)."""
    response = call(erp, "GET", "/api/erp/v1/inventory/warnings", actor="demo-a")
    assert response.status_code == 200, response.text
    for item in response.json()["data"]["items"]:
        if item["part_id"] == part_id:
            return int(item["on_hand"])
    raise AssertionError(f"{part_id} 不在预警集合内，无法通过该端点读取库存")


# --------------------------------------------------------------------------- #
# creation
# --------------------------------------------------------------------------- #


def test_creating_an_order_matches_the_fixed_total(erp):
    body = create_body(quantity=50, unit_price="25.50")
    response = post_order(erp, "acc-create", "acc-op-create-1", body)

    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["total_amount"] == "1275.00"
    assert data["version"] == 1
    assert data["supplier_id"] == "S001"
    assert data["currency"] == "CNY"
    assert data["lines"][0]["line_amount"] == "1275.00"
    assert response.headers.get("Idempotent-Replayed") == "false"


def test_creating_an_order_never_changes_inventory(erp):
    before = inventory_on_hand(erp, "P001")
    post_order(erp, "acc-stock", "acc-op-stock", create_body(quantity=7, unit_price="25.50"))
    after = inventory_on_hand(erp, "P001")

    assert after == before == 8, "本 Demo 下单不改变库存"


def test_multi_line_order_matches_the_fixed_total(erp):
    payload = {
        "supplier_id": "S001",
        "currency": "CNY",
        "lines": [
            {"part_id": "P001", "quantity": 2, "unit_price": "25.50"},
            {"part_id": "P002", "quantity": 3, "unit_price": "12.00"},
        ],
    }
    response = post_order(
        erp,
        "acc-multiline",
        "acc-op-multiline",
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )
    assert response.status_code == 201, response.text
    assert response.json()["data"]["total_amount"] == "87.00"


def test_a_created_order_is_retrievable_by_its_owner(erp):
    created = post_order(erp, "acc-query", "acc-op-query", create_body())
    order_id = created.json()["data"]["order_id"]

    response = call(erp, "GET", ORDERS, actor="acc-query", params={"order_id": order_id})
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["total"] == 1
    assert payload["items"][0]["order_id"] == order_id
    assert payload["items"][0]["lines"][0]["quantity"] == 50


def test_orders_are_listed_newest_first(erp):
    first = post_order(erp, "acc-order", "acc-op-order-1", create_body(quantity=1))
    second = post_order(erp, "acc-order", "acc-op-order-2", create_body(quantity=2))

    response = call(erp, "GET", ORDERS, actor="acc-order")
    ids = [item["order_id"] for item in response.json()["data"]["items"]]

    assert ids == [
        second.json()["data"]["order_id"],
        first.json()["data"]["order_id"],
    ]


# --------------------------------------------------------------------------- #
# update and versioning
# --------------------------------------------------------------------------- #


def test_updating_with_the_current_version_bumps_to_version_2(erp):
    created = post_order(erp, "acc-update", "acc-op-update-create", create_body())
    order_id = created.json()["data"]["order_id"]

    response = put_order(erp, "acc-update", "acc-op-update-1", order_id, update_body(expected_version=1))

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["version"] == 2
    assert data["total_amount"] == "1530.00"
    assert data["lines"][0]["quantity"] == 60
    assert data["order_no"] == created.json()["data"]["order_no"], "order_no 不随修改变化"


def test_a_stale_version_is_rejected_with_409(erp):
    created = post_order(erp, "acc-stale", "acc-op-stale-create", create_body())
    order_id = created.json()["data"]["order_id"]

    ok = put_order(erp, "acc-stale", "acc-op-stale-1", order_id, update_body(expected_version=1))
    assert ok.status_code == 200

    stale = put_order(erp, "acc-stale", "acc-op-stale-2", order_id, update_body(expected_version=1))
    assert stale.status_code == 409
    assert error_code(stale) == "VERSION_CONFLICT"
    assert stale.json()["error"]["details"]["current_version"] == 2


def test_two_edits_from_the_same_version_leave_one_winner(erp):
    created = post_order(erp, "acc-version-race", "acc-op-vr-create", create_body())
    order_id = created.json()["data"]["order_id"]
    barrier = threading.Barrier(2)

    def edit(operation_id: str) -> httpx.Response:
        barrier.wait(timeout=30)
        return put_order(erp, "acc-version-race", operation_id, order_id, update_body(expected_version=1))

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(edit, ["acc-op-vr-a", "acc-op-vr-b"]))

    statuses = sorted(response.status_code for response in responses)
    assert statuses == [200, 409], [response.text for response in responses]
    loser = next(response for response in responses if response.status_code == 409)
    assert error_code(loser) == "VERSION_CONFLICT"

    final = call(erp, "GET", ORDERS, actor="acc-version-race", params={"order_id": order_id})
    assert final.json()["data"]["items"][0]["version"] == 2
    assert order_count(erp, "acc-version-race") == 1


def test_updating_without_expected_version_is_rejected(erp):
    created = post_order(erp, "acc-noversion", "acc-op-nv-create", create_body())
    order_id = created.json()["data"]["order_id"]
    body = json.dumps(
        {
            "supplier_id": "S001",
            "currency": "CNY",
            "lines": [{"part_id": "P001", "quantity": 60, "unit_price": "25.50"}],
        },
        ensure_ascii=False,
    ).encode("utf-8")

    response = put_order(erp, "acc-noversion", "acc-op-nv", order_id, body)
    assert response.status_code == 400
    assert error_code(response) == "INVALID_ARGUMENT"


def test_an_update_requires_the_same_actor_who_owns_the_order(erp):
    created = post_order(erp, "acc-owner", "acc-op-owner", create_body())
    order_id = created.json()["data"]["order_id"]

    stolen = put_order(erp, "acc-thief", "acc-op-thief", order_id, update_body(expected_version=1))
    assert stolen.status_code == 404, "其他用户看到的是不存在，而不是无权"
    assert error_code(stolen) == "ORDER_NOT_FOUND"
    assert order_count(erp, "acc-thief") == 0


# --------------------------------------------------------------------------- #
# idempotency
# --------------------------------------------------------------------------- #


def test_resending_identical_bytes_replays_instead_of_duplicating(erp):
    body = create_body()
    first = post_order(erp, "acc-replay", "acc-op-replay", body)
    second = post_order(erp, "acc-replay", "acc-op-replay", body)

    assert first.status_code == second.status_code == 201
    assert first.json()["data"]["order_id"] == second.json()["data"]["order_id"]
    assert second.headers.get("Idempotent-Replayed") == "true"
    assert order_count(erp, "acc-replay") == 1


def test_the_same_operation_id_with_different_content_is_a_conflict(erp):
    post_order(erp, "acc-mismatch", "acc-op-mismatch", create_body(quantity=50))

    conflicting = post_order(erp, "acc-mismatch", "acc-op-mismatch", create_body(quantity=51))
    assert conflicting.status_code == 409
    assert error_code(conflicting) == "IDEMPOTENCY_CONFLICT"
    assert order_count(erp, "acc-mismatch") == 1


def test_an_operation_id_cannot_be_reused_across_operation_types(erp):
    created = post_order(erp, "acc-cross", "acc-op-cross", create_body())
    order_id = created.json()["data"]["order_id"]

    misuse = put_order(erp, "acc-cross", "acc-op-cross", order_id, update_body(expected_version=1))
    assert misuse.status_code == 409
    assert error_code(misuse) == "IDEMPOTENCY_CONFLICT"


def test_two_threads_with_the_same_key_create_exactly_one_order(erp):
    body = create_body()
    barrier = threading.Barrier(2)

    def send(_: int) -> httpx.Response:
        barrier.wait(timeout=30)
        return post_order(erp, "acc-race", "acc-op-race", body)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(send, [0, 1]))

    assert [response.status_code for response in responses] == [201, 201], [
        response.text for response in responses
    ]
    order_ids = {response.json()["data"]["order_id"] for response in responses}
    assert len(order_ids) == 1
    fresh = [r for r in responses if r.headers.get("Idempotent-Replayed") == "false"]
    assert len(fresh) == 1, "只能有一次真正的业务写"
    assert order_count(erp, "acc-race") == 1


def test_two_threads_with_the_same_key_but_different_bodies_conflict_once(erp):
    barrier = threading.Barrier(2)

    def send(quantity: int) -> httpx.Response:
        barrier.wait(timeout=30)
        return post_order(erp, "acc-race-diff", "acc-op-race-diff", create_body(quantity=quantity))

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(send, [50, 51]))

    statuses = sorted(response.status_code for response in responses)
    assert statuses == [201, 409], [response.text for response in responses]
    loser = next(response for response in responses if response.status_code == 409)
    assert error_code(loser) == "IDEMPOTENCY_CONFLICT"
    assert order_count(erp, "acc-race-diff") == 1


# --------------------------------------------------------------------------- #
# validation, and proof that rejections write nothing
# --------------------------------------------------------------------------- #


def test_business_rule_violations_are_rejected(erp):
    cases = [
        ("acc-inactive-supplier", create_body(supplier_id="S003", unit_price="26.00"), 422, "INACTIVE_SUPPLIER"),
        ("acc-unsupported-pair", create_body(part_id="P003", unit_price="68.00", supplier_id="S002"), 422, "UNSUPPORTED_PART"),
        ("acc-inactive-part", create_body(part_id="P005", unit_price="5.00"), 422, "UNSUPPORTED_PART"),
        ("acc-unknown-part", create_body(part_id="P999", unit_price="5.00"), 404, "PART_NOT_FOUND"),
        ("acc-unknown-supplier", create_body(supplier_id="S999"), 404, "SUPPLIER_NOT_FOUND"),
    ]

    for actor, body, expected_status, expected_code in cases:
        response = post_order(erp, actor, f"acc-op-{actor}", body)
        assert response.status_code == expected_status, (actor, response.text)
        assert error_code(response) == expected_code, actor
        assert order_count(erp, actor) == 0, f"{actor} 不得留下订单"


def test_invalid_amounts_quantities_and_duplicates_are_rejected(erp):
    duplicate = json.dumps(
        {
            "supplier_id": "S001",
            "currency": "CNY",
            "lines": [
                {"part_id": "P001", "quantity": 1, "unit_price": "25.50"},
                {"part_id": "P001", "quantity": 2, "unit_price": "25.50"},
            ],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    with_total = json.dumps(
        {
            "supplier_id": "S001",
            "currency": "CNY",
            "total_amount": "1.00",
            "lines": [{"part_id": "P001", "quantity": 1, "unit_price": "25.50"}],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    bad_currency = json.dumps(
        {
            "supplier_id": "S001",
            "currency": "USD",
            "lines": [{"part_id": "P001", "quantity": 1, "unit_price": "25.50"}],
        },
        ensure_ascii=False,
    ).encode("utf-8")

    cases = [
        ("acc-dup", duplicate),
        ("acc-3dp", create_body(unit_price="25.505")),
        ("acc-zero", create_body(quantity=0)),
        ("acc-toobig", create_body(quantity=10001)),
        ("acc-negprice", create_body(unit_price="0.00")),
        ("acc-total", with_total),
        ("acc-currency", bad_currency),
    ]

    for actor, body in cases:
        response = post_order(erp, actor, f"acc-op-{actor}", body)
        assert response.status_code == 400, (actor, response.status_code, response.text)
        assert error_code(response) == "INVALID_ARGUMENT", actor
        assert order_count(erp, actor) == 0, actor


def test_a_write_without_an_operation_id_is_rejected(erp):
    response = call(erp, "POST", ORDERS, actor="acc-noop", body=create_body())
    assert response.status_code == 400
    assert error_code(response) == "INVALID_ARGUMENT"
    assert order_count(erp, "acc-noop") == 0


def test_a_write_without_a_trusted_actor_is_rejected(erp):
    with httpx.Client(
        base_url=erp.base_url,
        headers={"X-Service-Token": erp.token, "X-Operation-Id": "acc-op-noactor"},
        timeout=30,
    ) as http:
        response = http.post(
            ORDERS, content=create_body(), headers={"Content-Type": "application/json"}
        )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_a_failed_update_leaves_the_order_completely_unchanged(erp):
    created = post_order(erp, "acc-rollback", "acc-op-rb-create", create_body())
    order_id = created.json()["data"]["order_id"]

    failed = put_order(erp, "acc-rollback", "acc-op-rb", order_id, update_body(expected_version=99))
    assert failed.status_code == 409

    current = call(erp, "GET", ORDERS, actor="acc-rollback", params={"order_id": order_id})
    item = current.json()["data"]["items"][0]
    assert item["version"] == 1
    assert item["total_amount"] == "1275.00"
    assert len(item["lines"]) == 1
    assert item["lines"][0]["quantity"] == 50


def test_a_failed_write_does_not_consume_its_operation_id(erp):
    created = post_order(erp, "acc-retry", "acc-op-retry-create", create_body())
    order_id = created.json()["data"]["order_id"]

    failed = put_order(erp, "acc-retry", "acc-op-retry", order_id, update_body(expected_version=77))
    assert failed.status_code == 409

    retried = put_order(erp, "acc-retry", "acc-op-retry", order_id, update_body(expected_version=1))
    assert retried.status_code == 200, "回滚后同一 operation id 仍应可用"
    assert retried.json()["data"]["version"] == 2


def test_pagination_applies_to_order_search(erp):
    for index in range(3):
        post_order(erp, "acc-page", f"acc-op-page-{index}", create_body(quantity=index + 1))

    first = call(erp, "GET", ORDERS, actor="acc-page", params={"page": 1, "page_size": 2})
    second = call(erp, "GET", ORDERS, actor="acc-page", params={"page": 2, "page_size": 2})

    assert first.json()["data"]["total"] == 3
    assert len(first.json()["data"]["items"]) == 2
    assert len(second.json()["data"]["items"]) == 1
    ids = [item["order_id"] for item in first.json()["data"]["items"] + second.json()["data"]["items"]]
    assert len(set(ids)) == 3
