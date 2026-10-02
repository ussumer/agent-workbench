"""Temporary probe: how does FastMCP 1.30 expose the HTTP request inside a tool?"""

import inspect

from mcp.server.fastmcp import Context, FastMCP

print("FastMCP methods:", [n for n in dir(FastMCP) if not n.startswith("_")])
print()
print("Context methods:", [n for n in dir(Context) if not n.startswith("_")])
print()
print("Context.request_context:", inspect.signature(Context.request_context.fget))
try:
    from mcp.server.fastmcp.server import RequestContext

    print("RequestContext fields:", getattr(RequestContext, "__annotations__", {}))
except Exception as exc:  # noqa: BLE001
    print("RequestContext import failed:", exc)

print()
print("FastMCP.streamable_http_app:", inspect.signature(FastMCP.streamable_http_app))
print("FastMCP.run:", inspect.signature(FastMCP.run))
print("FastMCP.run_streamable_http_async:", inspect.signature(FastMCP.run_streamable_http_async))
print("FastMCP.settings:", [n for n in dir(FastMCP(  # noqa
    "probe").settings) if not n.startswith("_")])
