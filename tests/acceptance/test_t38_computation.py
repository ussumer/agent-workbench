"""New T38 data protocol checks; legacy kernel tests remain historical."""

import json

import pytest

from agent.planning.computation_protocol import (
    ComputationError,
    decode_staged,
    encode_values,
    validate_code,
    validate_names,
    validate_operation,
)
from agent.tools.planning_computation import build_computation_tools

pytestmark = pytest.mark.unit

OP = "a" * 32


@pytest.mark.parametrize("values", [{"x": float("nan")}, {"x": float("inf")}, {"x": object()},
                                     {"bad-key": 1}, {"__rh_secret": 1}, {str(i): i for i in range(33)}])
def test_state_rejects_invalid_data(values):
    with pytest.raises(ComputationError):
        encode_values(values)


def test_state_size_is_bounded():
    with pytest.raises(ComputationError, match="4MiB"):
        encode_values({"x": "a" * (4 * 1024 * 1024)})


@pytest.mark.parametrize("data", [b'{"operation_id":"wrong","base_version":1,"values":{}}',
    json.dumps({"operation_id": OP, "base_version": True, "values": {}}).encode(),
    json.dumps({"operation_id": OP, "base_version": 2, "values": {}}).encode(),
    ('{"operation_id":"'+OP+'","base_version":1,"values":{"x":1,"x":2}}').encode(),
    ('{"operation_id":"'+OP+'","base_version":1,"values":{"x":NaN}}').encode()])
def test_staged_data_fence_and_json_rejections(data):
    with pytest.raises(ComputationError):
        decode_staged(data, OP, 1)


def test_json_roundtrip_preserves_data_and_does_not_execute_strings():
    values = {"quotes": {"P001": 2550}, "script": "__import__('os').system('false')"}
    raw = json.dumps({"operation_id": OP, "base_version": 1, "values": values}).encode()
    assert decode_staged(raw, OP, 1) == values


@pytest.mark.parametrize("names", [["x", "x"], ["../owner"], [False]])
def test_requested_names_are_explicit_and_safe(names):
    with pytest.raises(ComputationError):
        validate_names(names)


def test_timeout_and_operation_validation():
    for timeout in [0, 301, True, 1.5]:
        with pytest.raises(ComputationError):
            validate_code("pass", timeout)
    with pytest.raises(ComputationError):
        validate_operation("../../file")


def test_tool_schema_excludes_identity_and_approval():
    names = set()
    for tool in build_computation_tools(None):
        names.add(tool.name)
        properties = tool.tool_call_schema.model_json_schema()["properties"]
        forbidden = {"owner", "thread", "config", "approval", "cancel_signal"}
        if tool.name == "computation_execute":
            forbidden.add("operation_id")
        assert not forbidden & set(properties)
    assert names == {"computation_execute", "computation_status", "computation_cancel", "computation_output", "computation_recover"}


def test_tools_reject_missing_scope_before_using_backend():
    tool = build_computation_tools(None)[0]
    result = json.loads(tool.invoke({"code": "pass"}))
    assert result["ok"] is False
    assert result["error"]["code"] == "COMPUTATION_SCOPE_REQUIRED"
