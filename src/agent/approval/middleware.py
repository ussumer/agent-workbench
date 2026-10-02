"""Layer two: the approval gate that sits between a write tool call and the ERP.

The gate is a middleware rather than a prompt or a wrapper on the model's tools, because it
needs three things that only exist at the tool-call boundary: the exact ``tool_call_id`` the
framework assigned (so an approval can be matched to *this* call and not a lookalike), the
exact arguments about to be sent, and the ability to answer without executing anything.

What the model sees: the ERP's response envelope, or a structured refusal. What it never
sees: the grant, the operation id, the actor or the frozen bytes. Those travel out of band,
attached to the HTTP call to the gateway.

Placement matters. The middleware is installed on the sub-agents too, so the gate covers the
call the order agent makes, not only calls the main agent would have made itself.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage

from agent.approval.models import ApprovalError
from agent.approval.service import APPROVABLE_TOOLS, ApprovalService, WriteAuthorization

LOGGER = logging.getLogger("rush_harness.approval.middleware")

#: Run-scoped config keys. Injected by the API from the trusted session, never by a model.
OWNER_CONFIG_KEY = "owner_user_id"
THREAD_CONFIG_KEY = "thread_id"

#: The interrupt a resume run is answering. Set only by the resume path, after the request
#: has been validated against the stored pending action. Its presence is what distinguishes
#: "the user approved this" from "the graph is trying the write again".
APPROVED_INTERRUPT_CONFIG_KEY = "approved_interrupt_id"

#: How the refusal reaches the model. Same envelope shape as a gateway failure, so the
#: model has one failure format to reason about.
REFUSAL_CODES: dict[str, str] = {
    "APPROVAL_REQUIRED": "这个写操作需要用户审批后才能执行，当前没有有效授权，未执行任何写操作。",
    "APPROVAL_REJECTED": "用户拒绝了这次写操作，请勿重试，也不要改动参数后再试。",
    "APPROVAL_EXPIRED": "这次审批已过期，请重新征求用户确认。",
    "PARAMETERS_CHANGED": "待执行的内容与用户批准的不一致，必须重新审批。",
    "ALREADY_DECIDED": "这个动作已经有人处理过，本次不再执行。",
    "ACTION_NOT_FOUND": "找不到这次写操作对应的审批记录，未执行任何写操作。",
    "GRANT_MISMATCH": "授权与当前调用不匹配，未执行任何写操作。",
    "BATCH_NOT_SUPPORTED": "本演示一次只审批一个写操作。",
    "ILLEGAL_TRANSITION": "审批状态已变化，本次不再执行。",
}


class MCPWriteChannel(Protocol):
    """Performs one granted MCP write call."""

    async def __call__(
        self,
        *,
        name: str,
        arguments: Mapping[str, Any],
        owner_user_id: str,
        grant: str,
    ) -> Any: ...


@dataclass
class HttpMCPWriteChannel:
    """Calls a gateway tool on a fresh session that carries the grant header.

    A new client per write is deliberate. The gateway reads the grant from the request
    headers, and the adapter sets headers per connection; sharing one connection would mean
    either caching one user's grant for the next user or mutating shared defaults, which is
    the cross-user leak the MCP contract calls out. Writes are rare, so the extra handshake
    is affordable.
    """

    mcp_url: str
    timeout_seconds: float = 30.0

    async def __call__(
        self,
        *,
        name: str,
        arguments: Mapping[str, Any],
        owner_user_id: str,
        grant: str,
    ) -> Any:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        client = MultiServerMCPClient(
            {
                "erp": {
                    "url": self.mcp_url,
                    "transport": "streamable_http",
                    "headers": {
                        "x-actor-id": owner_user_id,
                        "x-approval-grant": grant,
                    },
                }
            }
        )
        tools = await client.get_tools()
        for tool in tools:
            if tool.name == name:
                return await tool.ainvoke(dict(arguments))
        raise ApprovalError("TOOL_NOT_FOUND", f"the gateway does not expose {name}")


def refusal_message(code: str, detail: str = "") -> str:
    """The JSON envelope a refused write returns."""
    text = REFUSAL_CODES.get(code, "写操作未获授权，未执行。")
    return json.dumps(
        {
            "ok": False,
            "data": None,
            "error": {
                "code": code,
                "message": f"{text}{(' ' + detail) if detail else ''}".strip(),
                "retryable": False,
                "details": None,
            },
            "request_id": None,
        },
        ensure_ascii=False,
    )


class WriteApprovalMiddleware(AgentMiddleware):
    """Refuses unapproved writes and performs approved ones out of band."""

    def __init__(
        self,
        *,
        service: ApprovalService,
        channel: MCPWriteChannel,
        write_tools: Sequence[str] = APPROVABLE_TOOLS,
    ) -> None:
        super().__init__()
        self._service = service
        self._channel = channel
        self._write_tools = frozenset(write_tools)

    # The hook is async because the gateway call is; the sync path refuses, since a
    # synchronous graph must not perform a network write while holding a tool slot.

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage]],
    ) -> ToolMessage:
        tool_call = request.tool_call
        name = str(tool_call.get("name") or "")
        if name not in self._write_tools:
            return await handler(request)

        call_id = str(tool_call.get("id") or "")
        arguments = dict(tool_call.get("args") or {})

        # Resolved separately: a run without a scope has no owner or thread to log, and
        # naming them in the failure path would be an UnboundLocalError rather than a refusal.
        try:
            owner_user_id, thread_id, approved_interrupt_id = _scope(request)
        except ApprovalError as failure:
            LOGGER.info("write %s refused: %s", name, failure.code)
            return _tool_message(
                call_id, name, refusal_message(failure.code, str(failure)), status="error"
            )

        if not approved_interrupt_id:
            LOGGER.info("write %s refused for owner=%s: no approved interrupt", name, owner_user_id)
            return _tool_message(
                call_id,
                name,
                refusal_message(
                    "APPROVAL_REQUIRED",
                    "this run carries no approved interrupt, so the write cannot be "
                    "attributed to a user decision",
                ),
                status="error",
            )

        try:
            authorization = self._service.authorize(
                owner_user_id=owner_user_id,
                thread_id=thread_id,
                interrupt_id=approved_interrupt_id,
                tool_name=name,
                arguments=arguments,
            )
        except ApprovalError as failure:
            LOGGER.info(
                "write %s refused for owner=%s thread=%s: %s",
                name,
                owner_user_id,
                thread_id,
                failure.code,
            )
            return _tool_message(call_id, name, refusal_message(failure.code, str(failure)), status="error")

        return await self._execute(call_id, name, owner_user_id, authorization, arguments)

    def wrap_tool_call(self, request: ToolCallRequest, handler: Callable[..., Any]) -> ToolMessage:
        """The synchronous path refuses every write.

        A granted write performs an HTTP call, and this codebase has no synchronous MCP
        client. Refusing is the honest outcome: the alternative would be to run the async
        client on a private loop inside a synchronously executing graph, which reintroduces
        exactly the concurrency the run registry is meant to control.
        """
        tool_call = request.tool_call
        name = str(tool_call.get("name") or "")
        if name not in self._write_tools:
            return handler(request)
        return _tool_message(
            str(tool_call.get("id") or ""),
            name,
            refusal_message(
                "APPROVAL_REQUIRED", "写操作只能通过异步运行执行。"
            ),
            status="error",
        )

    async def _execute(
        self,
        call_id: str,
        name: str,
        owner_user_id: str,
        authorization: WriteAuthorization,
        arguments: Mapping[str, Any],
    ) -> ToolMessage:
        """Perform the write the user approved.

        The arguments sent are the ones the *tool call* carried, not the ones the frozen
        payload carries, and the difference is not cosmetic: the frozen payload is the body
        the ERP will receive, and for ``order_update`` that body deliberately has no
        ``order_id`` — the target is bound in the grant instead (``canonical_update_body``,
        and the gateway re-checks it with ``expect_target``). Rebuilding the call from the
        payload therefore produced an ``order_update`` with no ``order_id``, which the gateway
        rejected as a schema violation before any write happened: an approved update could
        never execute. ``order_create`` hid this, because every one of its arguments is also
        in the frozen body, so re-deriving them happened to be lossless.

        Using the caller's arguments is not a weakening: ``authorize`` has already compared
        the canonical form of exactly these values against the bytes the user approved, and
        refused with ``PARAMETERS_CHANGED`` if they differ. This function only runs after that
        check passed.
        """
        try:
            result = await self._channel(
                name=name,
                arguments=dict(arguments),
                owner_user_id=owner_user_id,
                grant=authorization.grant,
            )
        except Exception as failure:  # noqa: BLE001 - reported, not swallowed
            self._service.finish(authorized=authorization, ok=False, error=str(failure))
            LOGGER.warning("write %s failed after approval: %s", name, failure)
            return _tool_message(
                call_id,
                name,
                _as_text(result=None, error=str(failure)),
                status="error",
            )

        self._service.finish(authorized=authorization, ok=True)
        LOGGER.info("write %s executed as operation %s", name, authorization.operation_id)
        return _tool_message(call_id, name, _as_text(result=result))


def _scope(request: ToolCallRequest) -> tuple[str, str, str | None]:
    """Owner, thread and approved interrupt for this run, from the trusted run config.

    Read from ``configurable`` rather than from tool arguments: the model must not be able
    to name the owner, and an approval must belong to the thread it was raised in.
    """
    config = getattr(getattr(request, "runtime", None), "config", None) or {}
    configurable = config.get("configurable") or {}
    owner = configurable.get(OWNER_CONFIG_KEY)
    thread = configurable.get(THREAD_CONFIG_KEY)
    if not owner or not thread:
        raise ApprovalError(
            "APPROVAL_REQUIRED",
            f"the run config must carry {OWNER_CONFIG_KEY!r} and {THREAD_CONFIG_KEY!r}; "
            "writes cannot be attributed without them",
        )
    approved = configurable.get(APPROVED_INTERRUPT_CONFIG_KEY)
    return str(owner), str(thread), (str(approved) if approved else None)


def _tool_message(
    call_id: str, name: str, content: str, *, status: str = "success"
) -> ToolMessage:
    return ToolMessage(
        content=content, tool_call_id=call_id, name=name, status=status  # type: ignore[arg-type]
    )


def _as_text(*, result: Any = None, error: str | None = None) -> str:
    """Normalise a tool result into the envelope string the model expects."""
    if error is not None:
        return json.dumps(
            {
                "ok": False,
                "data": None,
                "error": {"code": "WRITE_FAILED", "message": error, "retryable": False},
                "request_id": None,
            },
            ensure_ascii=False,
        )
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        # LangChain MCP tools may return structured content blocks.
        parts = []
        for block in result:
            if isinstance(block, Mapping) and isinstance(block.get("text"), str):
                parts.append(block["text"])
            else:
                parts.append(json.dumps(block, ensure_ascii=False, default=str))
        return "\n".join(parts)
    return json.dumps(result, ensure_ascii=False, default=str)


__all__ = [
    "OWNER_CONFIG_KEY",
    "THREAD_CONFIG_KEY",
    "HttpMCPWriteChannel",
    "MCPWriteChannel",
    "WriteApprovalMiddleware",
    "refusal_message",
]
