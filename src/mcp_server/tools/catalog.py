"""Read-only ERP tools.

Every tool takes its caller identity from the MCP request context; none of them
accepts an actor, token or operation id as a parameter, so a model cannot choose
whose data it reads.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context

from ..deps import caller, erp_client

API = "/api/erp/v1"


def _page(page: int, page_size: int) -> dict[str, Any]:
    return {"page": page, "page_size": page_size}


async def supplier_query(
    ctx: Context,
    q: str | None = None,
    active: bool | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """查询供应商。q 按名称或 ID 子串匹配；active 过滤启用状态。"""
    async with caller(ctx) as identity:
        params = _page(page, page_size)
        if q is not None:
            params["q"] = q
        if active is not None:
            params["active"] = active
        return await erp_client(ctx).call(
            "GET", f"{API}/suppliers", actor=identity.actor_id, params=params
        )


async def part_query(ctx: Context, part_id: str) -> dict[str, Any]:
    """查询单个物料详情，含当前可供货的启用供应商与目录价。"""
    async with caller(ctx) as identity:
        return await erp_client(ctx).call(
            "GET", f"{API}/parts/{part_id}", actor=identity.actor_id
        )


async def part_search(ctx: Context, q: str, page: int = 1, page_size: int = 20) -> dict[str, Any]:
    """按名称或 SKU 子串搜索物料。"""
    async with caller(ctx) as identity:
        params = _page(page, page_size)
        params["q"] = q
        return await erp_client(ctx).call(
            "GET", f"{API}/parts", actor=identity.actor_id, params=params
        )


async def part_by_supplier(
    ctx: Context, supplier_id: str, page: int = 1, page_size: int = 20
) -> dict[str, Any]:
    """查询某供应商提供的物料及其目录价与交期。"""
    async with caller(ctx) as identity:
        return await erp_client(ctx).call(
            "GET",
            f"{API}/suppliers/{supplier_id}/parts",
            actor=identity.actor_id,
            params=_page(page, page_size),
        )


async def inventory_warning(ctx: Context, page: int = 1, page_size: int = 20) -> dict[str, Any]:
    """查询库存预警：仅 on_hand < warning_threshold 的物料，附建议补货数量。"""
    async with caller(ctx) as identity:
        return await erp_client(ctx).call(
            "GET",
            f"{API}/inventory/warnings",
            actor=identity.actor_id,
            params=_page(page, page_size),
        )


async def order_search_details(
    ctx: Context,
    order_id: str | None = None,
    supplier_id: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """查询当前用户的采购订单（含明细）。只看得到自己的订单。"""
    async with caller(ctx) as identity:
        params = _page(page, page_size)
        if order_id is not None:
            params["order_id"] = order_id
        if supplier_id is not None:
            params["supplier_id"] = supplier_id
        return await erp_client(ctx).call(
            "GET", f"{API}/orders", actor=identity.actor_id, params=params
        )
