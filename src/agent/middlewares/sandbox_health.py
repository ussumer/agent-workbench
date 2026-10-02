"""Probe the owner's sandbox before a run and ask the pool to recover it.

This middleware deliberately owns no pool, no registry and no containers. It runs one
probe before the agent starts and, when the probe fails, asks
:class:`~agent.backends.sandbox_manager.SandboxManager` for a healthy sandbox. All
allocation decisions stay in the manager, which is the single lease holder — a second
pool here would let an async sub-agent run against a container nobody registered.

Recovery is attempted at most once per run. If it fails, the run continues and the
first real tool call surfaces the error, rather than the middleware retrying in a loop.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware

from agent.backends.sandbox_manager import SandboxManager
from agent.backends.sandbox_proxy import SandboxBackendProxy, SandboxReplacedError

LOGGER = logging.getLogger("rush_harness.sandbox.health")

#: State keys written when a probe runs, so the UI and tests can observe the outcome.
STATE_SANDBOX_GENERATION = "sandbox_generation"
STATE_SANDBOX_RECOVERED = "sandbox_recovered"
STATE_SANDBOX_ID = "sandbox_id"


def default_owner_resolver(runtime: Any) -> str | None:
    """Best-effort extraction of the sandbox owner from the runtime context.

    T10/T11 supply an explicit resolver; the fallbacks keep this usable on its own.
    Returning ``None`` means "no owner known", which the middleware treats as "do not
    touch any sandbox" rather than guessing one.
    """
    context = getattr(runtime, "context", None)
    for source in (context, runtime):
        if source is None:
            continue
        for attribute in ("owner_user_id", "user_id", "user"):
            value = getattr(source, attribute, None)
            if isinstance(value, str) and value:
                return value
        if isinstance(source, dict):
            for key in ("owner_user_id", "user_id", "user"):
                value = source.get(key)
                if isinstance(value, str) and value:
                    return value
    return None


class SandboxHealthMiddleware(AgentMiddleware):
    """``before_agent`` health probe for the current user's sandbox."""

    def __init__(
        self,
        manager: SandboxManager,
        *,
        owner_resolver: Callable[[Any], str | None] | None = None,
        recover: bool = True,
    ) -> None:
        super().__init__()
        self._manager = manager
        self._resolve_owner = owner_resolver or default_owner_resolver
        self._recover = recover

    def probe(self, owner_user_id: str) -> dict[str, Any]:
        """Return the sandbox snapshot for one owner, recovering when needed."""
        proxy = self._manager.get_or_create(owner_user_id)
        recovered = False
        try:
            healthy = self._manager._is_alive(proxy)  # noqa: SLF001 - same package boundary
        except SandboxReplacedError:
            healthy = False

        if not healthy and self._recover:
            LOGGER.warning("user=%s sandbox unhealthy before run; requesting recovery", owner_user_id)
            proxy = self._manager.ensure_healthy(owner_user_id)
            recovered = True
        elif not healthy:
            LOGGER.warning("user=%s sandbox unhealthy before run; recovery disabled", owner_user_id)

        return {
            STATE_SANDBOX_ID: _safe_id(proxy),
            STATE_SANDBOX_GENERATION: proxy.generation,
            STATE_SANDBOX_RECOVERED: recovered,
        }

    def before_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:  # noqa: ANN401
        owner_user_id = self._resolve_owner(runtime)
        if not owner_user_id:
            LOGGER.debug("no sandbox owner in this run; health probe skipped")
            return None
        return self.probe(owner_user_id)

    async def abefore_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:  # noqa: ANN401
        import asyncio

        owner_user_id = self._resolve_owner(runtime)
        if not owner_user_id:
            return None
        # The manager is synchronous by design: it holds real locks over container
        # operations, so the probe is off-loaded rather than run on the event loop.
        return await asyncio.to_thread(self.probe, owner_user_id)


def _safe_id(proxy: SandboxBackendProxy) -> str | None:
    try:
        return proxy.id
    except SandboxReplacedError:  # pragma: no cover - only during a concurrent swap
        return None
