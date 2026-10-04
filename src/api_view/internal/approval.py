"""The approval verification endpoint the MCP gateway calls before a write.

Why this endpoint exists at all: the grant's signature already proves the API issued it, and
it already binds the owner, tool, target, operation id and payload hash. Verifying the
signature is therefore enough to prevent forgery — but not enough to prevent *withdrawal*.
Between approval and execution, the record can be rejected, expired or superseded, and a
signature minted ten minutes ago would still verify. Asking the API about the recorded
approval closes that window.

Authentication is a dedicated service token, not the user session and not the grant secret:
the gateway must be able to call this, and a token that could also mint grants would make
the check pointless.
"""

from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from agent.approval.models import ApprovalError
from agent.approval.service import ApprovalService

LOGGER = logging.getLogger("rush_harness.internal.approval")

#: The header the gateway presents. Separate from the ERP service token on purpose.
INTERNAL_TOKEN_HEADER = "x-internal-service-token"

ROUTE_PREFIX = "/internal/approvals"


@dataclass(frozen=True)
class VerificationRequest:
    owner_user_id: str
    thread_id: str
    interrupt_id: str
    tool_name: str
    target: str
    payload_sha256: str
    operation_id: str

    @classmethod
    def parse(cls, body: Any) -> VerificationRequest:
        if not isinstance(body, dict):
            raise ApprovalError("INVALID_ARGUMENT", "request body must be an object")
        required = (
            "owner_user_id",
            "thread_id",
            "interrupt_id",
            "tool_name",
            "target",
            "payload_sha256",
            "operation_id",
        )
        missing = [key for key in required if not body.get(key)]
        if missing:
            raise ApprovalError(
                "INVALID_ARGUMENT", f"missing field(s): {', '.join(missing)}"
            )
        return cls(**{key: str(body[key]) for key in required})


def _error(status_code: int, code: str, message: str, request_id: str = "") -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": {}}, "request_id": request_id},
    )


def build_internal_router(*, service: ApprovalService, service_token: str) -> APIRouter:
    """Routes for the gateway, guarded by ``service_token``.

    Every response is scrubbed: only the operation id and status leave this endpoint. The
    frozen payload is not returned, because the caller already has it — it sent it — and
    returning it would turn this into a way to read other users' pending orders.
    """
    router = APIRouter(prefix=ROUTE_PREFIX, tags=["internal"])

    def authorised(presented: str | None) -> bool:
        if not service_token:
            return False
        return bool(presented) and hmac.compare_digest(presented, service_token)

    @router.post("/verify")
    async def verify(
        request: Request,
        x_internal_service_token: str | None = Header(default=None, alias=INTERNAL_TOKEN_HEADER),
    ) -> JSONResponse:
        if not authorised(x_internal_service_token):
            LOGGER.warning("rejected an internal verification with a bad or absent token")
            return _error(403, "FORBIDDEN", "internal service token is required")

        try:
            body = await request.json()
        except (ValueError, TypeError):
            return _error(400, "INVALID_ARGUMENT", "request body is not valid JSON")

        try:
            parsed = VerificationRequest.parse(body)
        except ApprovalError as failure:
            return _error(400, failure.code, str(failure))

        try:
            action = service.verify_internal(
                owner_user_id=parsed.owner_user_id,
                thread_id=parsed.thread_id,
                interrupt_id=parsed.interrupt_id,
                tool_name=parsed.tool_name,
                target=parsed.target,
                payload_sha256=parsed.payload_sha256,
                operation_id=parsed.operation_id,
            )
        except ApprovalError as failure:
            status = 404 if failure.code == "ACTION_NOT_FOUND" else 409
            return _error(status, failure.code, str(failure))

        return JSONResponse(
            status_code=200,
            content={
                "data": {"operation_id": action.operation_id, "status": str(action.status)},
                "request_id": "",
            },
        )

    return router


__all__ = ["INTERNAL_TOKEN_HEADER", "ROUTE_PREFIX", "VerificationRequest", "build_internal_router"]
