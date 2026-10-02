"""Backend composition for the main agent: a three-way virtual filesystem.

Routing (from ``contracts/storage-sandbox.md``):

| Virtual path | Backend | Purpose |
|---|---|---|
| everything else, incl. ``/workspace/``, ``/skills/``, ``/AGENTS.md`` | the user's sandbox proxy | code execution, scratch work |
| ``/memories/`` | Store, namespace ``('memories', user_id)`` | preferences and history references |
| ``/persisted-skills/`` | Store, namespace ``('skills',)`` | published skill packages |

Two different isolation mechanisms are needed, and the difference matters:

* ``/memories/`` is isolated by **namespace**. The namespace contains the user id and is
  bound when the backend is built, so one user's store lookups cannot reach another's.
* ``/persisted-skills/`` is a **shared** namespace whose keys carry a ``users/{id}/``
  prefix. Isolation there cannot come from the namespace, and a prompt telling the model
  to stay in its own directory is not a control. :class:`OwnerScopedStoreBackend`
  enforces it: the mount root is rewritten onto the owner's prefix, and any explicit
  reference to another user's prefix is refused rather than resolved.
"""

from __future__ import annotations

import json
import logging
import posixpath
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from deepagents.backends import CompositeBackend, StoreBackend
from deepagents.backends.protocol import (
    INVALID_PATH,
    PERMISSION_DENIED,
    DeleteResult,
    EditResult,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)

LOGGER = logging.getLogger("rush_harness.backend.scoped")

MEMORIES_ROOT = "/memories"
PERSISTED_SKILLS_ROOT = "/persisted-skills"
SKILLS_ROOT = "/skills"
AGENTS_MD_PATH = "/AGENTS.md"

#: Key segment that separates users inside the shared skills namespace.
USERS_SEGMENT = "users"


class OwnerScopeError(ValueError):
    """A path addressed a different owner's key prefix, or tried to leave the mount."""


def memories_namespace(owner_user_id: str) -> tuple[str, ...]:
    """Namespace for one user's memories. The user id is part of the namespace itself."""
    if not owner_user_id:
        raise OwnerScopeError("owner_user_id is required for the memories namespace")
    return ("memories", owner_user_id)


def persisted_skills_namespace() -> tuple[str, ...]:
    """The course's shared skills namespace; per-user isolation lives in the key prefix."""
    return ("skills",)


def owner_key_prefix(owner_user_id: str) -> str:
    """Key prefix owned by one user inside the shared skills namespace."""
    return f"{USERS_SEGMENT}/{owner_user_id}"


def user_skill_prefix(owner_user_id: str, scope: str, slug: str, version: str) -> str:
    """Full key prefix for one published skill version."""
    return f"{owner_key_prefix(owner_user_id)}/{scope}/{slug}/{version}"


class OwnerScopedStoreBackend:
    """A Store mount whose view is rooted at one owner's key prefix.

    Implements the full DeepAgents backend protocol by explicit delegation. There is no
    ``__getattr__``: a method this wrapper forgets fails visibly instead of silently
    bypassing the scope check.

    A violation is reported through the protocol's own error field on the result object —
    the correct result type for the operation — so it surfaces as a normal tool error
    rather than an exception escaping into the graph.
    """

    def __init__(
        self, inner: Any, *, owner_user_id: str, mount: str = PERSISTED_SKILLS_ROOT
    ) -> None:
        if not owner_user_id:
            raise OwnerScopeError("owner_user_id is required")
        self._inner = inner
        self._owner = owner_user_id
        self._mount = mount.rstrip("/")
        self._prefix = owner_key_prefix(owner_user_id)

    # ------------------------------------------------------------------ scoping

    @property
    def owner_user_id(self) -> str:
        return self._owner

    @property
    def mount(self) -> str:
        return self._mount

    def resolve(self, path: str) -> str:
        """Map a virtual path onto a path inside the owner's prefix.

        The owner's own subtree keeps its real ``users/{id}/...`` shape; anything else is
        a path *relative to the owner's root*, so an ``ls`` of the mount shows only the
        caller's entries even though the underlying namespace is shared.
        """
        if not path or not path.strip():
            raise OwnerScopeError("empty path")

        relative = path
        if relative == self._mount:
            relative = "/"
        elif relative.startswith(self._mount + "/"):
            relative = relative[len(self._mount) :]
        elif not relative.startswith("/"):
            raise OwnerScopeError(f"path must be absolute: {path!r}")

        # Normalise *before* inspecting so `/persisted-skills/a/../../etc` cannot slip past.
        segments = [
            segment
            for segment in posixpath.normpath("/" + relative.lstrip("/")).strip("/").split("/")
            if segment
        ]

        if segments and segments[0] == USERS_SEGMENT:
            if len(segments) < 2:
                raise OwnerScopeError("a users/ path must name an owner")
            if segments[1] != self._owner:
                raise OwnerScopeError(
                    f"path belongs to another owner: {path!r} (mount is scoped to {self._owner!r})"
                )
            return "/" + "/".join(segments)

        if not segments:
            return self._virtual_root()
        return f"/{self._prefix}/{'/'.join(segments)}"

    def _virtual_root(self) -> str:
        return f"/{self._prefix}"

    # ------------------------------------------------------------------ helpers

    def _guard(self, operation: Callable[[], Any], on_error: Callable[[str], Any]) -> Any:
        """Run a delegation, turning a scope violation into a typed error result.

        The human-readable reason is logged, while the result carries the protocol's
        machine-readable code so callers can branch on it.
        """
        try:
            return operation()
        except OwnerScopeError as failure:
            code = self._error_code(failure)
            LOGGER.warning(
                "owner-scope violation mount=%s owner=%s code=%s detail=%s",
                self._mount,
                self._owner,
                code,
                failure,
            )
            return on_error(code)

    @staticmethod
    def _error_code(failure: OwnerScopeError) -> str:
        # An explicit other-owner reference is a permission problem; anything else is a
        # malformed path. Keeping them apart makes an attempted cross-user read visible
        # instead of looking like a typo.
        return PERMISSION_DENIED if "another owner" in str(failure) else INVALID_PATH

    # ---------------------------------------------------------------------- ls

    def ls(self, path: str) -> LsResult:
        return self._guard(lambda: self._inner.ls(self.resolve(path)), lambda c: LsResult(error=c))

    async def als(self, path: str) -> LsResult:
        return await self._guard_async(
            lambda: self._inner.als(self.resolve(path)), lambda c: LsResult(error=c)
        )

    # -------------------------------------------------------------------- read

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return self._guard(
            lambda: self._inner.read(self.resolve(file_path), offset, limit),
            lambda c: ReadResult(error=c),
        )

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return await self._guard_async(
            lambda: self._inner.aread(self.resolve(file_path), offset, limit),
            lambda c: ReadResult(error=c),
        )

    # ------------------------------------------------------------------- write

    def write(self, file_path: str, content: str) -> WriteResult:
        return self._guard(
            lambda: self._inner.write(self.resolve(file_path), content),
            lambda c: WriteResult(error=c),
        )

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return await self._guard_async(
            lambda: self._inner.awrite(self.resolve(file_path), content),
            lambda c: WriteResult(error=c),
        )

    # -------------------------------------------------------------------- edit

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002 - protocol signature
    ) -> EditResult:
        return self._guard(
            lambda: self._inner.edit(self.resolve(file_path), old_string, new_string, replace_all),
            lambda c: EditResult(error=c),
        )

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002 - protocol signature
    ) -> EditResult:
        return await self._guard_async(
            lambda: self._inner.aedit(self.resolve(file_path), old_string, new_string, replace_all),
            lambda c: EditResult(error=c),
        )

    # -------------------------------------------------------------------- glob

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        return self._guard(
            lambda: self._inner.glob(pattern, self.resolve(path or self._mount)),
            lambda c: GlobResult(error=c),
        )

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return await self._guard_async(
            lambda: self._inner.aglob(pattern, self.resolve(path or self._mount)),
            lambda c: GlobResult(error=c),
        )

    # -------------------------------------------------------------------- grep

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        return self._guard(
            lambda: self._inner.grep(
                pattern, self.resolve(path or self._mount), glob, max_count=max_count
            ),
            lambda c: GrepResult(error=c),
        )

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        return await self._guard_async(
            lambda: self._inner.agrep(
                pattern, self.resolve(path or self._mount), glob, max_count=max_count
            ),
            lambda c: GrepResult(error=c),
        )

    # ------------------------------------------------------------------ delete

    def delete(self, file_path: str) -> DeleteResult:
        return self._guard(
            lambda: self._inner.delete(self.resolve(file_path)),
            lambda c: DeleteResult(error=c),
        )

    async def adelete(self, file_path: str) -> DeleteResult:
        return await self._guard_async(
            lambda: self._inner.adelete(self.resolve(file_path)),
            lambda c: DeleteResult(error=c),
        )

    # ---------------------------------------------------------------- transfer

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        try:
            resolved = [(self.resolve(path), content) for path, content in files]
        except OwnerScopeError as failure:
            code = self._error_code(failure)
            LOGGER.warning("owner-scope violation on upload owner=%s code=%s", self._owner, code)
            return [FileUploadResponse(path=path, error=code) for path, _ in files]
        return self._guard(lambda: self._inner.upload_files(resolved), lambda c: [])

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        try:
            resolved = [(self.resolve(path), content) for path, content in files]
        except OwnerScopeError as failure:
            code = self._error_code(failure)
            LOGGER.warning("owner-scope violation on upload owner=%s code=%s", self._owner, code)
            return [FileUploadResponse(path=path, error=code) for path, _ in files]
        return await self._guard_async(lambda: self._inner.aupload_files(resolved), lambda c: [])

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        try:
            resolved = [self.resolve(path) for path in paths]
        except OwnerScopeError as failure:
            code = self._error_code(failure)
            LOGGER.warning("owner-scope violation on download owner=%s code=%s", self._owner, code)
            return [
                FileDownloadResponse(path=path, content=None, error=code) for path in paths
            ]
        return self._guard(lambda: self._inner.download_files(resolved), lambda c: [])

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        try:
            resolved = [self.resolve(path) for path in paths]
        except OwnerScopeError as failure:
            code = self._error_code(failure)
            LOGGER.warning("owner-scope violation on download owner=%s code=%s", self._owner, code)
            return [
                FileDownloadResponse(path=path, content=None, error=code) for path in paths
            ]
        return await self._guard_async(
            lambda: self._inner.adownload_files(resolved), lambda c: []
        )

    # -------------------------------------------------------------- async guard

    async def _guard_async(
        self, operation: Callable[[], Any], on_error: Callable[[str], Any]
    ) -> Any:
        try:
            return await operation()
        except OwnerScopeError as failure:
            code = self._error_code(failure)
            LOGGER.warning(
                "owner-scope violation mount=%s owner=%s code=%s detail=%s",
                self._mount,
                self._owner,
                code,
                failure,
            )
            return on_error(code)


def build_virtual_backend(
    *,
    sandbox_backend: Any,
    store: Any,
    owner_user_id: str,
) -> CompositeBackend:
    """Compose the three-way virtual filesystem for one user.

    The memories mount is bound to a namespace that already contains the user id, so it
    needs no wrapper. The skills mount shares one namespace and is therefore wrapped.
    """
    memories = StoreBackend(
        namespace=lambda _runtime: memories_namespace(owner_user_id), store=store
    )
    skills_inner = StoreBackend(
        namespace=lambda _runtime: persisted_skills_namespace(), store=store
    )
    skills = OwnerScopedStoreBackend(skills_inner, owner_user_id=owner_user_id)

    return CompositeBackend(
        default=sandbox_backend,
        routes={
            f"{MEMORIES_ROOT}/": memories,
            f"{PERSISTED_SKILLS_ROOT}/": skills,
        },
    )


# --------------------------------------------------------------------------- #
# write guard
# --------------------------------------------------------------------------- #

#: Code returned when a write tool is called without an approval.
APPROVAL_REQUIRED = "APPROVAL_REQUIRED"


def guard_write_tools(
    tools: Sequence[Any],
    *,
    write_tools: Iterable[str],
    authorizer: Callable[[str, Mapping[str, Any]], bool] | None = None,
) -> tuple[list[Any], list[str]]:
    """Wrap write tools so they refuse to run without an approval.

    T12 supplies the real approval records through ``authorizer``; until then every write
    is refused. The guard lives at the tool boundary rather than in a prompt, because a
    prompt is a request and this needs to be a control: a refused call returns a structured
    failure and reaches neither the ERP nor the ledger.

    Returns ``(tools, guarded_names)``.
    """
    from langchain_core.tools import StructuredTool

    write_names = set(write_tools)
    guarded: list[Any] = []
    guarded_names: list[str] = []

    for original in tools:
        name = getattr(original, "name", "")
        if name not in write_names:
            guarded.append(original)
            continue

        def run(_original: Any = original, _name: str = name, **kwargs: Any) -> Any:
            approved = bool(authorizer(_name, kwargs)) if authorizer is not None else False
            if not approved:
                return json.dumps(
                    {
                        "ok": False,
                        "data": None,
                        "error": {
                            "code": APPROVAL_REQUIRED,
                            "message": (
                                f"{_name} 需要用户审批后才能执行；当前没有有效授权，未执行任何写操作"
                            ),
                            "retryable": False,
                            "details": None,
                        },
                        "request_id": None,
                    },
                    ensure_ascii=False,
                )
            return _original.invoke(kwargs)

        guarded.append(
            StructuredTool.from_function(
                func=run,
                name=name,
                description=getattr(original, "description", "") or name,
                args_schema=getattr(original, "args_schema", None),
            )
        )
        guarded_names.append(name)

    return guarded, guarded_names


# --------------------------------------------------------------------------- #
# agent assembly
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AgentAssembly:
    """A built graph plus the facts about how it was built."""

    graph: Any
    subagents: dict[str, Any]
    prompt: str
    tool_names: tuple[str, ...]
    delegated_tools: tuple[str, ...]
    guarded_write_tools: tuple[str, ...]
    middleware_inventory: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "subagents": [config.as_dict() for config in self.subagents.values()],
            "tool_names": list(self.tool_names),
            "delegated_tools": list(self.delegated_tools),
            "guarded_write_tools": list(self.guarded_write_tools),
            "middleware": self.middleware_inventory,
        }


def build_main_agent(
    *,
    model: Any,
    tools: Sequence[Any],
    write_tools: Sequence[str],
    backend: Any,
    owner_user_id: str,
    subagents: dict[str, Any],
    delegated_tools: Iterable[str] | None = None,
    preferences: Mapping[str, Any] | None = None,
    skills: Sequence[Mapping[str, str]] = (),
    middleware: Sequence[Any] = (),
    middleware_inventory: list[dict[str, Any]] | None = None,
    subagent_models: Mapping[str, Any] | None = None,
    subagent_middleware: Sequence[Any] = (),
    checkpointer: Any | None = None,
    store: Any | None = None,
    system_prompt: str | None = None,
    authorizer: Callable[[str, Mapping[str, Any]], bool] | None = None,
) -> AgentAssembly:
    """Assemble the main agent from already-validated parts.

    Tool authority is decided by the YAML loader, not here: this function receives
    validated sub-agent configs and hands each one exactly the tools it declared. The main
    agent itself receives only the read surface plus the planning tools, so a write can
    only happen inside the order sub-agent, through the guard.

    Every run-specific value (backend, store namespace, preferences) is passed in rather
    than stored on the graph, so a cached graph cannot leak one user's context into the
    next run.
    """
    from deepagents import create_deep_agent

    from agent.memory.prompts import build_main_prompt
    from agent.middlewares.skill_discovery import MAIN_SKILL_SOURCES, SkillCatalogRefreshMiddleware
    from agent.subagents.loader import to_subagents

    guarded, guarded_names = guard_write_tools(
        tools, write_tools=write_tools, authorizer=authorizer
    )

    # Every tool a sub-agent declares is a tool the main agent delegates. Handing the main
    # agent the ERP tools as well would let it answer directly, which is what a first cut of
    # this code did — and a real-model smoke test showed the model doing exactly that. The
    # role split in docs/plan/architecture.md ("管理任务清单、分派、汇总") is then a fact
    # about the tool set rather than a request in a prompt.
    if delegated_tools is None:
        delegated = {name for config in subagents.values() for name in config.expected_tools}
    else:
        delegated = set(delegated_tools)
    main_tools = [tool for tool in guarded if getattr(tool, "name", "") not in delegated]

    # The same gates apply at the top level, so a write tool that ever reaches the main
    # agent is paused too. The sub-agents carry their own copies.
    interrupt_on: dict[str, Any] = {}
    for config in subagents.values():
        interrupt_on.update(config.interrupt_map())

    from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware

    from agent.middlewares.context_injection import ContextInjectionMiddleware
    from agent.middlewares.conversation_archive import (
        ArchivedSummarizationMiddleware,
        build_archived_summarization,
    )
    from agent.middlewares.tools_summarization import ToolsSummarizationMiddleware

    # Non-fork subagents do not inherit custom parent middleware in this SDK version.
    child_middleware = list(subagent_middleware)
    inherited_types = (ContextInjectionMiddleware, ToolsSummarizationMiddleware,
                       ModelCallLimitMiddleware, ToolCallLimitMiddleware)
    for item in middleware:
        if isinstance(item, inherited_types) and not any(type(child) is type(item) for child in child_middleware):
            child_middleware.append(item)

    prompt = system_prompt or build_main_prompt(preferences=preferences, skills=skills)
    summary_middleware = ([] if any(isinstance(item, ArchivedSummarizationMiddleware) for item in middleware)
                          else [build_archived_summarization(model, backend)])

    graph = create_deep_agent(
        model=model,
        tools=main_tools,
        subagents=to_subagents(
            subagents.values(), guarded, models=subagent_models, middleware=child_middleware,
            backend=backend, parent_model=model,
        ),
        interrupt_on=interrupt_on or None,
        backend=backend,
        checkpointer=checkpointer,
        store=store,
        skills=list(MAIN_SKILL_SOURCES),
        middleware=[*middleware, *summary_middleware, SkillCatalogRefreshMiddleware(
            backend=backend, sources=MAIN_SKILL_SOURCES, scope="main",
            preset_names=("skill-management",),
        )],
        system_prompt=prompt,
    )

    return AgentAssembly(
        graph=graph,
        subagents=subagents,
        prompt=prompt,
        tool_names=tuple(sorted(getattr(tool, "name", "") for tool in main_tools)),
        delegated_tools=tuple(sorted(delegated)),
        guarded_write_tools=tuple(sorted(guarded_names)),
        middleware_inventory=list(middleware_inventory or []),
    )


__all__ = [
    "AGENTS_MD_PATH",
    "APPROVAL_REQUIRED",
    "MEMORIES_ROOT",
    "PERSISTED_SKILLS_ROOT",
    "SKILLS_ROOT",
    "AgentAssembly",
    "OwnerScopeError",
    "OwnerScopedStoreBackend",
    "build_main_agent",
    "build_virtual_backend",
    "guard_write_tools",
    "memories_namespace",
    "owner_key_prefix",
    "persisted_skills_namespace",
    "user_skill_prefix",
]
