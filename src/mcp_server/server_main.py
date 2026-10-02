"""Gateway entry point: Streamable HTTP MCP server exposing the eight ERP tools.

Run with::

    PYTHONPATH=src python -m mcp_server.server_main

Configuration comes from the environment (see :mod:`mcp_server.config`). The
process refuses to start if the service token or the grant secret is missing, or
if the registered tool surface does not match the contract.
"""

from __future__ import annotations

import contextlib
import logging
import sys
from collections.abc import AsyncIterator

from mcp.server.fastmcp import FastMCP

from .config import ConfigurationError, McpSettings
from .deps import GatewayRuntime
from .http_base import ErpClient
from .tools import register_all_tools
from .tools.registry import validate_tool_surface

LOGGER = logging.getLogger("rush_harness.mcp")

INSTRUCTIONS = """\
采购 ERP 网关。提供八个工具：
- 只读：supplier_query、part_query、part_search、part_by_supplier、inventory_warning、
  order_search_details
- 写：order_create、order_update（必须已获得用户审批）

订单的归属来自运行时可信身份，工具参数里没有 actor、token 或 operation_id。
写工具在没有有效审批授权时会直接拒绝，不会先写后报错。
"""


def build_server(settings: McpSettings) -> FastMCP:
    """Create the FastMCP application with its lifespan-managed resources."""

    @contextlib.asynccontextmanager
    async def lifespan(app: FastMCP) -> AsyncIterator[GatewayRuntime]:
        # A tool surface that does not match the contract must abort startup rather than serve.
        names = await validate_tool_surface(app)
        LOGGER.info("tool surface validated: %s", ", ".join(names))

        erp = ErpClient(settings)
        await erp.start()
        LOGGER.info(
            "gateway ready: erp=%s timeouts(%s) tools=%d",
            settings.erp_base_url,
            settings.timeout_description(),
            len(names),
        )
        try:
            yield GatewayRuntime(erp=erp, settings=settings)
        finally:
            await erp.aclose()
            LOGGER.info("gateway stopped: erp connection pool closed")

    mcp = FastMCP(
        name="procurement-erp-gateway",
        instructions=INSTRUCTIONS,
        lifespan=lifespan,
        host=settings.host,
        port=settings.port,
        streamable_http_path=settings.streamable_http_path,
    )
    register_all_tools(mcp)
    return mcp


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        settings = McpSettings.from_env()
    except ConfigurationError as failure:
        print(f"mcp gateway configuration error: {failure}", file=sys.stderr)
        return 2

    mcp = build_server(settings)
    mcp.run(transport="streamable-http")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
