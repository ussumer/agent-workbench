"""YAML-declared sub-agents and their loader."""

from agent.subagents.loader import (
    CONFIGS_DIR,
    READ_ONLY_SCOPES,
    SubAgentLoaderError,
    build_config,
    load_all,
    load_config_file,
    resolve_tool_patterns,
    to_subagents,
)

__all__ = [
    "CONFIGS_DIR",
    "READ_ONLY_SCOPES",
    "SubAgentLoaderError",
    "build_config",
    "load_all",
    "load_config_file",
    "resolve_tool_patterns",
    "to_subagents",
]
