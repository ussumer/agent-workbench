"""Read-only business data for the background analysis service.

The background process runs the same project contracts as the foreground, but it does not
hold the MCP credentials and it must never be able to write — so it asks here, and this
endpoint answers with **read tools only**. The whitelist is not a formality: it is the reason
"异步没有写单权限" is a property of the system rather than a sentence in a prompt. A request
naming ``order_create`` is refused here, and the refusal is independent of what the background
graph believes it may do.

The owner comes from the request body, which is acceptable *only* because the caller is
authenticated by the internal service token first — the same reasoning as
``/internal/approvals/verify``. A browser can never reach this router.
"""

from __future__ import annotations

import hmac
import json
import logging
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Header, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

LOGGER = logging.getLogger("rush_harness.internal.analysis")

#: The tools the background service may call. Every one of them is a GET behind the gateway.
READ_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "inventory_warning",
        "part_query",
        "part_search",
        "part_by_supplier",
        "supplier_query",
        "order_search_details",
        "planning_goal",
    }
)

#: Named so a refusal can say what *would* have been allowed, and so a regression that adds a
#: write tool to the whitelist is visible in a diff rather than buried.
WRITE_TOOLS: frozenset[str] = frozenset({"order_create", "order_update"})

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


class ReadRequest(BaseModel):
    owner_user_id: str
    thread_id: str = ""
    operation_id: str = ""
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


def build_internal_analysis_router(*, mcp_url: str, service_token: str, database: Any = None) -> APIRouter:
    router = APIRouter(tags=["internal"])

    def _authorise(token: str | None) -> None:
        if not service_token:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "no internal service token is configured; the analysis surface is closed",
            )
        if not token or not hmac.compare_digest(token, service_token):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid internal service token")

    @router.post("/internal/analysis/read")
    async def read(
        body: ReadRequest,
        x_internal_service_token: str | None = Header(default=None),
    ) -> JSONResponse:
        _authorise(x_internal_service_token)

        if body.tool in WRITE_TOOLS:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"{body.tool} is a write tool; the background analyst is read-only",
            )
        if body.tool not in READ_ONLY_TOOLS:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"unknown read tool {body.tool!r}; allowed: {sorted(READ_ONLY_TOOLS)}",
            )
        if not body.owner_user_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "owner_user_id is required")
        arguments = _bounded_arguments(body.arguments)
        if body.tool == "planning_goal":
            if database is None:
                raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "planning store is not configured")
            goal = database.planning_goals.find_one({
                "owner_user_id": body.owner_user_id, "thread_id": body.thread_id,
                "goal_id": body.thread_id,
            }, {"_id": 0})
            if goal is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "planning goal not found")
            return JSONResponse({"data": {"tool": body.tool, "result": _planning_payload(goal)},
                                 "request_id": body.operation_id})
        if not mcp_url:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "no MCP gateway is configured"
            )
        try:
            result = await _call_gateway(mcp_url, body.owner_user_id, body.tool, arguments)
        except Exception as failure:  # noqa: BLE001 - reported as unavailable, not crashed
            LOGGER.warning("read %s failed for %s: %s", body.tool, body.owner_user_id, failure)
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, f"gateway read failed: {failure}"
            ) from failure

        return JSONResponse(
            {"data": {"tool": body.tool, "result": result}, "request_id": body.operation_id}
        )

    return router


def _planning_payload(goal: Mapping[str, Any]) -> dict[str, Any]:
    """Expose only public planning state; the async graph never receives approval tokens."""
    problem = goal.get("problem") or {}
    return {
        "goal_id": goal.get("goal_id"), "revision": goal.get("revision"),
        "problem": problem, "revision_change": goal.get("revision_change"),
        "decision": goal.get("decision"),
        "orders": [item.get("result", {}).get("data", {}) for item in goal.get("orders", [])],
        "sources": [{"part_id": item.get("part_id"), "path": item.get("path"),
                     "body": item.get("body")} for item in goal.get("sources", [])],
    }


def _bounded_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Keep pagination inside the contract's range, and drop everything else.

    Allow-listing the keys rather than passing the dict through is what stops the caller from
    smuggling an ``actor`` or a token: those are injected here from the trusted owner, and a
    request that carries one is simply not forwarded.
    """
    bounded: dict[str, Any] = {}
    page = arguments.get("page")
    if isinstance(page, int) and not isinstance(page, bool) and page >= 1:
        bounded["page"] = page
    size = arguments.get("page_size")
    if isinstance(size, int) and not isinstance(size, bool):
        bounded["page_size"] = max(1, min(MAX_PAGE_SIZE, size))
    else:
        bounded["page_size"] = DEFAULT_PAGE_SIZE
    for key in ("q", "active", "part_id", "supplier_id", "order_id"):
        if key in arguments and arguments[key] not in (None, ""):
            bounded[key] = arguments[key]
    return bounded


async def _call_gateway(
    mcp_url: str, owner_user_id: str, tool_name: str, arguments: dict[str, Any]
) -> Any:
    """Call one read tool on the gateway **as this owner**.

    The actor header is built here, never taken from the request — the background service
    cannot ask to be somebody else, and the gateway's own ``x-actor-id`` check still applies.
    """
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient(
        {
            "erp": {
                "url": mcp_url,
                "transport": "streamable_http",
                "headers": {"x-actor-id": owner_user_id},
            }
        }
    )
    tools = await client.get_tools()
    for tool in tools:
        if tool.name == tool_name:
            return _readable_payload(await tool.ainvoke(arguments))
    raise RuntimeError(f"the gateway does not expose {tool_name}")


def _readable_payload(result: Any) -> Any:
    """The tool's answer as data, not as content blocks.

    ``langchain_mcp_adapters`` hands back MCP's content list — ``[{"type": "text", "text":
    "{...}"}]`` — which is the right shape for a *model* to read and the wrong one for the
    caller here, which is another program indexing into the payload. Passing the blocks through
    left the background analyst with something it could only reject: it did, with "gateway
    returned no readable payload", and the task failed with an empty error field because the
    rejection happened inside the Protocol process.
    """
    if isinstance(result, str):
        text = result
    elif isinstance(result, (list, tuple)) and result:
        first = result[0]
        if not (isinstance(first, Mapping) and isinstance(first.get("text"), str)):
            return result
        text = first["text"]
    else:
        return result

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Not JSON is not the same as broken: the caller gets the text and can decide.
        return text


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "READ_ONLY_TOOLS",
    "WRITE_TOOLS",
    "ReadRequest",
    "build_internal_analysis_router",
]
