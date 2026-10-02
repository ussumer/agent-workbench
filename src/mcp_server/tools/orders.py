"""Write tools: order creation and whole-order update.

Both are gated on a signed approval grant that binds the owner, the tool, the
target resource, the operation id and the SHA-256 of the frozen request body. The
gateway verifies all of that before it forwards anything to the ERP, so an
unapproved write, a reused grant with changed parameters, or another user's grant
all fail here — at the point of execution, not in a prompt.
"""

from __future__ import annotations

import hashlib
from typing import Any

from mcp.server.fastmcp import Context

from ..deps import caller, erp_client, runtime
from ..grants import GrantError, verify_grant
from ..http_base import error_result
from .registry import (
    OrderLineInput,
    canonical_order_body,
    canonical_update_body,
    create_target,
    update_target,
)

API = "/api/erp/v1"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


async def order_create(
    ctx: Context,
    supplier_id: str,
    currency: str,
    lines: list[OrderLineInput],
    note: str | None = None,
) -> dict[str, Any]:
    """创建采购订单。必须先经用户审批：无有效授权时本工具拒绝执行。"""
    async with caller(ctx) as identity:
        settings = runtime(ctx).settings
        frozen = canonical_order_body(
            supplier_id=supplier_id, currency=currency, lines=lines, note=note
        )
        try:
            grant = verify_grant(
                identity.grant,
                secret=settings.grant_secret,
                expect_owner=identity.actor_id,
                expect_tool="order_create",
                expect_target=create_target(),
                expect_payload_sha256=_sha256(frozen),
            )
        except GrantError as failure:
            return error_result(failure.code, str(failure), retryable=False)

        return await erp_client(ctx).call(
            "POST",
            f"{API}/orders",
            actor=identity.actor_id,
            frozen_body=frozen,
            operation_id=grant.operation_id,
            # The same bytes under the same operation id are a replay, which the ERP recognises.
            idempotent_resend=True,
        )


async def order_update(
    ctx: Context,
    order_id: str,
    expected_version: int,
    supplier_id: str,
    currency: str,
    lines: list[OrderLineInput],
    note: str | None = None,
) -> dict[str, Any]:
    """整单修改采购订单。必须针对该订单重新审批：授权与订单号、版本绑定。"""
    async with caller(ctx) as identity:
        settings = runtime(ctx).settings
        frozen = canonical_update_body(
            expected_version=expected_version,
            supplier_id=supplier_id,
            currency=currency,
            lines=lines,
            note=note,
        )
        try:
            grant = verify_grant(
                identity.grant,
                secret=settings.grant_secret,
                expect_owner=identity.actor_id,
                expect_tool="order_update",
                expect_target=update_target(order_id),
                expect_payload_sha256=_sha256(frozen),
            )
        except GrantError as failure:
            return error_result(failure.code, str(failure), retryable=False)

        return await erp_client(ctx).call(
            "PUT",
            f"{API}/orders/{order_id}",
            actor=identity.actor_id,
            frozen_body=frozen,
            operation_id=grant.operation_id,
            idempotent_resend=True,
        )
