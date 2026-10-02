"""Accessors that turn an MCP tool context into the gateway's dependencies.

Keeping these in one place means every tool obtains the caller identity and the
pooled ERP client the same way, and none of them has a reason to reach for a
module-level global.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any

from mcp.server.fastmcp import Context

from .config import McpSettings
from .context import CallerIdentity, caller_scope, identity_from_headers
from .http_base import ErpClient


@dataclass(frozen=True)
class GatewayRuntime:
    """Per-process resources created by the FastMCP lifespan."""

    erp: ErpClient
    settings: McpSettings


def runtime(ctx: Context) -> GatewayRuntime:
    lifespan_context = ctx.request_context.lifespan_context
    if not isinstance(lifespan_context, GatewayRuntime):
        raise RuntimeError("the gateway lifespan did not publish its runtime resources")
    return lifespan_context


def erp_client(ctx: Context) -> ErpClient:
    return runtime(ctx).erp


def request_headers(ctx: Context) -> Mapping[str, str]:
    """HTTP headers of the MCP request that carried this tool call."""
    request = ctx.request_context.request
    if request is None:
        raise RuntimeError(
            "this tool call has no HTTP request context; the gateway only serves Streamable HTTP"
        )
    headers = getattr(request, "headers", None)
    if headers is None:  # pragma: no cover - defensive
        raise RuntimeError("the MCP request carries no headers")
    return headers


@contextlib.asynccontextmanager
async def caller(ctx: Context) -> AsyncIterator[CallerIdentity]:
    """Bind the trusted caller identity for the duration of one tool call."""
    identity = identity_from_headers(request_headers(ctx))
    with caller_scope(identity):
        yield identity


def as_dict(value: Any) -> dict[str, Any]:
    """Normalise a FastMCP result payload into a plain dict for the envelope."""
    if isinstance(value, dict):
        return value
    return {"value": value}
