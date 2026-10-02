"""Refresh the official skill catalogue after sync/restore, once per graph invocation."""

from __future__ import annotations

import posixpath
from collections.abc import Sequence
from typing import Any

from deepagents.middleware.skills import SkillsMiddleware
from langchain_core.runnables import RunnableConfig

from agent.middlewares.skills_sync import DEFAULT_TARGET_ROOT, load_manifest
from agent.middlewares.user_skills_restore import USER_SKILLS_ROOT

# A reference document has no SKILL.md and must not masquerade as an executable skill.
REFERENCES = {"chart_params": "/skills/procurement/chart_params.md"}
MAIN_SKILL_SOURCES = ("/skills/main", f"{USER_SKILLS_ROOT}/main")


def configured_sources(names: Sequence[str], scope: str) -> tuple[list[str], list[str]]:
    """Resolve YAML slugs to *parent directories*, as required by SkillsMiddleware."""
    manifest = load_manifest()
    by_name = {skill.name: skill for skill in manifest.skills}
    sources: list[str] = []
    references: list[str] = []
    for name in names:
        if name in REFERENCES:
            references.append(REFERENCES[name])
            continue
        if name not in by_name:
            raise ValueError(f"unknown configured skill: {name!r}")
        parent = posixpath.dirname(by_name[name].directory)
        source = f"{DEFAULT_TARGET_ROOT}/{parent}"
        if source not in sources:
            sources.append(source)
    sources.append(f"{USER_SKILLS_ROOT}/{scope}")
    return sources, references


class SkillCatalogRefreshMiddleware(SkillsMiddleware):
    """Keep SDK parsing/prompt rendering, but invalidate its checkpointed catalogue.

    The SDK's initial SkillsMiddleware runs before application sync/restore hooks.
    This middleware is appended after those hooks and supplies the final catalogue.
    It does not add a second prompt: the SDK's existing wrapper reads this state.
    """

    def __init__(self, *, backend: Any, sources: Sequence[str], scope: str,
                 preset_names: Sequence[str]) -> None:
        super().__init__(backend=backend, sources=sources, system_prompt=None)
        self._user_root = f"{USER_SKILLS_ROOT}/{scope}/"
        self._preset_names = frozenset(preset_names)

    def _fresh_state(self, state: Any) -> dict[str, Any]:
        fresh = dict(state)
        fresh.pop("skills_metadata", None)
        fresh.pop("skills_load_errors", None)
        return fresh

    def _scoped(self, update: Any) -> dict[str, Any]:
        update = dict(update or {})
        update["skills_metadata"] = [
            skill for skill in update.get("skills_metadata", [])
            if str(skill["path"]).startswith(self._user_root)
            or (not str(skill["path"]).startswith(USER_SKILLS_ROOT + "/")
                and skill["name"] in self._preset_names)
        ]
        update.setdefault("skills_load_errors", [])
        return update

    def before_agent(self, state: Any, runtime: Any, config: RunnableConfig) -> dict[str, Any]:
        return self._scoped(super().before_agent(self._fresh_state(state), runtime, config))

    async def abefore_agent(self, state: Any, runtime: Any, config: RunnableConfig) -> dict[str, Any]:
        update = await super().abefore_agent(self._fresh_state(state), runtime, config)
        return self._scoped(update)
