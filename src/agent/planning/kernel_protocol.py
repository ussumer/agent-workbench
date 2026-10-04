"""Public persistent-computation limits and safe JSON checkpoint encoding."""

from __future__ import annotations

import json
import re
import uuid

MAX_CODE_BYTES = 64 * 1024
MAX_PREVIEW_BYTES = 32 * 1024
MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
MAX_NAMES = 32


class KernelError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def validate_session(session: str) -> str:
    if not isinstance(session, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", session):
        raise KernelError("INVALID_SESSION", "session must be a short identifier")
    return session


def validate_names(names: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    if not isinstance(names, (tuple, list)) or any(not isinstance(n, str) for n in names):
        raise KernelError("INVALID_CHECKPOINT_NAMES", "names must be an array of strings")
    if len(names) > MAX_NAMES or len(names) != len(set(names)):
        raise KernelError("INVALID_CHECKPOINT_NAMES", "at most 32 distinct variable names")
    for name in names:
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name) or name.startswith("__rh_"):
            raise KernelError("INVALID_CHECKPOINT_NAMES", "checkpoint names must be Python identifiers")
    return tuple(names)


def validate_code(code: str, timeout: int) -> None:
    if not isinstance(code, str) or not code.strip() or len(code.encode()) > MAX_CODE_BYTES:
        raise KernelError("INVALID_CODE", "nonempty code of at most 64KiB required")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 300:
        raise KernelError("INVALID_TIMEOUT", "timeout must be an integer between 1 and 300 seconds")


def snapshot_source(names: tuple[str, ...], path: str) -> str:
    """A data export, not a planner. Values are encoded inside the remote kernel."""
    validate_names(names)
    helper = "__rh_export_" + uuid.uuid4().hex
    return f'''
def {helper}():
    import json
    namespace = globals()
    values, unsupported = {{}}, {{}}
    for name in {names!r}:
        if name not in namespace:
            unsupported[name] = "missing; rebuild explicitly"
            continue
        value = namespace[name]
        try:
            json.dumps(value, allow_nan=False)
            values[name] = value
        except (TypeError, ValueError, OverflowError, RecursionError):
            unsupported[name] = type(value).__name__ + "; rebuild explicitly, never replay automatically"
    text = json.dumps({{"schema_version": 1, "values": values, "unsupported": unsupported}}, allow_nan=False)
    if len(text.encode("utf-8")) > {MAX_SNAPSHOT_BYTES}:
        raise ValueError("KERNEL_CHECKPOINT_TOO_LARGE")
    with open({path!r}, "w", encoding="utf-8") as output:
        output.write(text)
{helper}()
del {helper}
'''


def decode_snapshot(raw: bytes, names: tuple[str, ...]) -> dict:
    if len(raw) > MAX_SNAPSHOT_BYTES:
        raise KernelError("CHECKPOINT_TOO_LARGE", "checkpoint exceeds 4MiB")
    try:
        value = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
        if set(value) != {"schema_version", "values", "unsupported"} or type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise ValueError("checkpoint shape")
        if not isinstance(value["values"], dict) or not isinstance(value["unsupported"], dict):
            raise ValueError("checkpoint values")
        keys = set(value["values"]) | set(value["unsupported"])
        if keys != set(names) or set(value["values"]) & set(value["unsupported"]):
            raise ValueError("checkpoint variable set")
        validate_names(tuple(keys))
        if any(not isinstance(item, str) for item in value["unsupported"].values()):
            raise ValueError("checkpoint rebuild hints")
        return value
    except (TypeError, ValueError, KeyError) as failure:
        raise KernelError("INVALID_CHECKPOINT", "checkpoint must match its JSON contract") from failure


def restore_source(snapshot: dict) -> str:
    text = json.dumps(snapshot["values"], ensure_ascii=True, allow_nan=False)
    # Data is decoded by JSON; neither values nor reconstruction hints are code.
    return f"globals().update(__import__('json').loads({text!r}))"
