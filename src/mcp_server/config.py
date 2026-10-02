"""Gateway configuration, read from the environment.

Nothing here has an insecure default: if the service token or the grant secret is
missing, the gateway refuses to start rather than falling back to a shared
well-known value.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

DEFAULT_ERP_BASE_URL = "http://localhost:8080"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


class ConfigurationError(RuntimeError):
    """Raised when required configuration is absent or unusable."""


@dataclass(frozen=True)
class McpSettings:
    """Timeouts follow contracts/mcp.md: connect 3s, read 15s, 20s per-tool budget."""

    erp_base_url: str
    service_token: str
    grant_secret: str
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    connect_timeout: float = 3.0
    read_timeout: float = 15.0
    tool_budget: float = 20.0
    read_retries: int = 2
    streamable_http_path: str = "/mcp"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> McpSettings:
        source = dict(os.environ if env is None else env)

        erp_base_url = source.get("ERP_BASE_URL", DEFAULT_ERP_BASE_URL).rstrip("/")
        service_token = (source.get("ERP_SERVICE_TOKEN") or "").strip()
        if not service_token:
            raise ConfigurationError(
                "ERP_SERVICE_TOKEN is required: the ERP refuses unauthenticated callers and the "
                "gateway must never invent a token"
            )

        grant_secret = (source.get("MCP_GRANT_SECRET") or "").strip()
        if not grant_secret:
            raise ConfigurationError(
                "MCP_GRANT_SECRET is required: without it the gateway cannot verify an approval "
                "grant, and write tools would have to be trusted blindly"
            )

        return cls(
            erp_base_url=erp_base_url,
            service_token=service_token,
            grant_secret=grant_secret,
            host=source.get("MCP_HOST", DEFAULT_HOST),
            port=int(source.get("MCP_PORT", DEFAULT_PORT)),
            connect_timeout=float(source.get("MCP_CONNECT_TIMEOUT", 3.0)),
            read_timeout=float(source.get("MCP_READ_TIMEOUT", 15.0)),
            tool_budget=float(source.get("MCP_TOOL_BUDGET", 20.0)),
            read_retries=int(source.get("MCP_READ_RETRIES", 2)),
            streamable_http_path=source.get("MCP_STREAMABLE_PATH", "/mcp"),
        )

    def timeout_description(self) -> str:
        return (
            f"connect={self.connect_timeout}s read={self.read_timeout}s "
            f"budget={self.tool_budget}s retries={self.read_retries}"
        )
