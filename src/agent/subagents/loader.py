"""Load and validate sub-agent configurations from YAML.

The loader is where authority is decided, so it is deliberately strict:

* A configuration must resolve its patterns to **exactly** the reviewed ``expected_tools``
  snapshot. A pattern that matches nothing means the config is stale; a pattern that now
  matches more means the catalogue grew a tool that nobody re-reviewed. Both abort
  startup, because a sub-agent that quietly gained a capability is far worse than one that
  refuses to start.
* Names are resolved against the MCP catalogue, never against a substring of it, so
  ``order_*`` cannot be used to reach a write tool that merely happens to be spelled
  similarly.
* A scope that may not write is checked again after resolution, independent of the
  snapshot, so the "analyst has no write tools" property does not depend on the snapshot
  being right.

YAML loading only guarantees the property holds at graph rebuild time; it is not a
hot-reload mechanism.
"""

from __future__ import annotations

import fnmatch
import logging
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from agent.schema import SubAgentConfig, SubAgentConfigError

LOGGER = logging.getLogger("rush_harness.subagents.loader")

CONFIGS_DIR = Path(__file__).resolve().parent / "configs"

#: Scopes that must never be granted a write tool.
READ_ONLY_SCOPES: frozenset[str] = frozenset({"procurement-analyst"})

REQUIRED_KEYS: tuple[str, ...] = (
    "name",
    "description",
    "scope",
    "tool_patterns",
    "expected_tools",
    "system_prompt",
)


class SubAgentLoaderError(SubAgentConfigError):
    """The sub-agent configuration set cannot be loaded."""


def _require(mapping: dict[str, Any], key: str, *, source: str) -> Any:
    if key not in mapping or mapping[key] in (None, "", []):
        raise SubAgentLoaderError(f"{source}: required key {key!r} is missing or empty")
    return mapping[key]


def _as_str_tuple(value: Any, *, source: str, key: str) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, Sequence):
        raise SubAgentLoaderError(f"{source}: {key} must be a list of strings")
    items = tuple(str(item).strip() for item in value)
    if any(not item for item in items):
        raise SubAgentLoaderError(f"{source}: {key} contains an empty entry")
    return items


#: Decisions a configuration may offer at an approval. ``edit`` is deliberately absent:
#: changing parameters must become a new pending action, not an in-place amendment of an
#: approval the user already gave.
ALLOWED_DECISIONS: frozenset[str] = frozenset({"approve", "reject", "respond"})


def _interrupt_on(
    raw: Any, *, source: str, name: str
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Parse the ``interrupt_on`` block into a stable, hashable shape."""
    if raw in (None, {}):
        return ()
    if not isinstance(raw, dict):
        raise SubAgentLoaderError(f"{source}: interrupt_on must be a mapping")

    parsed: list[tuple[str, tuple[str, ...]]] = []
    for tool_name, settings in raw.items():
        if settings is True:
            decisions: tuple[str, ...] = ("approve", "reject")
        elif settings is False:
            raise SubAgentLoaderError(
                f"{source}: interrupt_on[{tool_name!r}] is disabled; "
                "a write tool without approval is not an acceptable configuration"
            )
        elif isinstance(settings, dict):
            decisions = _as_str_tuple(
                settings.get("allowed_decisions") or ("approve", "reject"),
                source=source,
                key=f"interrupt_on.{tool_name}.allowed_decisions",
            )
            if "edit" in decisions:
                raise SubAgentLoaderError(
                    f"{source}: interrupt_on[{tool_name!r}] allows 'edit'; "
                    "changed parameters must form a new candidate action and a new approval"
                )
        else:
            raise SubAgentLoaderError(
                f"{source}: interrupt_on[{tool_name!r}] must be true or a mapping"
            )

        unknown = sorted(set(decisions) - ALLOWED_DECISIONS)
        if unknown:
            raise SubAgentLoaderError(
                f"{source}: interrupt_on[{tool_name!r}] has unsupported decisions {unknown}; "
                f"allowed: {sorted(ALLOWED_DECISIONS)}"
            )
        parsed.append((str(tool_name), decisions))
    return tuple(sorted(parsed))


def resolve_tool_patterns(
    patterns: Iterable[str],
    *,
    available: Iterable[str],
    name: str,
) -> tuple[str, ...]:
    """Expand glob patterns against the catalogue, refusing empty matches.

    ``fnmatch`` is used so the pattern is matched against the whole tool name, not as a
    substring: ``order_*`` cannot reach ``order_search_details`` unless the pattern says so,
    and a pattern like ``part`` matches nothing rather than everything containing "part".
    """
    catalogue = tuple(available)
    matched: set[str] = set()
    for pattern in patterns:
        hits = {tool for tool in catalogue if fnmatch.fnmatchcase(tool, pattern)}
        if not hits:
            raise SubAgentLoaderError(
                f"{name}: tool pattern {pattern!r} matched no tool in the catalogue "
                f"({len(catalogue)} known); the configuration is stale"
            )
        matched |= hits
    return tuple(sorted(matched))


def build_config(
    document: dict[str, Any],
    *,
    available_tools: Iterable[str],
    write_tools: Iterable[str],
    source: str = "<mapping>",
) -> SubAgentConfig:
    """Validate one configuration document against the live catalogue."""
    if not isinstance(document, dict):
        raise SubAgentLoaderError(f"{source}: configuration must be a mapping")

    for key in REQUIRED_KEYS:
        _require(document, key, source=source)

    name = str(document["name"]).strip()
    scope = str(document["scope"]).strip()
    patterns = _as_str_tuple(document["tool_patterns"], source=source, key="tool_patterns")
    expected = frozenset(
        _as_str_tuple(document["expected_tools"], source=source, key="expected_tools")
    )
    skills = _as_str_tuple(document.get("skills") or (), source=source, key="skills")

    catalogue = tuple(available_tools)
    unknown = sorted(expected - set(catalogue))
    if unknown:
        raise SubAgentLoaderError(
            f"{name}: expected_tools names tools that are not in the catalogue: {unknown}"
        )

    resolved = frozenset(
        resolve_tool_patterns(patterns, available=catalogue, name=name)
    )

    matched_but_expected = sorted(resolved - expected)
    expected_but_unmatched = sorted(expected - resolved)
    if matched_but_expected or expected_but_unmatched:
        raise SubAgentLoaderError(
            f"{name}: patterns and the reviewed tool snapshot disagree; "
            f"patterns grant {matched_but_expected or '[]'} beyond the snapshot and "
            f"fail to grant {expected_but_unmatched or '[]'} that the snapshot expects. "
            "Re-review the agent's authority and update expected_tools deliberately."
        )

    interrupt_on = _interrupt_on(document.get("interrupt_on"), source=source, name=name)

    try:
        config = SubAgentConfig(
            name=name,
            description=str(document["description"]).strip() if document["description"] else "",
            scope=scope,
            tool_patterns=patterns,
            expected_tools=expected,
            system_prompt=str(document["system_prompt"]),
            skills=skills,
            write_tools=frozenset(write_tools),
            model=(str(document["model"]).strip() if document.get("model") else None),
            interrupt_on=interrupt_on,
        )
    except SubAgentConfigError as failure:
        # Re-raised as a loader error so callers see one failure type, with the file named.
        raise SubAgentLoaderError(f"{source}: {failure}") from failure

    # Checked after resolution and independently of the snapshot: the property "this scope
    # cannot write" must not be able to depend on a snapshot someone edited.
    if config.scope in READ_ONLY_SCOPES and config.grants_write():
        offending = sorted(config.expected_tools & config.write_tools)
        raise SubAgentLoaderError(
            f"{name}: scope {config.scope!r} is read-only but the resolved tool set "
            f"includes write tool(s) {offending}"
        )

    return config


def load_config_file(
    path: Path,
    *,
    available_tools: Iterable[str],
    write_tools: Iterable[str],
) -> SubAgentConfig:
    """Read one YAML file and validate it."""
    if not path.is_file():
        raise SubAgentLoaderError(f"sub-agent config not found: {path}")
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as failure:
        raise SubAgentLoaderError(f"{path.name}: invalid YAML: {failure}") from failure
    return build_config(
        document,
        available_tools=available_tools,
        write_tools=write_tools,
        source=path.name,
    )


def load_all(
    *,
    available_tools: Iterable[str],
    write_tools: Iterable[str],
    directory: Path | None = None,
) -> dict[str, SubAgentConfig]:
    """Load every ``*.yaml`` in the configs directory, keyed by sub-agent name.

    Duplicate names are an error: two configs with the same name would make delegation
    depend on filesystem ordering.
    """
    root = directory or CONFIGS_DIR
    if not root.is_dir():
        raise SubAgentLoaderError(f"sub-agent config directory not found: {root}")

    configs: dict[str, SubAgentConfig] = {}
    for path in sorted(root.glob("*.yaml")):
        config = load_config_file(
            path, available_tools=available_tools, write_tools=write_tools
        )
        if config.name in configs:
            raise SubAgentLoaderError(f"{path.name}: duplicate sub-agent name {config.name!r}")
        configs[config.name] = config
        LOGGER.info("loaded sub-agent %s scope=%s tools=%d", config.name, config.scope, len(config.expected_tools))
    if not configs:
        raise SubAgentLoaderError(f"no sub-agent configs found in {root}")
    return configs


def to_subagents(
    configs: Iterable[SubAgentConfig],
    tools: Sequence[Any],
    *,
    models: Mapping[str, Any] | None = None,
    middleware: Sequence[Any] = (),
    backend: Any | None = None,
    parent_model: Any | None = None,
) -> list[Any]:
    """Turn validated configs into DeepAgents ``SubAgent`` descriptions.

    Each sub-agent receives exactly the tools it declared: the delegation layer never
    passes the parent's full tool list down, which is what would otherwise leak a write
    tool into a read-only analyst.

    ``models`` optionally overrides the model for named sub-agents. Omitting a name means
    "use the parent's model", which is the framework default.
    """
    from deepagents import SubAgent

    from agent.middlewares.skill_discovery import SkillCatalogRefreshMiddleware, configured_sources

    overrides = models or {}
    unknown = sorted(set(overrides) - {config.name for config in configs})
    if unknown:
        raise SubAgentLoaderError(
            f"model overrides name unknown sub-agents: {unknown}"
        )

    by_name = {getattr(tool, "name", None): tool for tool in tools}
    subagents: list[Any] = []
    for config in configs:
        granted = []
        for name in sorted(config.expected_tools):
            tool = by_name.get(name)
            if tool is None:
                raise SubAgentLoaderError(
                    f"{config.name}: resolved tool {name!r} is not present in the provided tools"
                )
            granted.append(tool)
        spec: dict[str, Any] = {
            "name": config.name,
            "description": config.description,
            "system_prompt": config.system_prompt,
            "tools": granted,
        }
        sources, references = configured_sources(config.skills, config.scope)
        spec["skills"] = sources
        if references:
            spec["system_prompt"] += "\n技能参考文件（按需 read_file）：" + "、".join(references)
        scoped_middleware = list(middleware)
        if backend is not None:
            scoped_middleware.append(SkillCatalogRefreshMiddleware(
                backend=backend, sources=sources, scope=config.scope, preset_names=config.skills,
            ))
        override = overrides.get(config.name, config.model)
        if backend is not None and parent_model is not None:
            from agent.middlewares.conversation_archive import build_archived_summarization

            scoped_middleware.append(build_archived_summarization(
                override or parent_model, backend, scope=config.name))
        if override is not None:
            # The key must be absent, not None, when there is no override: the framework
            # reads `spec.get("model", parent_model)`, so an explicit None suppresses the
            # parent-model fallback and the sub-agent ends up with no model at all.
            spec["model"] = override
        if config.interrupt_on:
            spec["interrupt_on"] = config.interrupt_map()
        if scoped_middleware:
            # Applied inside the sub-agent as well as around the parent: a gate that only
            # saw the parent's tool calls would miss the order agent's writes entirely.
            spec["middleware"] = scoped_middleware
        subagents.append(SubAgent(**spec))
    return subagents


__all__ = [
    "CONFIGS_DIR",
    "READ_ONLY_SCOPES",
    "REQUIRED_KEYS",
    "SubAgentLoaderError",
    "build_config",
    "load_all",
    "load_config_file",
    "resolve_tool_patterns",
    "to_subagents",
]
