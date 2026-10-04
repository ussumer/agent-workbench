"""The middleware stack, and an honest record of what is actually installed.

``contracts/skills-memory.md`` fixes a custom order:

    health -> context_injection -> skills_sync -> user_skills_restore ->
    主动摘要工具 -> memory_update -> sandbox_breaker -> model/tool limits

Two things this module refuses to do:

* **Claim a middleware exists when it does not.** Entries owned by later tasks are
  declared with ``owner_task`` and are simply not instantiated, so the inventory cannot
  be mistaken for a working feature.
* **Treat list position as execution order.** A position in this list is the *intended*
  order; what actually runs is decided by the hooks each class overrides. Those are read
  back off the classes by :func:`declared_hooks`, so the record stays true if a class stops
  overriding something.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

LOGGER = logging.getLogger("rush_harness.middleware")

#: Hooks the framework may call, in the order a single agent turn encounters them.
HOOK_NAMES: tuple[str, ...] = (
    "before_agent",
    "abefore_agent",
    "before_model",
    "abefore_model",
    "wrap_model_call",
    "awrap_model_call",
    "wrap_tool_call",
    "awrap_tool_call",
    "after_model",
    "aafter_model",
    "after_agent",
    "aafter_agent",
)


@dataclass(frozen=True)
class MiddlewareSpec:
    """One slot of the stack."""

    key: str
    purpose: str
    owner_task: str
    implemented: bool
    factory: Callable[[Any], Any] | None = None
    framework: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "purpose": self.purpose,
            "owner_task": self.owner_task,
            "implemented": self.implemented,
            "framework": self.framework,
        }


def _todo_list():
    """The framework's planning middleware.

    ``create_deep_agent`` (0.7.14) builds the filesystem and sub-agent middleware but does
    **not** install a todo list, so ``write_todos`` only exists once this is supplied. The
    main agent's planning is a task requirement, so it is added explicitly rather than
    assumed to come from the base stack.
    """
    from langchain.agents.middleware import TodoListMiddleware

    return TodoListMiddleware()


def _specs() -> tuple[MiddlewareSpec, ...]:
    """The stack, in the intended order.

    Factories are imported lazily so this module stays importable without a sandbox or a
    database, which is what lets the inventory be inspected in a unit test.
    """
    from agent.middlewares.context_injection import ContextInjectionMiddleware
    from agent.middlewares.memory_update import MemoryUpdateMiddleware
    from agent.middlewares.sandbox_breaker import BreakerRegistry
    from agent.middlewares.sandbox_health import SandboxHealthMiddleware
    from agent.middlewares.skills_sync import SkillsSyncMiddleware
    from agent.middlewares.tools_summarization import (
        ToolsSummarizationMiddleware,
        build_budget_middlewares,
        build_compaction_tool,
    )
    from agent.middlewares.user_skills_restore import UserSkillsRestoreMiddleware

    return (
        MiddlewareSpec(
            key="todo_list",
            purpose="框架待办清单中间件，提供 write_todos 与 persisted todos 状态",
            owner_task="T11",
            implemented=True,
            factory=lambda ctx: _todo_list(),
            framework=True,
        ),
        MiddlewareSpec(
            key="sandbox_health",
            purpose="每轮开始前探测用户沙箱，必要时请求一次性恢复",
            owner_task="T09",
            implemented=True,
            factory=lambda ctx: SandboxHealthMiddleware(
                ctx["manager"], owner_resolver=ctx["owner_resolver"]
            ),
        ),
        MiddlewareSpec(
            key="context_injection",
            purpose="启动规则、用户偏好与技能元数据的分层注入",
            owner_task="T18",
            implemented=True,
            factory=lambda ctx: ContextInjectionMiddleware(store_provider=ctx["store_provider"]),
        ),
        MiddlewareSpec(
            key="skills_sync",
            purpose="预置技能按 manifest hash 增量同步进沙箱",
            owner_task="T10",
            implemented=True,
            factory=lambda ctx: SkillsSyncMiddleware(backend_provider=ctx["backend_provider"]),
        ),
        MiddlewareSpec(
            key="user_skills_restore",
            purpose="从 Store 校验并恢复用户已发布技能",
            owner_task="T10",
            implemented=True,
            factory=lambda ctx: UserSkillsRestoreMiddleware(
                backend_provider=ctx["backend_provider"], reader=ctx["skill_reader"]
            ),
        ),
        MiddlewareSpec(
            key="conversation_summary",
            purpose="框架摘要能力 + 主动 compact_conversation 工具",
            owner_task="T19",
            implemented=True,
            factory=lambda ctx: [
                build_compaction_tool(ctx["model"], ctx["backend"]),
                ToolsSummarizationMiddleware(writer=ctx["workspace_writer"]),
            ],
        ),
        MiddlewareSpec(
            key="memory_update",
            purpose="只在成功采购任务后更新自动历史字段",
            owner_task="T18",
            # Built but not hook-driven: the run layer calls `apply()` with the finished
            # run's facts, because no middleware hook fires once per run with the trace.
            implemented=True,
            factory=lambda ctx: MemoryUpdateMiddleware(store_provider=ctx["store_provider"]),
        ),
        MiddlewareSpec(
            key="sandbox_breaker",
            purpose="沙箱连续失败熔断与半开探测",
            owner_task="T19",
            # Built but not hook-driven: the sandbox call sites report outcomes to it, since
            # only they know whether a failure was infrastructure or a business answer.
            implemented=True,
            factory=lambda ctx: ctx.get("breaker_registry") or BreakerRegistry(),
        ),
        MiddlewareSpec(
            key="model_tool_limits",
            purpose="主子共享的模型/工具调用与时长预算",
            owner_task="T19",
            implemented=True,
            # Two SDK graph-local guards. The API callback aggregates main and children;
            # reusing these instances alone does not share their private graph-state counts.
            factory=lambda ctx: build_budget_middlewares(ctx.get("budget_config")),
        ),
    )


def build_middlewares(
    context: dict[str, Any], *, only: Iterable[str] | None = None
) -> list[Any]:
    """Instantiate the implemented part of the stack.

    Missing context keys for an implemented middleware are an error: a silently skipped
    middleware would mean the graph runs without a protection it declares.

    ``only`` narrows assembly to the named slots. It exists for tests that exercise a part
    of the graph without the sandbox or the store behind it — naming the slots explicitly
    keeps that a deliberate choice rather than an accident of missing context.
    """
    wanted = set(only) if only is not None else None
    middlewares: list[Any] = []
    for spec in _specs():
        if wanted is not None and spec.key not in wanted:
            continue
        if not spec.implemented or spec.factory is None:
            continue
        missing = _missing_context(spec, context)
        if missing:
            raise KeyError(
                f"middleware {spec.key!r} needs context keys {missing} to be built"
            )
        built = spec.factory(context)
        # A slot may legitimately expand to several middleware (the budget is two), so a
        # list is flattened rather than being nested one level deeper.
        if isinstance(built, list):
            middlewares.extend(built)
        else:
            middlewares.append(built)
    return middlewares


def _missing_context(spec: MiddlewareSpec, context: dict[str, Any]) -> list[str]:
    required = {
        "sandbox_health": ("manager", "owner_resolver"),
        "skills_sync": ("backend_provider",),
        "user_skills_restore": ("backend_provider", "skill_reader"),
        "context_injection": ("store_provider",),
        "memory_update": ("store_provider",),
        "conversation_summary": ("model", "backend", "workspace_writer"),
    }.get(spec.key, ())
    return [key for key in required if key not in context]


def declared_hooks(middleware: Any) -> list[str]:
    """Which hooks a middleware actually overrides.

    Read off the class hierarchy rather than trusted from this module's list, so the
    inventory cannot drift away from the code.
    """
    cls = type(middleware)
    overridden: list[str] = []
    for name in HOOK_NAMES:
        for klass in cls.__mro__:
            if name in klass.__dict__:
                # Ignore the framework base class's own (no-op) definitions.
                if klass.__name__ in {"AgentMiddleware", "object"}:
                    break
                overridden.append(name)
                break
    return overridden


def middleware_inventory(context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The stack as data: intended order, ownership, and installed hooks.

    Describing the stack must not require everything it needs to be available. A slot whose
    context is missing is reported with ``buildable: false`` rather than raising, so the
    inventory stays readable in a process that has no sandbox or no store — and so a
    missing dependency shows up as a fact in the snapshot instead of an exception at
    reporting time.
    """
    inventory: list[dict[str, Any]] = []
    for index, spec in enumerate(_specs()):
        entry = spec.as_dict()
        entry["order"] = index
        entry["hooks"] = []
        entry["buildable"] = spec.implemented
        inventory.append(entry)

    if context is None:
        return inventory

    for entry, spec in zip(inventory, _specs(), strict=True):
        if not spec.implemented:
            entry["buildable"] = False
            continue
        try:
            built = build_middlewares(context, only={spec.key})
        except KeyError:
            entry["buildable"] = False
            continue
        entry["buildable"] = True
        entry["hooks"] = declared_hooks(built[0]) if built else []
    return inventory


def hook_order(inventory: Sequence[dict[str, Any]]) -> list[tuple[str, str]]:
    """``(middleware key, hook)`` pairs, flattened in the order they are installed.

    This is the intended order only; the framework decides what actually runs for a given
    turn depending on which hooks a class defines.
    """
    return [
        (entry["key"], hook)
        for entry in inventory
        for hook in entry.get("hooks", [])
    ]


__all__ = [
    "HOOK_NAMES",
    "MiddlewareSpec",
    "build_middlewares",
    "declared_hooks",
    "hook_order",
    "middleware_inventory",
]
