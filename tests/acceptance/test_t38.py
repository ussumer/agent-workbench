"""Deterministic input, reconstruction and trusted-tool-scope boundaries."""

import json

import pytest

from agent.planning.kernel_protocol import (
    KernelError,
    decode_snapshot,
    restore_source,
    snapshot_source,
    validate_code,
    validate_names,
    validate_session,
)
from agent.tools.planning_kernel import build_kernel_tools

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("session", ["../other", "", "a b", "x" * 65, "owner/session", 123])
def test_session_cannot_be_a_path_or_foreign_identity(session):
    with pytest.raises(KernelError, match="identifier"):
        validate_session(session)


@pytest.mark.parametrize("names", [["quotes;__import__('os')"], ["x", "x"], ["__rh_export"],
                                  ["x"] * 33, [None], [{}]])
def test_checkpoint_name_injection_or_ambiguous_names_are_rejected(names):
    with pytest.raises(KernelError):
        validate_names(names)


@pytest.mark.parametrize("code,timeout", [("", 1), ("pass", True), ("pass", 301), ("pass", 0),
                                         ("x" * 65537, 60)], ids=["empty", "bool", "too-long", "zero", "large-code"])
def test_code_and_timeout_limits_are_not_silently_coerced(code, timeout):
    with pytest.raises(KernelError):
        validate_code(code, timeout)


def test_json_snapshot_preserves_metadata_and_never_evaluates_reconstruction_hint():
    snapshot = {"schema_version": 1, "values": {"quotes": {"version": 2, "price": "25.50"}},
                "unsupported": {"planner": "function; rebuild explicitly"}}
    actual = decode_snapshot(json.dumps(snapshot).encode(), ("quotes", "planner"))
    assert actual == snapshot
    source = restore_source(actual)
    assert "function; rebuild explicitly" not in source
    assert "json" in source and "pickle" not in source
    assert "eval(" not in source and "exec(" not in source


@pytest.mark.parametrize("raw", [b"null", b"{}", b'{"schema_version":1,"values":{"x":NaN},"unsupported":{}}',
                                 b'{"schema_version":true,"values":{},"unsupported":{}}'])
def test_untrusted_checkpoint_shape_and_nonfinite_values_are_rejected(raw):
    with pytest.raises(KernelError):
        decode_snapshot(raw, ("x",))


def test_extra_checkpoint_variable_cannot_override_globals_during_recovery():
    raw = json.dumps({"schema_version": 1, "values": {"safe": 2, "__builtins__": {}},
                      "unsupported": {}}).encode()
    with pytest.raises(KernelError):
        decode_snapshot(raw, ("safe",))


def test_snapshot_export_has_no_full_planner_and_safely_quotes_filename():
    source = snapshot_source(("quotes", "constraints", "plans", "checks"), "'file.json")
    assert "allow_nan=False" in source
    assert "globals()" in source
    assert "supplier" not in source
    # Source is parsed only; actual execution must happen remotely.
    import ast
    ast.parse(source)


class ToolOnlyService:
    """Adapter fake only. Real Mongo/OpenSandbox behavior is tested separately."""
    def execute(self, owner, thread, code, **kwargs):
        return {"status": "completed", "scope": [owner, thread], "code": code}


def test_kernel_tool_schema_does_not_let_model_select_owner_thread_or_context():
    tools = build_kernel_tools(ToolOnlyService())
    for tool in tools:
        fields = tool.tool_call_schema.model_json_schema()["properties"]
        assert not {"config", "owner", "owner_user_id", "thread", "thread_id", "context_id"} & fields.keys()
    execute = next(t for t in tools if t.name == "kernel_execute")
    assert json.loads(execute.invoke({"code": "pass"}))["error"]["code"] == "KERNEL_SCOPE_REQUIRED"
    output = json.loads(execute.invoke({"code": "pass"}, config={
        "configurable": {"owner_user_id": "trusted-owner", "thread_id": "trusted-thread"}
    }))
    assert output["data"]["scope"] == ["trusted-owner", "trusted-thread"]


async def test_kernel_tool_async_uses_same_scope_and_executes_off_event_loop():
    tools = build_kernel_tools(ToolOnlyService())
    execute = next(t for t in tools if t.name == "kernel_execute")
    result = json.loads(await execute.ainvoke({"code": "pass"}, config={
        "configurable": {"owner_user_id": "trusted-owner", "thread_id": "trusted-thread"}
    }))
    assert result["ok"] and result["data"]["scope"] == ["trusted-owner", "trusted-thread"]


@pytest.mark.parametrize("status,infra", [(404, False), (400, False), (500, True), (503, True)])
def test_kernel_sdk_server_errors_feed_shared_breaker_but_bad_context_does_not(status, infra):
    from opensandbox.exceptions import SandboxApiException

    from agent.backends.sandbox_proxy import SandboxBackendProxy
    class Backend:
        id = "adapter-only"
        def kernel_get_context(self, context_id):
            raise SandboxApiException(status_code=status)
    proxy = SandboxBackendProxy(Backend(), owner_user_id="test-owner")
    for _ in range(3):
        with pytest.raises(SandboxApiException):
            proxy.kernel_get_context("missing")
    assert proxy.breaker.snapshot().sandbox_failures == (3 if infra else 0)
    assert proxy.breaker.state == ("open" if infra else "closed")

