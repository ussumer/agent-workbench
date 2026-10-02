"""T04 acceptance: the eight ERP tools over real MCP Streamable HTTP.

The gateway and the ERP both run as separate processes. Tools are listed and
called through the official MCP client, so this suite proves protocol-level
integration rather than in-process function calls.

Approval is enforced at execution: writes require a signed grant bound to the
owner, the tool, the target order, the operation id and the SHA-256 of the frozen
request body. The tests below mint real grants with the same code the approval
service will use, and check that every mismatch is refused.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures import erp_service, loader, mcp_service  # noqa: E402
from mcp_server.grants import issue_grant  # noqa: E402
from mcp_server.tools.registry import (  # noqa: E402
    ERP_TOOLS,
    OrderLineInput,
    canonical_order_body,
    canonical_update_body,
    create_target,
    update_target,
)

pytestmark = pytest.mark.integration

SNAPSHOT_PATH = loader.FIXTURES_DIR / "mcp-tools.snapshot.json"
ACTOR_HEADER = "x-actor-id"
GRANT_HEADER = "x-approval-grant"


# --------------------------------------------------------------------------- #
# MCP plumbing
# --------------------------------------------------------------------------- #


@contextlib.asynccontextmanager
async def mcp_session(
    gateway: mcp_service.McpGateway, actor: str, grant: str | None = None
) -> AsyncIterator[ClientSession]:
    """One MCP session carrying a fixed caller identity (and optional grant).

    Headers ride on a dedicated httpx client rather than on shared defaults, so
    two users cannot inherit each other's identity. ``trust_env=False`` keeps an
    ambient HTTP proxy out of the loopback traffic.
    """
    headers = {ACTOR_HEADER: actor}
    if grant is not None:
        headers[GRANT_HEADER] = grant
    async with httpx.AsyncClient(
        headers=headers,
        timeout=httpx.Timeout(30.0, connect=5.0),
        trust_env=False,
    ) as http:
        async with streamable_http_client(gateway.mcp_url, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


def payload_of(result) -> dict:
    """Extract the tool's JSON envelope from an MCP CallToolResult.

    FastMCP may expose a dict either as structured content or as a text block;
    both shapes are accepted so the assertions stay about behaviour.
    """
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and structured:
        if set(structured.keys()) == {"result"} and isinstance(structured["result"], dict):
            return structured["result"]
        return structured
    for item in result.content:
        text = getattr(item, "text", None)
        if text:
            return json.loads(text)
    raise AssertionError(f"tool returned no usable payload: {result}")


async def call_tool(
    gateway: mcp_service.McpGateway, actor: str, name: str, arguments: dict, grant: str | None = None
) -> dict:
    async with mcp_session(gateway, actor, grant) as session:
        result = await session.call_tool(name, arguments)
    assert getattr(result, "isError", False) is False, f"{name} raised at the protocol level: {result}"
    return payload_of(result)


def canonical_create_lines(quantity: int = 50) -> list[OrderLineInput]:
    return [OrderLineInput(part_id="P001", quantity=quantity, unit_price="25.50")]


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    """A live ERP plus a live gateway wired to it."""
    erp_dir = tmp_path_factory.mktemp("t04-erp")
    gateway_dir = tmp_path_factory.mktemp("t04-gateway")
    with erp_service.running_erp(erp_dir, seed_path=loader.SEED_PATH) as erp:
        with mcp_service.running_gateway(
            gateway_dir,
            erp_base_url=erp.base_url,
            erp_token=erp.token,
            grant_secret=mcp_service.DEFAULT_GRANT_SECRET,
        ) as gateway:
            yield erp, gateway


@pytest.fixture(scope="module")
def gateway(stack) -> mcp_service.McpGateway:
    return stack[1]


# --------------------------------------------------------------------------- #
# tool catalogue
# --------------------------------------------------------------------------- #


def test_tools_list_exposes_exactly_the_eight_contract_tools(gateway):
    async def list_names() -> set[str]:
        async with mcp_session(gateway, "demo-a") as session:
            listed = await session.list_tools()
            return {tool.name for tool in listed.tools}

    assert asyncio.run(list_names()) == set(ERP_TOOLS)


def test_tool_schemas_never_expose_runtime_injected_parameters(gateway):
    forbidden = {
        "actor",
        "actor_id",
        "owner",
        "owner_user_id",
        "token",
        "service_token",
        "operation_id",
        "grant",
        "approval",
        "approval_grant",
    }

    async def collect() -> dict[str, set[str]]:
        async with mcp_session(gateway, "demo-a") as session:
            listed = await session.list_tools()
            return {
                tool.name: set((tool.inputSchema or {}).get("properties", {})) for tool in listed.tools
            }

    surfaces = asyncio.run(collect())
    for name, properties in surfaces.items():
        assert not (properties & forbidden), f"{name} 暴露了运行时注入参数: {properties & forbidden}"


def test_tool_schema_snapshot_matches_the_checked_in_file(gateway):
    """Drift detection: any change to a published tool schema must be deliberate."""
    async def collect() -> dict:
        async with mcp_session(gateway, "demo-a") as session:
            listed = await session.list_tools()
            return {
                tool.name: tool.inputSchema for tool in sorted(listed.tools, key=lambda t: t.name)
            }

    actual = asyncio.run(collect())
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))["tools"]
    assert actual == expected, (
        "MCP 工具 schema 与快照不一致；若是有意变更，请更新 fixtures/mcp-tools.snapshot.json"
    )


# --------------------------------------------------------------------------- #
# read tools through real MCP
# --------------------------------------------------------------------------- #


def test_supplier_query_returns_the_seeded_suppliers(gateway):
    result = asyncio.run(call_tool(gateway, "demo-a", "supplier_query", {}))
    assert result["ok"] is True
    assert result["error"] is None
    assert result["request_id"]
    assert [item["supplier_id"] for item in result["data"]["items"]] == ["S001", "S002", "S003"]


def test_part_query_offers_only_active_suppliers(gateway):
    result = asyncio.run(call_tool(gateway, "demo-a", "part_query", {"part_id": "P001"}))
    assert result["ok"] is True
    offers = result["data"]["available_suppliers"]
    assert [offer["supplier_id"] for offer in offers] == ["S002", "S001"]
    assert offers[0]["catalog_price"] == "24.00"


def test_inventory_warning_matches_the_fixed_expectations(gateway):
    result = asyncio.run(call_tool(gateway, "demo-a", "inventory_warning", {}))
    expected = loader.expected()["inventory_warning"]

    assert result["ok"] is True
    items = result["data"]["items"]
    assert [item["part_id"] for item in items] == expected["part_ids"]
    assert {item["part_id"]: item["suggested_quantity"] for item in items} == (
        expected["suggested_quantity"]
    )


def test_part_search_matches_a_sku_substring(gateway):
    result = asyncio.run(call_tool(gateway, "demo-a", "part_search", {"q": "CHAIN"}))
    assert result["ok"] is True
    assert [item["part_id"] for item in result["data"]["items"]] == ["P003"]


def test_part_by_supplier_omits_parts_without_a_supply_relation(gateway):
    result = asyncio.run(call_tool(gateway, "demo-a", "part_by_supplier", {"supplier_id": "S002"}))
    assert result["ok"] is True
    assert [item["part_id"] for item in result["data"]["items"]] == ["P001", "P002", "P004"]


def test_unknown_part_is_a_structured_failure_not_an_empty_success(gateway):
    result = asyncio.run(call_tool(gateway, "demo-a", "part_query", {"part_id": "P999"}))

    assert result["ok"] is False
    assert result["data"] is None, "失败绝不能被转换成空数据成功"
    assert result["error"]["code"] == "PART_NOT_FOUND"
    assert result["error"]["retryable"] is False


def test_invalid_pagination_is_reported_as_a_business_error(gateway):
    result = asyncio.run(call_tool(gateway, "demo-a", "supplier_query", {"page_size": 500}))
    assert result["ok"] is False
    assert result["data"] is None
    assert result["error"]["code"] == "INVALID_ARGUMENT"


# --------------------------------------------------------------------------- #
# approval enforcement
# --------------------------------------------------------------------------- #


def grant_for_create(
    gateway: mcp_service.McpGateway,
    *,
    owner: str,
    quantity: int = 50,
    tool: str = "order_create",
    target: str | None = None,
    operation_id: str = "op-t04-create",
    ttl_seconds: int = 300,
    now: int | None = None,
    secret: str | None = None,
) -> str:
    frozen = canonical_order_body(
        supplier_id="S001",
        currency="CNY",
        lines=canonical_create_lines(quantity),
        note=None,
    )
    return issue_grant(
        secret=secret or gateway.grant_secret,
        owner=owner,
        tool=tool,
        target=target or create_target(),
        operation_id=operation_id,
        payload_sha256=sha256(frozen),
        ttl_seconds=ttl_seconds,
        now=now,
    )


CREATE_ARGUMENTS = {
    "supplier_id": "S001",
    "currency": "CNY",
    "lines": [{"part_id": "P001", "quantity": 50, "unit_price": "25.50"}],
}


def test_a_write_without_a_grant_is_refused(gateway):
    result = asyncio.run(call_tool(gateway, "demo-t04-nogrant", "order_create", CREATE_ARGUMENTS))

    assert result["ok"] is False
    assert result["data"] is None
    assert result["error"]["code"] == "APPROVAL_REQUIRED"


def test_a_grant_belonging_to_another_user_is_refused(gateway):
    grant = grant_for_create(gateway, owner="demo-a", operation_id="op-t04-other")
    result = asyncio.run(
        call_tool(gateway, "demo-t04-thief", "order_create", CREATE_ARGUMENTS, grant=grant)
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "GRANT_MISMATCH"
    assert "different user" in result["error"]["message"]


def test_a_grant_issued_for_different_parameters_is_refused(gateway):
    grant = grant_for_create(gateway, owner="demo-t04-changed", quantity=50, operation_id="op-t04-changed")
    changed = dict(CREATE_ARGUMENTS, lines=[{"part_id": "P001", "quantity": 51, "unit_price": "25.50"}])

    result = asyncio.run(
        call_tool(gateway, "demo-t04-changed", "order_create", changed, grant=grant)
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "GRANT_MISMATCH"
    assert "different parameters" in result["error"]["message"]


def test_a_grant_for_a_different_target_order_is_refused(gateway):
    grant = grant_for_create(
        gateway,
        owner="demo-t04-target",
        tool="order_update",
        target=update_target("O-19700101-00000001"),
        operation_id="op-t04-target",
    )
    arguments = {
        "order_id": "O-19700101-00000002",
        "expected_version": 1,
        "supplier_id": "S001",
        "currency": "CNY",
        "lines": [{"part_id": "P001", "quantity": 60, "unit_price": "25.50"}],
    }
    result = asyncio.run(call_tool(gateway, "demo-t04-target", "order_update", arguments, grant=grant))

    assert result["ok"] is False
    assert result["error"]["code"] == "GRANT_MISMATCH"


def test_an_expired_grant_is_refused(gateway):
    grant = grant_for_create(
        gateway, owner="demo-t04-expired", operation_id="op-t04-expired", ttl_seconds=1, now=1
    )
    result = asyncio.run(
        call_tool(gateway, "demo-t04-expired", "order_create", CREATE_ARGUMENTS, grant=grant)
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "GRANT_EXPIRED"


def test_a_tampered_grant_is_refused(gateway):
    grant = grant_for_create(gateway, owner="demo-t04-tampered", operation_id="op-t04-tampered")
    head, payload, signature = grant.split(".")
    tampered = f"{head}.{payload}.{signature[:-2]}xx"

    result = asyncio.run(
        call_tool(gateway, "demo-t04-tampered", "order_create", CREATE_ARGUMENTS, grant=tampered)
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_GRANT"


def test_a_grant_signed_with_the_wrong_secret_is_refused(gateway):
    grant = grant_for_create(
        gateway,
        owner="demo-t04-wrongsecret",
        operation_id="op-t04-wrongsecret",
        secret="not-the-gateway-secret",
    )
    result = asyncio.run(
        call_tool(gateway, "demo-t04-wrongsecret", "order_create", CREATE_ARGUMENTS, grant=grant)
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_GRANT"


# --------------------------------------------------------------------------- #
# approved writes
# --------------------------------------------------------------------------- #


def test_an_approved_create_writes_exactly_one_order_and_replays_under_the_same_grant(gateway):
    actor = "demo-t04-approved"
    grant = grant_for_create(gateway, owner=actor, operation_id="op-t04-approved")

    first = asyncio.run(call_tool(gateway, actor, "order_create", CREATE_ARGUMENTS, grant=grant))
    second = asyncio.run(call_tool(gateway, actor, "order_create", CREATE_ARGUMENTS, grant=grant))

    assert first["ok"] is True, first
    assert first["data"]["total_amount"] == "1275.00"
    assert first["data"]["version"] == 1
    assert second["ok"] is True
    assert second["data"]["order_id"] == first["data"]["order_id"], "同 key 重发必须重放而不是新建"

    listed = asyncio.run(call_tool(gateway, actor, "order_search_details", {}))
    assert listed["data"]["total"] == 1
    assert listed["data"]["items"][0]["order_id"] == first["data"]["order_id"]


def test_an_approved_update_replaces_the_whole_order(gateway):
    actor = "demo-t04-update"
    create_grant = grant_for_create(gateway, owner=actor, operation_id="op-t04-update-create")
    created = asyncio.run(
        call_tool(gateway, actor, "order_create", CREATE_ARGUMENTS, grant=create_grant)
    )
    order_id = created["data"]["order_id"]

    lines = [OrderLineInput(part_id="P001", quantity=60, unit_price="25.50")]
    frozen = canonical_update_body(
        expected_version=1, supplier_id="S001", currency="CNY", lines=lines, note=None
    )
    update_grant = issue_grant(
        secret=gateway.grant_secret,
        owner=actor,
        tool="order_update",
        target=update_target(order_id),
        operation_id="op-t04-update",
        payload_sha256=sha256(frozen),
    )
    arguments = {
        "order_id": order_id,
        "expected_version": 1,
        "supplier_id": "S001",
        "currency": "CNY",
        "lines": [{"part_id": "P001", "quantity": 60, "unit_price": "25.50"}],
    }
    updated = asyncio.run(call_tool(gateway, actor, "order_update", arguments, grant=update_grant))

    assert updated["ok"] is True, updated
    assert updated["data"]["version"] == 2
    assert updated["data"]["total_amount"] == "1530.00"
    assert updated["data"]["lines"][0]["quantity"] == 60


def test_an_approved_update_with_a_stale_version_is_refused_by_the_erp(gateway):
    actor = "demo-t04-stale"
    create_grant = grant_for_create(gateway, owner=actor, operation_id="op-t04-stale-create")
    created = asyncio.run(
        call_tool(gateway, actor, "order_create", CREATE_ARGUMENTS, grant=create_grant)
    )
    order_id = created["data"]["order_id"]

    lines = [OrderLineInput(part_id="P001", quantity=60, unit_price="25.50")]
    frozen = canonical_update_body(
        expected_version=1, supplier_id="S001", currency="CNY", lines=lines, note=None
    )
    first_grant = issue_grant(
        secret=gateway.grant_secret,
        owner=actor,
        tool="order_update",
        target=update_target(order_id),
        operation_id="op-t04-stale-1",
        payload_sha256=sha256(frozen),
    )
    arguments = {
        "order_id": order_id,
        "expected_version": 1,
        "supplier_id": "S001",
        "currency": "CNY",
        "lines": [{"part_id": "P001", "quantity": 60, "unit_price": "25.50"}],
    }
    assert asyncio.run(call_tool(gateway, actor, "order_update", arguments, grant=first_grant))["ok"]

    # A fresh grant for the same bytes but a new operation id: the ERP itself must still reject it,
    # because the version moved. Approval does not outrank the database.
    second_grant = issue_grant(
        secret=gateway.grant_secret,
        owner=actor,
        tool="order_update",
        target=update_target(order_id),
        operation_id="op-t04-stale-2",
        payload_sha256=sha256(frozen),
    )
    stale = asyncio.run(call_tool(gateway, actor, "order_update", arguments, grant=second_grant))

    assert stale["ok"] is False
    assert stale["error"]["code"] == "VERSION_CONFLICT"


def test_orders_are_scoped_to_the_calling_user(gateway):
    owner = "demo-t04-owner"
    grant = grant_for_create(gateway, owner=owner, operation_id="op-t04-scope")
    asyncio.run(call_tool(gateway, owner, "order_create", CREATE_ARGUMENTS, grant=grant))

    mine = asyncio.run(call_tool(gateway, owner, "order_search_details", {}))
    theirs = asyncio.run(call_tool(gateway, "demo-t04-stranger", "order_search_details", {}))

    assert mine["data"]["total"] == 1
    assert theirs["data"]["total"] == 0


def test_concurrent_users_do_not_cross_identities(gateway):
    owner = "demo-t04-concurrent-a"
    grant = grant_for_create(gateway, owner=owner, operation_id="op-t04-concurrent")
    asyncio.run(call_tool(gateway, owner, "order_create", CREATE_ARGUMENTS, grant=grant))

    async def interleaved() -> tuple[dict, dict]:
        first, second = await asyncio.gather(
            call_tool(gateway, owner, "order_search_details", {}),
            call_tool(gateway, "demo-t04-concurrent-b", "order_search_details", {}),
        )
        return first, second

    mine, theirs = asyncio.run(interleaved())
    assert mine["data"]["total"] == 1
    assert theirs["data"]["total"] == 0


# --------------------------------------------------------------------------- #
# failure handling and lifecycle
# --------------------------------------------------------------------------- #


def test_an_unreachable_erp_yields_a_retryable_failure_not_an_empty_success(tmp_path: Path):
    dead_port = mcp_service.free_port()
    with mcp_service.running_gateway(
        tmp_path / "isolated",
        erp_base_url=f"http://127.0.0.1:{dead_port}",
    ) as isolated:
        result = asyncio.run(call_tool(isolated, "demo-a", "supplier_query", {}))

    assert result["ok"] is False
    assert result["data"] is None, "断网不得被伪装成空列表成功"
    assert result["error"]["code"] in {"NETWORK_ERROR", "TIMEOUT"}
    assert result["error"]["retryable"] is True


def test_the_gateway_refuses_to_start_without_its_secrets():
    """No insecure fallback: a missing token or grant secret aborts startup.

    The child gets a normal environment with only the gateway's own settings
    removed, so the failure can only come from the missing configuration.
    """
    repo_root = Path(__file__).resolve().parents[2]
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"ERP_SERVICE_TOKEN", "MCP_GRANT_SECRET"}
    }
    env["PYTHONPATH"] = f"{repo_root / 'src'}{os.pathsep}{env.get('PYTHONPATH', '')}"
    completed = subprocess.run(
        [sys.executable, "-m", "mcp_server.server_main"],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 2, completed.stdout + completed.stderr
    output = completed.stdout + completed.stderr
    assert b"ERP_SERVICE_TOKEN" in output


def test_the_gateway_releases_its_port_when_it_stops(tmp_path: Path):
    port = mcp_service.free_port()
    with mcp_service.running_gateway(
        tmp_path / "lifecycle",
        erp_base_url="http://127.0.0.1:1",
        port=port,
    ):
        assert mcp_service.port_is_open(port)

    assert mcp_service.wait_until_closed(port), "退出后应释放端口，不能残留监听"


def test_the_gateway_logs_a_real_interaction_trail(gateway):
    """Evidence: the process log shows the ERP calls it actually made."""
    asyncio.run(call_tool(gateway, "demo-t04-log", "inventory_warning", {}))
    log = gateway.log_tail(limit=20000)

    assert "tool surface validated" in log
    assert "erp actor=demo-t04-log GET /api/erp/v1/inventory/warnings -> ok" in log
