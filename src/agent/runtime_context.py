"""Resolve identity from the trusted runtime or LangGraph invocation configuration."""

from collections.abc import Mapping
from typing import Any

from langgraph.config import get_config


def runtime_owner(runtime: Any) -> str | None:
    context = getattr(runtime, "context", None)
    for source in (context, runtime):
        if isinstance(source, Mapping):
            value = source.get("owner_user_id") or source.get("user_id")
        else:
            value = getattr(source, "owner_user_id", None) or getattr(source, "user_id", None)
        if isinstance(value, str) and value:
            return value
    config = getattr(runtime, "config", None)
    if not config:
        try:
            config = get_config()
        except RuntimeError:
            return None
    configurable = config.get("configurable") or {}
    value = configurable.get("owner_user_id")
    return value if isinstance(value, str) and value else None
