"""The eight ERP tools exposed over MCP."""

from __future__ import annotations

from typing import Any

from . import catalog, orders

READ_TOOLS = (
    catalog.supplier_query,
    catalog.part_query,
    catalog.part_search,
    catalog.part_by_supplier,
    catalog.inventory_warning,
    catalog.order_search_details,
)

WRITE_TOOLS = (
    orders.order_create,
    orders.order_update,
)

ALL_TOOLS = READ_TOOLS + WRITE_TOOLS


def register_all_tools(mcp: Any) -> None:
    """Register exactly the eight contract tools, taking each name from its function."""
    for tool in ALL_TOOLS:
        mcp.tool()(tool)
