"""After a run, extend the automatic history — and only the automatic history.

This module can write exactly two fields (``recent_supplier_ids`` and ``recent_queries``). It
has no access to the four explicit preferences at all, so "an automatic update must not
overwrite a user's explicit preference" is not a rule that a future edit could forget to
apply: there is no code path from here to those keys.

What it refuses to record, and why each one is listed in the contract:

* a run that did not complete — an interrupted run is parked on a decision, and a failed one
  produced nothing worth remembering;
* a run with no successful ERP call — chit-chat is not procurement;
* a rejected write — the user declined, which is the opposite of a purchase.

Nothing here stores a secret, a page's instructions, or a full tool response. Queries are
truncated to a short label and suppliers are ids the ERP itself returned.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain.agents.middleware import AgentMiddleware

from agent.memory.preferences import (
    MAX_QUERY_CHARS,
    UserPreferences,
    load_preferences,
    save_automatic_history,
)

LOGGER = logging.getLogger("rush_harness.middleware.memory_update")


@dataclass
class MemoryUpdateReport:
    """What was written, and what was skipped and why."""

    updated: bool = False
    reason: str = ""
    supplier_ids: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "updated": self.updated,
            "reason": self.reason,
            "recent_supplier_ids": list(self.supplier_ids),
            "recent_queries": list(self.queries),
        }


def extract_supplier_ids(tool_calls: Sequence[Mapping[str, Any]]) -> list[str]:
    """Supplier ids the ERP returned this run, in the order first seen.

    Read from tool *results*, not from arguments: an argument is what the model asked for,
    a result is what the ERP confirmed exists.
    """
    seen: list[str] = []
    for call in tool_calls:
        if call.get("status") != "success":
            continue
        payload = call.get("data")
        for supplier_id in _walk_suppliers(payload):
            if supplier_id not in seen:
                seen.append(supplier_id)
    return seen


def _walk_suppliers(node: Any) -> list[str]:
    found: list[str] = []
    if isinstance(node, Mapping):
        for key, value in node.items():
            if key == "supplier_id" and isinstance(value, str) and value:
                found.append(value)
            else:
                found.extend(_walk_suppliers(value))
    elif isinstance(node, Sequence) and not isinstance(node, (str, bytes)):
        for item in node:
            found.extend(_walk_suppliers(item))
    return found


class MemoryUpdateMiddleware(AgentMiddleware):
    """Applies the automatic history update at the end of a run.

    A middleware for the framework's sake, not a hook: there is no hook that fires once per *run* with the whole
    tool trace, and inventing one from ``after_agent`` would mean re-deriving the trace from
    message history. The run layer already knows the outcome, so it calls
    :meth:`apply` with the facts it has.
    """

    def __init__(self, *, store_provider: Callable[[str], Any]) -> None:
        self._store_provider = store_provider

    def apply(
        self,
        *,
        owner_user_id: str,
        query: str,
        run_status: str,
        tool_calls: Sequence[Mapping[str, Any]],
        should_update: bool,
        reason: str,
    ) -> MemoryUpdateReport:
        if not should_update:
            LOGGER.info("memory update skipped for %s: %s", owner_user_id, reason)
            return MemoryUpdateReport(updated=False, reason=reason)

        scoped = self._store_provider(owner_user_id)
        preferences: UserPreferences = load_preferences(scoped)

        suppliers = extract_supplier_ids(tool_calls)
        if suppliers:
            preferences.record_suppliers(suppliers)
        label = " ".join(str(query).split())[:MAX_QUERY_CHARS]
        if label:
            preferences.record_query(label)

        save_automatic_history(scoped, preferences)
        LOGGER.info(
            "memory updated for %s: suppliers=%s query=%r",
            owner_user_id,
            suppliers,
            label,
        )
        return MemoryUpdateReport(
            updated=True,
            reason=reason,
            supplier_ids=list(preferences.history.get("recent_supplier_ids") or []),
            queries=list(preferences.history.get("recent_queries") or []),
        )


__all__ = [
    "MemoryUpdateMiddleware",
    "MemoryUpdateReport",
    "extract_supplier_ids",
]
