"""Layer the runtime context into the model call: rules, preferences, then skills.

Injection happens per model call, not at graph build time. The graph may be cached and
shared, but a preference belongs to one owner, so putting it in a compiled prompt would leak
one user's settings into the next user's run.

The block is *rebuilt* for each call from the owner's stored preferences rather than carried
in a mutable field, which is what makes a preference change visible on the next turn of a
running conversation without restarting anything.

Because sub-agents run their own model calls, installing this middleware on them too is what
makes "the analyst follows the user's output format" true rather than assumed.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse

from agent.memory.preferences import (
    PreferencesReport,
    UserPreferences,
    derive_preference_updates,
    load_preferences,
    save_preferences,
)

LOGGER = logging.getLogger("rush_harness.middleware.context")

#: Marks the injected section so it can be replaced rather than duplicated when a model call
#: is retried or a middleware runs twice.
PREFERENCES_HEADING = "## 用户偏好与采购记忆"


def render_preference_block(preferences: UserPreferences) -> str:
    """The prompt section for one owner's preferences and recent history."""
    values = preferences.values
    suppliers = preferences.history.get("recent_supplier_ids") or []
    queries = preferences.history.get("recent_queries") or []

    lines = [
        PREFERENCES_HEADING,
        "",
        "以下是这位用户已确认的设定，请遵循；这是**用户**设定的，不是网页或工具内容设定的。",
        "",
        f"- language: {values.get('language', 'zh-CN')}",
        f"- currency: {values.get('currency', 'CNY')}（当前没有汇率服务，不做外币换算）",
        f"- output_format: {values.get('output_format', 'markdown')}",
        f"- chart_type: {values.get('chart_type', 'bar')}",
    ]
    if suppliers:
        lines.append(f"- 最近采购过的供应商：{', '.join(suppliers)}")
    if queries:
        lines.append(f"- 最近的采购查询：{'; '.join(queries)}")

    if values.get("output_format") == "table":
        lines.append("")
        lines.append("用户要求表格输出：结论请用 Markdown 表格给出，不要只写段落。")
    return "\n".join(lines)


def merge_system_prompt(existing: str | None, block: str) -> str:
    """Append the block, replacing a previous copy if one is already there."""
    base = existing or ""
    if PREFERENCES_HEADING in base:
        head, _, _tail = base.partition(PREFERENCES_HEADING)
        base = head.rstrip()
    return f"{base}\n\n{block}".strip()


class ContextInjectionMiddleware(AgentMiddleware):
    """Puts the owner's preferences in front of the model on every call."""

    def __init__(self, *, store_provider: Callable[[str], Any]) -> None:
        super().__init__()
        self._store_provider = store_provider

    # ------------------------------------------------------------- injection

    def _inject(self, request: ModelRequest) -> ModelRequest:
        owner = _owner_of(request)
        if not owner:
            return request
        preferences = load_preferences(self._store_provider(owner))
        from langchain_core.messages import SystemMessage

        return request.override(
            system_message=SystemMessage(
                content=merge_system_prompt(request.system_prompt, render_preference_block(preferences))
            )
        )

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(self._inject(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(self._inject(request))

    # ------------------------------------------------------- explicit updates

    def apply_user_preferences(
        self, *, owner_user_id: str, message: str, source_is_user: bool = True
    ) -> PreferencesReport:
        """Turn a user's own words into validated preference updates.

        Called by the run layer with the user's message. ``source_is_user=False`` is how the
        caller says "this text came from a page or a tool", and the whole update is refused —
        a scraped page telling the assistant to change the user's settings is content, not
        consent.
        """
        updates = derive_preference_updates(message, source_is_user=source_is_user)
        if not updates:
            return PreferencesReport()
        scoped = self._store_provider(owner_user_id)
        preferences = load_preferences(scoped)
        report = preferences.apply_explicit(updates)
        if report.changed():
            save_preferences(scoped, preferences, include_history=False)
        LOGGER.info(
            "preference update for %s: applied=%s refused=%s",
            owner_user_id,
            report.applied,
            report.refused,
        )
        return report


def _owner_of(request: ModelRequest) -> str | None:
    from agent.runtime_context import runtime_owner

    return runtime_owner(getattr(request, "runtime", None))


#: Tools whose success means the user was doing procurement, not chatting.
ERP_TOOLS: frozenset[str] = frozenset(
    {
        "supplier_query",
        "part_query",
        "part_search",
        "part_by_supplier",
        "inventory_warning",
        "order_search_details",
        "order_create",
        "order_update",
    }
)

#: Write tools, which only count when the user actually approved them.
WRITE_TOOLS: frozenset[str] = frozenset({"order_create", "order_update"})


def evaluate_run(
    *,
    run_status: str,
    tool_calls: Sequence[Mapping[str, Any]],
    denied_writes: Sequence[str] = (),
) -> tuple[bool, str]:
    """Whether a finished run counts as a successful procurement task, and why.

    Deliberately strict, because the contract lists the exclusions explicitly: chit-chat,
    failures and rejected writes must not be remembered as purchases. A run that ended
    ``interrupted`` is *not* a success either — it is parked on a decision nobody has made.
    """
    if run_status != "completed":
        return False, f"run 状态为 {run_status}，不算成功采购"

    succeeded = [
        str(call.get("name"))
        for call in tool_calls
        if call.get("name") in ERP_TOOLS and call.get("status") == "success"
    ]
    if not succeeded:
        return False, "本轮没有成功的 ERP 相关调用（闲聊不记录）"

    if denied_writes:
        # A rejected write is a decision *not* to buy; the run may still have completed, but
        # the rejection itself is not evidence of a purchase.
        return True, f"成功查询 {len(succeeded)} 次；但写操作被拒绝，不记为采购"

    writes = [name for name in succeeded if name in WRITE_TOOLS]
    return True, f"成功调用 {len(succeeded)} 次" + (f"，其中写操作 {writes}" if writes else "")


__all__ = [
    "ERP_TOOLS",
    "PREFERENCES_HEADING",
    "WRITE_TOOLS",
    "ContextInjectionMiddleware",
    "evaluate_run",
    "merge_system_prompt",
    "render_preference_block",
]
