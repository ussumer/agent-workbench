"""General computation tools; identity comes only from trusted runtime scope."""

from __future__ import annotations

import asyncio
import json
import threading
import uuid

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool

from agent.artifacts.service import resolve_scope
from agent.planning.computation import ComputationService
from agent.planning.computation_protocol import ComputationError


def build_computation_tools(service: ComputationService) -> list[StructuredTool]:
    def scoped(config: RunnableConfig) -> tuple[str, str]:
        scope = resolve_scope(config)
        if scope is None:
            raise ComputationError("COMPUTATION_SCOPE_REQUIRED", "trusted owner/thread configuration required")
        return scope

    def reply(action):
        try:
            result = action()
            ok = not isinstance(result, dict) or result.get("status", "completed") == "completed"
            return json.dumps({"ok": ok, "data": result, "error": None}, ensure_ascii=False)
        except ComputationError as failure:
            return json.dumps({"ok": False, "data": None, "error": {
                "code": failure.code, "message": str(failure)}}, ensure_ascii=False)

    def computation_execute(code: str, config: RunnableConfig, read_names: list[str] | None = None,
                            session: str = "analysis", timeout: int = 60, expected_version: int | None = None) -> str:
        """Run your Python code in a fresh OpenSandbox process. Declare only needed read_names;
        load_state(name) reads committed JSON and save_state(name, value) stages JSON.
        Successful validated execution atomically publishes a new version; failure preserves old data.
        No fixed planner, persistent Python objects, or arbitrary side-effect rollback is provided.
        """
        def execute():
            owner, thread = scoped(config)
            return service.execute(owner, thread, code, read_names=read_names or [],
                session=session, timeout=timeout, expected_version=expected_version)
        return reply(execute)

    def computation_status(config: RunnableConfig, session: str = "analysis") -> str:
        """Inspect committed version and per-name metadata, without loading all data into context."""
        return reply(lambda: service.status(*scoped(config), session=session))

    def computation_cancel(operation_id: str, config: RunnableConfig) -> str:
        """Revoke this operation's commit eligibility and stop its computation process tree."""
        return reply(lambda: {"cancel_requested": service.cancel(*scoped(config), operation_id)})

    def computation_output(operation_id: str, config: RunnableConfig, kind: str = "stdout", offset: int = 0) -> str:
        """Read a bounded page of durable output using a byte offset."""
        return reply(lambda: service.read_output(*scoped(config), operation_id, kind=kind, offset=offset))

    def computation_recover(config: RunnableConfig, session: str = "analysis") -> str:
        """After an API crash, revoke an abandoned operation and verify stopped processes.
        Preserve the last committed data. Unconfirmed cleanup quarantines the environment.
        """
        return reply(lambda: service.recover(*scoped(config), session=session))

    async def async_execute(code: str, config: RunnableConfig, read_names: list[str] | None = None,
                            session: str = "analysis", timeout: int = 60, expected_version: int | None = None) -> str:
        owner, thread = scoped(config)
        signal, opid = threading.Event(), uuid.uuid4().hex
        pending = asyncio.create_task(asyncio.to_thread(lambda: reply(lambda: service.execute(
            owner, thread, code, read_names=read_names or [], session=session, timeout=timeout,
            expected_version=expected_version, cancel_signal=signal, operation_id=opid))))
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            signal.set()
            # Join cleanup, so cancellation completion cannot be mistaken for
            # stopping just the Python await. The worker is bounded by its timeout.
            await asyncio.shield(pending)
            raise

    result = [StructuredTool.from_function(computation_execute, coroutine=async_execute)]
    for function in (computation_status, computation_cancel, computation_output, computation_recover):
        async def async_call(config: RunnableConfig, _function=function, **kwargs):
            return await asyncio.to_thread(_function, config=config, **kwargs)
        result.append(StructuredTool.from_function(function, coroutine=async_call))
    return result
