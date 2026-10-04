"""Tool catalogue, canonical payload building and startup surface validation.

The canonical builders are the single definition of "the frozen bytes" for a
write. The approval service hashes exactly these bytes, and the gateway sends the
same bytes to the ERP unchanged — that identity is what makes the idempotency
ledger and the grant binding meaningful.
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# catalogue
# --------------------------------------------------------------------------- #

SUPPLIER_QUERY = "supplier_query"
PART_QUERY = "part_query"
PART_SEARCH = "part_search"
PART_BY_SUPPLIER = "part_by_supplier"
INVENTORY_WARNING = "inventory_warning"
ORDER_SEARCH_DETAILS = "order_search_details"
ORDER_CREATE = "order_create"
ORDER_UPDATE = "order_update"

ERP_READ_TOOLS: tuple[str, ...] = (
    SUPPLIER_QUERY,
    PART_QUERY,
    PART_SEARCH,
    PART_BY_SUPPLIER,
    INVENTORY_WARNING,
    ORDER_SEARCH_DETAILS,
)

ERP_WRITE_TOOLS: tuple[str, ...] = (ORDER_CREATE, ORDER_UPDATE)

ERP_TOOLS: tuple[str, ...] = ERP_READ_TOOLS + ERP_WRITE_TOOLS

# Role expectations from docs/plan/architecture.md. The analyst cannot write; the order agent can.
ANALYST_TOOL_SET: frozenset[str] = frozenset(ERP_READ_TOOLS)
ORDER_TOOL_SET: frozenset[str] = frozenset(ERP_TOOLS)

# Nothing in this set may ever appear in a tool's model-visible input schema: these are injected
# from the trusted runtime, and a model must not be able to name them.
FORBIDDEN_SCHEMA_PROPERTIES: frozenset[str] = frozenset(
    {
        "actor",
        "actor_id",
        "user",
        "user_id",
        "owner",
        "owner_user_id",
        "token",
        "service_token",
        "operation_id",
        "grant",
        "approval",
        "approval_grant",
    }
)


class OrderLineInput(BaseModel):
    """One requested order line, as the model may express it."""

    part_id: str = Field(description="物料 ID，例如 P001", pattern=r"^P\d{3}$")
    quantity: int = Field(description="数量，1..10000", ge=1, le=10000)
    unit_price: str = Field(
        description="两位小数单价字符串，例如 \"25.50\"；不接受三位小数",
        pattern=r"^(?:0\.(?:0[1-9]|[1-9][0-9])|[1-9][0-9]{0,5}\.[0-9]{2})$",
    )


def _canonical(payload: dict[str, Any]) -> bytes:
    """Deterministic UTF-8 bytes: sorted keys, no whitespace, note normalised to a string."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def canonical_order_body(
    *,
    supplier_id: str,
    currency: str,
    lines: Sequence[OrderLineInput],
    note: str | None,
) -> bytes:
    """Frozen bytes for ``order_create`` (contracts/mcp.md: 省略 note 统一为空字符串)."""
    return _canonical(
        {
            "supplier_id": supplier_id,
            "currency": currency,
            "lines": [
                {
                    "part_id": line.part_id,
                    "quantity": line.quantity,
                    "unit_price": line.unit_price,
                }
                for line in lines
            ],
            "note": note or "",
        }
    )


def canonical_update_body(
    *,
    expected_version: int,
    supplier_id: str,
    currency: str,
    lines: Sequence[OrderLineInput],
    note: str | None,
) -> bytes:
    """Frozen bytes for ``order_update``. The target order id is bound separately, in the grant."""
    return _canonical(
        {
            "expected_version": expected_version,
            "supplier_id": supplier_id,
            "currency": currency,
            "lines": [
                {
                    "part_id": line.part_id,
                    "quantity": line.quantity,
                    "unit_price": line.unit_price,
                }
                for line in lines
            ],
            "note": note or "",
        }
    )


def create_target() -> str:
    """Grant target for a new order."""
    return "orders"


def update_target(order_id: str) -> str:
    """Grant target for an order edit; binds the grant to one specific order."""
    return f"orders/{order_id}"


class ToolSurfaceError(RuntimeError):
    """The registered tool surface does not match the contract."""


async def validate_tool_surface(mcp: Any) -> tuple[str, ...]:
    """Fail startup unless exactly the eight contract tools are registered and safe.

    A missing tool, an unexpected tool, or a model-visible parameter that should be
    runtime-injected aborts startup. Registering something silently would be worse
    than not starting at all.
    """
    tools = await mcp.list_tools()
    names = tuple(tool.name for tool in tools)

    missing = sorted(set(ERP_TOOLS) - set(names))
    unexpected = sorted(set(names) - set(ERP_TOOLS))
    if missing or unexpected:
        raise ToolSurfaceError(
            f"tool surface does not match the contract; missing={missing} unexpected={unexpected}"
        )

    problems: list[str] = []
    for tool in tools:
        schema = tool.inputSchema or {}
        properties = set((schema.get("properties") or {}).keys())
        exposed = sorted(properties & FORBIDDEN_SCHEMA_PROPERTIES)
        if exposed:
            problems.append(f"{tool.name} exposes runtime-injected parameter(s): {exposed}")
    if problems:
        raise ToolSurfaceError("; ".join(problems))

    return names
