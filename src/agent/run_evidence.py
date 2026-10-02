"""Invocation-local evidence from ERP tools, including delegated agent calls."""

from __future__ import annotations

import json
import threading
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from agent.middlewares.context_injection import ERP_TOOLS, WRITE_TOOLS


def _body(output: Any) -> dict[str, Any] | None:
    value = getattr(output, "content", output)
    if isinstance(value, list):
        value = "".join(block.get("text", "") for block in value if isinstance(block, dict))
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    if isinstance(value, dict) and isinstance(value.get("result"), dict):
        value = value["result"]
    return value if isinstance(value, dict) else None


class RunEvidence(BaseCallbackHandler):
    """One instance per API invocation; no graph-global mutable trace or tool arguments."""

    def __init__(self) -> None:
        self._calls: dict[UUID, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def on_tool_start(self, serialized: dict[str, Any], input_str: str, *, run_id: UUID,
                      **kwargs: Any) -> None:
        name = str((serialized or {}).get("name") or kwargs.get("name") or "")
        if name in ERP_TOOLS:
            with self._lock:
                self._calls[run_id] = {"name": name, "status": "running", "data": None}

    def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        body = _body(output)
        with self._lock:
            call = self._calls.get(run_id)
            if call is None:
                return
            # A completed callback is not itself proof of a successful business call.
            if body is not None and body.get("ok") is True and getattr(output, "status", "success") != "error":
                call.update(status="success", data=body.get("data"))
            else:
                error = (body or {}).get("error") or {}
                call.update(status="failed", code=error.get("code", "RESULT_UNVERIFIED"))

    def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        with self._lock:
            if run_id in self._calls:
                self._calls[run_id].update(status="failed", code=type(error).__name__)

    def calls(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(call) for call in self._calls.values()]

    def denied_writes(self) -> list[str]:
        return [call["name"] for call in self.calls() if call["name"] in WRITE_TOOLS
                and call.get("code") in {"APPROVAL_DENIED", "WRITE_NOT_APPROVED", "REJECTED"}]
