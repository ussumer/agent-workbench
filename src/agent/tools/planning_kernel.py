"""General persistent Python tools; trusted scope is not in the model schema."""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from collections.abc import Callable

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool

from agent.artifacts.service import resolve_scope
from agent.planning.kernel import KernelService
from agent.planning.kernel_protocol import KernelError


def build_kernel_tools(service: KernelService) -> list[StructuredTool]:
    def scoped(config: RunnableConfig) -> tuple[str, str]:
        result = resolve_scope(config)
        if result is None:
            raise KernelError("KERNEL_SCOPE_REQUIRED", "trusted owner/thread configuration required")
        return result

    def reply(action: Callable[[], object]) -> str:
        try:
            result = action()
            ok = not isinstance(result, dict) or result.get("status") not in {"failed", "cancelled"}
            return json.dumps({"ok": ok, "data": result, "error": None}, ensure_ascii=False)
        except KernelError as failure:
            return json.dumps({"ok": False, "data": None,
                               "error": {"code": failure.code, "message": str(failure)}}, ensure_ascii=False)

    def kernel_execute(code: str, config: RunnableConfig, session: str = "analysis", timeout: int = 60) -> str:
        """Execute Python in this conversation's persistent kernel. Organize your own calculations;
        variables/imports persist. Read only needed results; no fixed planner is provided.
        """
        return reply(lambda: service.execute(*scoped(config), code, session=session, timeout=timeout))

    def kernel_checkpoint(names: list[str], config: RunnableConfig, session: str = "analysis") -> str:
        """Select named JSON-compatible variables for durable recovery. Preserve quotes, constraints,
        candidates and checks. Functions/modules need explicit reconstruction after context loss.
        """
        return reply(lambda: service.checkpoint(*scoped(config), tuple(names), session=session))

    def kernel_status(config: RunnableConfig, session: str = "analysis") -> str:
        """Inspect context identity and selected variable metadata, without reading all values."""
        return reply(lambda: service.status(*scoped(config), session=session))

    def kernel_cancel(operation_id: str, config: RunnableConfig) -> str:
        """Request interruption of this thread's running kernel operation; terminal operations are unchanged."""
        return reply(lambda: {"cancel_requested": service.cancel(*scoped(config), operation_id)})

    def kernel_output(operation_id: str, config: RunnableConfig, kind: str = "stdout", offset: int = 0) -> str:
        """Read a bounded page of preserved output, using byte offset; do not reload whole tables unnecessarily."""
        return reply(lambda: service.read_output(*scoped(config), operation_id, kind=kind, offset=offset))

    result = []
    for function in (kernel_execute, kernel_checkpoint, kernel_status, kernel_cancel, kernel_output):
        async def async_call(config: RunnableConfig, _function=function, **kwargs):
            return await asyncio.to_thread(_function, config=config, **kwargs)
        result.append(StructuredTool.from_function(function, coroutine=async_call))

    async def cancellable_execute(code: str, config: RunnableConfig, session: str,
                                  timeout: int, checkpoint_names=None) -> str:
        try:
            owner, thread = scoped(config)
        except KernelError as failure:
            return json.dumps({"ok": False, "data": None,
                               "error": {"code": failure.code, "message": str(failure)}})
        cancel_signal, operation_id = threading.Event(), uuid.uuid4().hex
        pending = asyncio.create_task(asyncio.to_thread(
            lambda: reply(lambda: service.execute(owner, thread, code, session=session,
                                                  timeout=timeout, cancel_signal=cancel_signal,
                                                  operation_id=operation_id,
                                                  checkpoint_names=checkpoint_names))))
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            cancel_signal.set()
            # The operation ID is assigned before scheduling. This cannot cancel
            # a newer operation in the same session, and a pre-start signal is
            # also checked inside the worker before running any Actor code.
            await asyncio.shield(asyncio.to_thread(service.cancel, owner, thread, operation_id))
            raise

    async def async_execute(code: str, config: RunnableConfig, session: str = "analysis", timeout: int = 60) -> str:
        return await cancellable_execute(code, config, session, timeout)

    async def async_checkpoint(names: list[str], config: RunnableConfig, session: str = "analysis") -> str:
        return await cancellable_execute("pass", config, session, 60, tuple(names))

    result[0] = StructuredTool.from_function(kernel_execute, coroutine=async_execute)
    result[1] = StructuredTool.from_function(kernel_checkpoint, coroutine=async_checkpoint)
    return result
