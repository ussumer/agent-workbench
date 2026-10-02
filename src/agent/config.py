"""Model and service configuration, read only from the environment.

The model identity is **never inferred from a product shorthand**. There is no
default ``MODEL_ID``: an unset model is a configuration error, not a licence to guess
a public model name, a wire protocol or a context limit. Whatever the provider reports
back is recorded as evidence instead.

``ModelConfig.redacted()`` is the only supported way to surface model configuration:
it is what receipts, fixtures and log lines use, so a key cannot be committed by
accident.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from agent.env_utils import (
    MissingConfiguration,
    capability_satisfied,
    load_env,
    redact,
    require,
    secret_values,
)


def _optional_int(value: str | None) -> int | None:
    if value is None or not str(value).strip():
        return None
    try:
        return int(str(value).strip())
    except ValueError as failure:
        raise MissingConfiguration(
            "model", [f"MODEL_MAX_TOKENS is not an integer: {value}"]
        ) from failure

#: Wire protocol spoken to the model endpoint. The runtime is configured with a
#: generic base URL, and the client used is OpenAI-compatible.
DEFAULT_PROTOCOL = "openai-compatible"

#: Documented local fallbacks for *addresses* (never for credentials).
DEFAULT_SERVICE_ADDRESSES: dict[str, str] = {
    "ERP_BASE_URL": "http://localhost:8080",
    "MCP_BASE_URL": "http://localhost:8000",
    "FIXTURES_BASE_URL": "http://localhost:8088",
    "OPENSANDBOX_BASE_URL": "http://localhost:18080",
    "AGENT_PROTOCOL_BASE_URL": "http://localhost:8123",
}


@dataclass(frozen=True)
class ModelConfig:
    """Everything needed to build a chat model, and nothing guessed."""

    model_id: str
    base_url: str
    api_key: str
    protocol: str = DEFAULT_PROTOCOL
    temperature: float = 0.0
    max_tokens: int | None = None
    timeout_seconds: float = 120.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ModelConfig:
        resolved_env = dict(load_env() if env is None else env)
        api_key, base_url, model_id = require(
            resolved_env, (("MODEL_API_KEY", "OPENAI_API_KEY"),
                           ("MODEL_BASE_URL", "OPENAI_BASE_URL"),
                           ("MODEL_ID", "MODEL_NAME")),
            capability="model",
        )
        return cls(
            model_id=model_id,
            base_url=base_url.rstrip("/"),
            api_key=api_key,
            protocol=resolved_env.get("MODEL_PROTOCOL", DEFAULT_PROTOCOL),
            temperature=float(resolved_env.get("MODEL_TEMPERATURE", "0") or 0),
            max_tokens=_optional_int(resolved_env.get("MODEL_MAX_TOKENS")),
            timeout_seconds=float(resolved_env.get("MODEL_TIMEOUT_SECONDS", "120") or 120),
        )

    def create_chat_model(self) -> Any:
        """Build the chat model. Imported lazily so config stays import-light."""
        from langchain_openai import ChatOpenAI

        kwargs: dict[str, Any] = {
            "model": self.model_id,
            "base_url": self.base_url,
            "api_key": self.api_key,
            "temperature": self.temperature,
            "timeout": self.timeout_seconds,
        }
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens
        return ChatOpenAI(**kwargs)

    def redacted(self) -> dict[str, Any]:
        """Configuration safe to print or commit."""
        return {
            "model_id": self.model_id,
            "base_url": self.base_url,
            "protocol": self.protocol,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout_seconds": self.timeout_seconds,
            "api_key": redact(self.api_key, {self.api_key}),
        }


@dataclass(frozen=True)
class ServiceAddresses:
    """Where the local services live. Addresses have documented defaults."""

    erp_base_url: str = DEFAULT_SERVICE_ADDRESSES["ERP_BASE_URL"]
    mcp_base_url: str = DEFAULT_SERVICE_ADDRESSES["MCP_BASE_URL"]
    fixtures_base_url: str = DEFAULT_SERVICE_ADDRESSES["FIXTURES_BASE_URL"]
    opensandbox_base_url: str = DEFAULT_SERVICE_ADDRESSES["OPENSANDBOX_BASE_URL"]
    agent_protocol_base_url: str = DEFAULT_SERVICE_ADDRESSES["AGENT_PROTOCOL_BASE_URL"]

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ServiceAddresses:
        resolved = dict(load_env() if env is None else env)

        def address(variable: str) -> str:
            return (resolved.get(variable) or DEFAULT_SERVICE_ADDRESSES[variable]).rstrip("/")

        return cls(
            erp_base_url=address("ERP_BASE_URL"),
            mcp_base_url=address("MCP_BASE_URL"),
            fixtures_base_url=address("FIXTURES_BASE_URL"),
            opensandbox_base_url=address("OPENSANDBOX_BASE_URL"),
            agent_protocol_base_url=address("AGENT_PROTOCOL_BASE_URL"),
        )


@dataclass(frozen=True)
class AppConfig:
    """The runtime configuration one process needs."""

    model: ModelConfig
    services: ServiceAddresses = field(default_factory=ServiceAddresses)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AppConfig:
        resolved = dict(load_env() if env is None else env)
        return cls(
            model=ModelConfig.from_env(resolved),
            services=ServiceAddresses.from_env(resolved),
        )


def capability_report(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Which capabilities this environment can satisfy, with values redacted.

    Used by the blocked-capability path: a missing credential must produce a clear,
    secret-free diagnosis rather than a stack trace.
    """
    resolved = dict(load_env() if env is None else env)
    secrets = secret_values(resolved)
    report: dict[str, Any] = {}
    for capability in ("model", "search", "chart", "sandbox"):
        satisfied, missing = capability_satisfied(resolved, capability)
        report[capability] = {
            "satisfied": satisfied,
            "missing": missing,
            "diagnosis": redact(
                f"capability {capability}: "
                + ("ok" if satisfied else "missing " + "; ".join(missing)),
                secrets,
            ),
        }
    return report


def require_model(env: Mapping[str, str] | None = None) -> ModelConfig:
    """Model configuration or a typed :class:`MissingConfiguration`."""
    return ModelConfig.from_env(env)


__all__ = [
    "AppConfig",
    "DEFAULT_PROTOCOL",
    "DEFAULT_SERVICE_ADDRESSES",
    "MissingConfiguration",
    "ModelConfig",
    "ServiceAddresses",
    "capability_report",
    "require_model",
]
