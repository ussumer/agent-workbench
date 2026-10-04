"""A planning grant must still belong to the current revision at write time."""

from __future__ import annotations

import httpx

from .config import McpSettings
from .grants import Grant, GrantError


async def verify_planning_approval(grant: Grant, settings: McpSettings) -> None:
    if grant.approval_ref is None:
        return  # legacy course grants preserve their existing contract
    if not settings.approval_verify_url or not settings.internal_service_token:
        raise GrantError("APPROVAL_VERIFICATION_UNAVAILABLE", "planning approval verification is required")
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(settings.read_timeout, connect=settings.connect_timeout),
            trust_env=False,
        ) as client:
            response = await client.post(
                settings.approval_verify_url,
                headers={"x-internal-service-token": settings.internal_service_token},
                json={"owner_user_id": grant.owner, **grant.approval_ref,
                      "tool_name": grant.tool, "target": grant.target,
                      "payload_sha256": grant.payload_sha256, "operation_id": grant.operation_id},
            )
        body = response.json()
        if response.status_code != 200:
            raise GrantError("APPROVAL_REQUIRED", "planning approval is no longer executable")
        data = body.get("data", {})
        if (data.get("operation_id") != grant.operation_id
                or data.get("status") not in ("executing", "executed")):
            raise GrantError("GRANT_MISMATCH", "planning approval verification disagrees")
    except (httpx.HTTPError, ValueError, AttributeError, TypeError) as exc:
        raise GrantError("APPROVAL_VERIFICATION_UNAVAILABLE", "planning approval verification failed") from exc
