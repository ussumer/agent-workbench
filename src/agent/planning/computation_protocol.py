"""Strict, bounded JSON data contract for disposable sandbox computations."""

from __future__ import annotations

import json
import re

from agent.planning.kernel_protocol import (
    MAX_CODE_BYTES,
    MAX_NAMES,
    MAX_PREVIEW_BYTES,
    MAX_SNAPSHOT_BYTES,
    KernelError,
    validate_code,
    validate_names,
    validate_session,
)

ComputationError = KernelError


def encode_values(values: dict) -> str:
    if not isinstance(values, dict):
        raise ComputationError("INVALID_STATE", "state must be a named JSON object")
    validate_names(tuple(values))
    try:
        text = json.dumps(values, ensure_ascii=True, allow_nan=False)
    except (ValueError, TypeError, OverflowError, RecursionError) as failure:
        raise ComputationError("INVALID_STATE", "state must contain finite JSON data") from failure
    if len(text.encode()) > MAX_SNAPSHOT_BYTES:
        raise ComputationError("STATE_TOO_LARGE", "state exceeds 4MiB")
    return text


def decode_staged(raw: bytes, operation_id: str, base_version: int) -> dict:
    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        if len(raw) > MAX_SNAPSHOT_BYTES + 1024:
            raise ValueError("size")
        data = json.loads(raw, object_pairs_hook=no_duplicates,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
        if (not isinstance(data, dict) or set(data) != {"operation_id", "base_version", "values"}
                or data["operation_id"] != operation_id or type(data["base_version"]) is not int
                or data["base_version"] != base_version):
            raise ValueError("fence")
        encode_values(data["values"])
        return data["values"]
    except (TypeError, ValueError, KeyError, RecursionError) as failure:
        raise ComputationError("INVALID_STAGED_STATE", "temporary result failed JSON/fence validation") from failure


def validate_operation(operation_id: str) -> str:
    if not isinstance(operation_id, str) or not re.fullmatch(r"[a-f0-9]{32}", operation_id):
        raise ComputationError("INVALID_OPERATION", "operation ID must be a 32-character hex identifier")
    return operation_id


__all__ = ["ComputationError", "MAX_CODE_BYTES", "MAX_NAMES", "MAX_PREVIEW_BYTES",
           "MAX_SNAPSHOT_BYTES", "decode_staged", "encode_values", "validate_code",
           "validate_names", "validate_operation", "validate_session"]
