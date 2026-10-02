"""Schemas for the agent graph: sub-agent configuration and sub-agent returns.

Two contracts live here.

**Sub-agent configuration** describes what a sub-agent *is allowed to do*. The tool set is
declared twice on purpose: as glob patterns (the course-style naming the configuration is
written in) and as the reviewed snapshot of the exact tools those patterns must resolve
to. A pattern that matches nothing, or that matches something the catalogue grew later,
fails at startup instead of silently widening the agent's authority. That is the whole
point of keeping both.

**Sub-agent return** is the structured envelope a delegated agent replies with. Parsing
never invents a success: a payload missing a required key raises, naming the missing keys,
because an empty "successful" result is indistinguishable from a real one downstream.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from agent.main_agent import MEMORIES_ROOT, PERSISTED_SKILLS_ROOT, SKILLS_ROOT

SCHEMA_VERSION = 1

#: Slugs follow the same rule as skill names, so a config name cannot be anything odd.
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SLUG_MAX_LENGTH = 64

#: Scopes a sub-agent may be assigned to. Mirrors the skill assignment scopes.
KNOWN_SCOPES: tuple[str, ...] = ("main", "procurement-analyst", "procurement-order")

#: Fields every sub-agent return must carry (contracts/skills-memory.md).
SUBAGENT_RETURN_FIELDS: tuple[str, ...] = (
    "summary",
    "facts",
    "artifact_ids",
    "warnings",
    "next_action",
)

#: Fields that must be lists of strings when present.
SUBAGENT_LIST_FIELDS: tuple[str, ...] = ("facts", "artifact_ids", "warnings")

#: Virtual paths a sub-agent may touch, from contracts/storage-sandbox.md. Stated once so
#: the file-sharing boundary is a value the tests can assert against rather than prose.
SHARED_WRITE_ROOTS: tuple[str, ...] = (SKILLS_ROOT,)
SHARED_READ_ROOTS: tuple[str, ...] = (SKILLS_ROOT, MEMORIES_ROOT, PERSISTED_SKILLS_ROOT)


class SubAgentConfigError(ValueError):
    """A sub-agent configuration is not publishable as written."""


class SubAgentReturnError(ValueError):
    """A sub-agent returned something that does not satisfy the agreed envelope."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = list(problems)
        super().__init__("sub-agent return is incomplete: " + "; ".join(self.problems))


@dataclass(frozen=True)
class SubAgentConfig:
    """One declarative sub-agent.

    ``expected_tools`` is a reviewed snapshot, not a computed value: when the catalogue
    changes, the mismatch is the signal to re-review the agent's authority.
    """

    name: str
    description: str
    scope: str
    tool_patterns: tuple[str, ...]
    expected_tools: frozenset[str]
    system_prompt: str
    skills: tuple[str, ...] = ()
    write_tools: frozenset[str] = frozenset()
    model: str | None = None
    #: Tools this sub-agent must pause on before executing, in the framework's own
    #: ``interrupt_on`` shape. Declared per sub-agent so approval is a property of the
    #: configuration rather than something the caller has to remember to ask for.
    interrupt_on: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def __post_init__(self) -> None:
        if not SLUG_PATTERN.match(self.name):
            raise SubAgentConfigError(
                f"name {self.name!r} must match {SLUG_PATTERN.pattern}"
            )
        if len(self.name) > SLUG_MAX_LENGTH:
            raise SubAgentConfigError(f"name {self.name!r} exceeds {SLUG_MAX_LENGTH} characters")
        if not self.description.strip():
            raise SubAgentConfigError(f"{self.name}: description is required")
        if not self.tool_patterns:
            raise SubAgentConfigError(f"{self.name}: at least one tool pattern is required")
        if not self.expected_tools:
            raise SubAgentConfigError(f"{self.name}: expected_tools must not be empty")
        if self.scope not in KNOWN_SCOPES:
            raise SubAgentConfigError(
                f"{self.name}: scope {self.scope!r} is not one of {list(KNOWN_SCOPES)}"
            )

        gated = {name for name, _decisions in self.interrupt_on}
        ungated = gated - self.expected_tools
        if ungated:
            raise SubAgentConfigError(
                f"{self.name}: interrupt_on names tools this agent cannot call: "
                f"{sorted(ungated)}"
            )
        # A write tool that is not gated would execute without approval, which is the one
        # hole this configuration must not be able to describe.
        unguarded = set(self.expected_tools) & set(self.write_tools) - gated
        if unguarded:
            raise SubAgentConfigError(
                f"{self.name}: write tool(s) {sorted(unguarded)} are granted without "
                "interrupt_on; every write must pause for approval"
            )

    def grants_write(self) -> bool:
        """Whether this configuration can execute a write tool."""
        return bool(self.expected_tools & self.write_tools)

    def interrupt_map(self) -> dict[str, Any]:
        """``interrupt_on`` in the framework's expected shape."""
        return {
            name: {"allowed_decisions": list(decisions)} for name, decisions in self.interrupt_on
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "scope": self.scope,
            "tool_patterns": list(self.tool_patterns),
            "expected_tools": sorted(self.expected_tools),
            "skills": list(self.skills),
            "grants_write": self.grants_write(),
            "model": self.model,
            "interrupt_on": {name: list(decisions) for name, decisions in self.interrupt_on},
        }


@dataclass(frozen=True)
class SubAgentReturn:
    """The structured envelope a delegated sub-agent must reply with."""

    summary: str
    facts: list[str] = field(default_factory=list)
    artifact_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    next_action: str | None = None

    @classmethod
    def parse(cls, payload: Any) -> SubAgentReturn:
        """Build from a mapping, refusing anything incomplete.

        A missing key is reported by name. Returning an empty envelope instead would let a
        malformed delegation look like a clean "nothing to report".
        """
        if not isinstance(payload, Mapping):
            raise SubAgentReturnError([f"expected an object, got {type(payload).__name__}"])

        problems: list[str] = []

        summary = payload.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            problems.append("summary is missing or empty")

        for key in SUBAGENT_LIST_FIELDS:
            if key not in payload:
                problems.append(f"{key} is missing")
                continue
            value = payload[key]
            if value is None:
                continue
            if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
                problems.append(f"{key} must be a list")
                continue
            if any(not isinstance(item, str) for item in value):
                problems.append(f"{key} must contain only strings")

        if "next_action" not in payload:
            problems.append("next_action is missing (use null when there is none)")
        elif payload["next_action"] is not None and not isinstance(payload["next_action"], str):
            problems.append("next_action must be a string or null")

        if problems:
            raise SubAgentReturnError(problems)

        return cls(
            summary=str(summary).strip(),
            facts=_strings(payload.get("facts")),
            artifact_ids=_strings(payload.get("artifact_ids")),
            warnings=_strings(payload.get("warnings")),
            next_action=payload.get("next_action"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "facts": list(self.facts),
            "artifact_ids": list(self.artifact_ids),
            "warnings": list(self.warnings),
            "next_action": self.next_action,
        }


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    return [str(item) for item in value]


def is_within(path: str, roots: Iterable[str]) -> bool:
    """Whether a virtual path sits inside one of the allowed roots."""
    if not path.startswith("/"):
        return False
    for root in roots:
        if path == root or path.startswith(root.rstrip("/") + "/"):
            return True
    return False


__all__ = [
    "KNOWN_SCOPES",
    "SCHEMA_VERSION",
    "SHARED_READ_ROOTS",
    "SHARED_WRITE_ROOTS",
    "SLUG_PATTERN",
    "SUBAGENT_LIST_FIELDS",
    "SUBAGENT_RETURN_FIELDS",
    "SubAgentConfig",
    "SubAgentConfigError",
    "SubAgentReturn",
    "SubAgentReturnError",
    "is_within",
]
