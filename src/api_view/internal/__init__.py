"""Internal endpoints, reachable only with the service token.

These exist so the MCP gateway can confirm a grant against the approval record that
produced it. They are deliberately kept out of the public surface: the token is a separate
secret from anything the model or the browser holds, and the routes are mounted with their
own dependency rather than alongside the user-facing API.
"""

from api_view.internal.approval import INTERNAL_TOKEN_HEADER, build_internal_router

__all__ = ["INTERNAL_TOKEN_HEADER", "build_internal_router"]
