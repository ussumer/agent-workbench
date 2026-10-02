"""Contract tests for the MCP gateway's pure logic.

No service is started here: these cover the canonical frozen bytes, the grant
codec, the tool catalogue and the result envelope. They are fast enough to run on
every change and are included in the full regression.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from mcp_server.config import ConfigurationError, McpSettings  # noqa: E402
from mcp_server.context import (  # noqa: E402
    CallerIdentity,
    MissingCallerIdentity,
    caller_scope,
    current_caller,
    identity_from_headers,
)
from mcp_server.grants import GrantError, issue_grant, verify_grant  # noqa: E402
from mcp_server.http_base import error_result, ok_result  # noqa: E402
from mcp_server.tools.registry import (  # noqa: E402
    ANALYST_TOOL_SET,
    ERP_READ_TOOLS,
    ERP_TOOLS,
    ERP_WRITE_TOOLS,
    FORBIDDEN_SCHEMA_PROPERTIES,
    ORDER_TOOL_SET,
    OrderLineInput,
    canonical_order_body,
    canonical_update_body,
    create_target,
    update_target,
)

pytestmark = pytest.mark.contract

SECRET = "contract-secret"


def lines(quantity: int = 50) -> list[OrderLineInput]:
    return [OrderLineInput(part_id="P001", quantity=quantity, unit_price="25.50")]


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


# --------------------------------------------------------------------------- #
# canonical frozen bytes
# --------------------------------------------------------------------------- #


def test_canonical_create_body_is_stable_and_normalises_the_note():
    first = canonical_order_body(
        supplier_id="S001", currency="CNY", lines=lines(), note=None
    )
    second = canonical_order_body(
        supplier_id="S001", currency="CNY", lines=lines(), note=""
    )

    assert first == second, "省略 note 与空字符串必须生成同一份冻结字节"
    assert first == (
        b'{"currency":"CNY","lines":[{"part_id":"P001","quantity":50,'
        b'"unit_price":"25.50"}],"note":"","supplier_id":"S001"}'
    )


def test_canonical_body_changes_with_any_business_field():
    base = canonical_order_body(supplier_id="S001", currency="CNY", lines=lines(), note=None)

    assert base != canonical_order_body(
        supplier_id="S002", currency="CNY", lines=lines(), note=None
    )
    assert base != canonical_order_body(
        supplier_id="S001", currency="CNY", lines=lines(51), note=None
    )
    assert base != canonical_order_body(
        supplier_id="S001", currency="CNY", lines=lines(), note="备注"
    )


def test_canonical_update_body_includes_the_expected_version():
    body = canonical_update_body(
        expected_version=1, supplier_id="S001", currency="CNY", lines=lines(60), note=None
    )
    assert b'"expected_version":1' in body
    assert body != canonical_update_body(
        expected_version=2, supplier_id="S001", currency="CNY", lines=lines(60), note=None
    )


def test_targets_distinguish_create_from_a_specific_order():
    assert create_target() == "orders"
    assert update_target("O-1") == "orders/O-1"
    assert update_target("O-1") != update_target("O-2")


# --------------------------------------------------------------------------- #
# tool catalogue
# --------------------------------------------------------------------------- #


def test_catalogue_holds_exactly_the_eight_contract_tools():
    assert ERP_TOOLS == (
        "supplier_query",
        "part_query",
        "part_search",
        "part_by_supplier",
        "inventory_warning",
        "order_search_details",
        "order_create",
        "order_update",
    )
    assert len(ERP_READ_TOOLS) == 6
    assert ERP_WRITE_TOOLS == ("order_create", "order_update")


def test_role_tool_sets_match_the_architecture():
    assert ANALYST_TOOL_SET == set(ERP_READ_TOOLS)
    assert ORDER_TOOL_SET == set(ERP_TOOLS)
    assert not (ANALYST_TOOL_SET & set(ERP_WRITE_TOOLS)), "分析角色不得拥有写工具"


def test_forbidden_parameter_list_covers_runtime_injected_names():
    for name in ("actor", "actor_id", "token", "service_token", "operation_id", "grant"):
        assert name in FORBIDDEN_SCHEMA_PROPERTIES


# --------------------------------------------------------------------------- #
# grants
# --------------------------------------------------------------------------- #


def grant(**overrides) -> str:
    parameters = {
        "secret": SECRET,
        "owner": "demo-a",
        "tool": "order_create",
        "target": create_target(),
        "operation_id": "op-1",
        "payload_sha256": "a" * 64,
        "now": 1_000,
    }
    parameters.update(overrides)
    return issue_grant(**parameters)


def verify(token: str, **overrides):
    parameters = {
        "secret": SECRET,
        "expect_owner": "demo-a",
        "expect_tool": "order_create",
        "expect_target": create_target(),
        "expect_payload_sha256": "a" * 64,
        "now": 1_100,
    }
    parameters.update(overrides)
    return verify_grant(token, **parameters)


def test_a_grant_round_trips_and_carries_its_bindings():
    parsed = verify(grant())

    assert parsed.owner == "demo-a"
    assert parsed.tool == "order_create"
    assert parsed.target == "orders"
    assert parsed.operation_id == "op-1"
    assert parsed.payload_sha256 == "a" * 64


def test_a_missing_grant_is_reported_as_approval_required():
    with pytest.raises(GrantError) as failure:
        verify_grant(
            None,
            secret=SECRET,
            expect_owner="demo-a",
            expect_tool="order_create",
            expect_target=create_target(),
            expect_payload_sha256="a" * 64,
        )
    assert failure.value.code == "APPROVAL_REQUIRED"


def test_a_malformed_or_tampered_grant_is_invalid():
    with pytest.raises(GrantError) as malformed:
        verify("not-a-grant")
    assert malformed.value.code == "INVALID_GRANT"

    head, payload, signature = grant().split(".")
    with pytest.raises(GrantError) as tampered:
        verify(f"{head}.{payload}.{signature[:-2]}xx")
    assert tampered.value.code == "INVALID_GRANT"


def test_a_grant_signed_with_another_secret_is_invalid():
    with pytest.raises(GrantError) as failure:
        verify(grant(secret="a-different-secret"))
    assert failure.value.code == "INVALID_GRANT"


def test_an_expired_grant_is_reported_separately():
    with pytest.raises(GrantError) as failure:
        verify(grant(now=1_000), now=9_999)
    assert failure.value.code == "GRANT_EXPIRED"


@pytest.mark.parametrize(
    "override",
    [
        {"expect_owner": "demo-b"},
        {"expect_tool": "order_update"},
        {"expect_target": update_target("O-1")},
        {"expect_payload_sha256": "b" * 64},
    ],
)
def test_every_binding_mismatch_is_rejected(override):
    with pytest.raises(GrantError) as failure:
        verify(grant(), **override)
    assert failure.value.code == "GRANT_MISMATCH"


# --------------------------------------------------------------------------- #
# identity
# --------------------------------------------------------------------------- #


def test_identity_is_read_case_insensitively_from_headers():
    identity = identity_from_headers({"X-Actor-Id": "demo-a", "X-Approval-Grant": "token"})
    assert identity == CallerIdentity(actor_id="demo-a", grant="token")


def test_a_missing_actor_is_refused_rather_than_defaulted():
    with pytest.raises(MissingCallerIdentity):
        identity_from_headers({"x-approval-grant": "token"})


def test_the_caller_scope_does_not_leak_after_the_call():
    with caller_scope(CallerIdentity(actor_id="demo-a")):
        assert current_caller().actor_id == "demo-a"
    with pytest.raises(MissingCallerIdentity):
        current_caller()


# --------------------------------------------------------------------------- #
# configuration and envelope
# --------------------------------------------------------------------------- #


def test_configuration_requires_the_service_token_and_grant_secret():
    with pytest.raises(ConfigurationError) as missing_token:
        McpSettings.from_env({"ERP_BASE_URL": "http://localhost:8080", "MCP_GRANT_SECRET": "s"})
    assert "ERP_SERVICE_TOKEN" in str(missing_token.value)

    with pytest.raises(ConfigurationError) as missing_secret:
        McpSettings.from_env({"ERP_BASE_URL": "http://localhost:8080", "ERP_SERVICE_TOKEN": "s"})
    assert "MCP_GRANT_SECRET" in str(missing_secret.value)


def test_configuration_defaults_match_the_contract_timeouts():
    settings = McpSettings.from_env({"ERP_SERVICE_TOKEN": "s", "MCP_GRANT_SECRET": "g"})

    assert settings.connect_timeout == 3.0
    assert settings.read_timeout == 15.0
    assert settings.tool_budget == 20.0
    assert settings.read_retries == 2
    assert settings.erp_base_url.startswith("http://")


def test_the_success_envelope_never_carries_an_error():
    envelope = ok_result({"items": []}, "req-1")
    assert envelope == {"ok": True, "data": {"items": []}, "error": None, "request_id": "req-1"}


def test_the_failure_envelope_never_carries_data():
    envelope = error_result("PART_NOT_FOUND", "missing", retryable=False)
    assert envelope["ok"] is False
    assert envelope["data"] is None
    assert envelope["error"]["code"] == "PART_NOT_FOUND"
    assert envelope["error"]["retryable"] is False
    assert envelope["error"]["details"] == {}
