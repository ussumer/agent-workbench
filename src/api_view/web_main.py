"""The FastAPI application: one worker, managed runs, explicit shutdown.

Assembly is separated from the process entry point so the same app can be built around a
scripted model in a test and a real model in the demo, with no route knowing the difference.
The routes only ever see :class:`~api_view.api.deps.WebContext`.

Two lifecycle decisions are made here rather than left implicit:

* **Runs are shut down, not abandoned.** ``lifespan`` asks the registry to stop the live
  runs before closing Mongo, so a request in flight cannot have its persistence pulled out
  from under it.
* **Mongo is started once and closed once.** The resources object owns the client; the
  repository, checkpointer and store all read from it, so there is exactly one connection
  pool per process and no per-request client to leak.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from agent.approval.service import ApprovalService
from agent.approval.store import MongoPendingActionStore
from agent.artifacts.service import ArtifactService
from agent.artifacts.store import MongoArtifactStore
from agent.async_tasks.service import build_async_task_service
from agent.persistence.repository import ApplicationRepository
from api_view.api.artifacts import build_artifacts_router
from api_view.api.async_tasks import build_async_tasks_router
from api_view.api.chat import build_chat_router
from api_view.api.deps import WebContext
from api_view.api.history import build_history_router
from api_view.run_registry import RunRegistry
from api_view.web_config import (
    DEFAULT_INTERNAL_SERVICE_TOKEN_ENV,
    PersistenceSettings,
    internal_service_token,
)

LOGGER = logging.getLogger("rush_harness.web")


def create_app(
    *,
    settings: PersistenceSettings | None = None,
    graph_provider: Callable[[str], Any] | None = None,
    resources: Any | None = None,
    context: WebContext | None = None,
    sandboxes: Any | None = None,
) -> FastAPI:
    """Build the application.

    Either a ready ``context`` is supplied (tests wire one directly) or the pieces are built
    from ``settings`` and ``graph_provider``. There is no third path that silently degrades
    to an in-memory stand-in: a missing Mongo is a startup failure, reported as blocked.
    """
    if context is None and graph_provider is None:
        raise ValueError("create_app needs either a context or a graph_provider")

    # Whose resources are these? If the caller supplied a context, the caller owns the
    # lifetime and closing them here would pull Mongo out from under every later test — or,
    # in production, out from under an embedding process that shares them.
    owns_resources = context is None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        ctx = app.state.context
        try:
            yield
        finally:
            stopped = await ctx.registry.shutdown()
            if stopped:
                LOGGER.info("stopped %d run(s) during shutdown", stopped)
            if owns_resources and ctx.resources is not None:
                ctx.resources.close()

    app = FastAPI(title="rush-harness assistant", version="1.0", lifespan=lifespan)

    if context is None:
        resolved_settings = settings or PersistenceSettings.from_env()
        from api_view.agent_loader import build_persistence

        started = build_persistence(resolved_settings)
        # One artifact store, shared by the two things that file into it: the foreground tools
        # and the background task service. A second instance would work and would also make
        # "where did that file come from" depend on which one you asked.
        artifacts = ArtifactService(store=MongoArtifactStore(started.database))
        context = WebContext(
            resources=started,
            repository=ApplicationRepository(started.database),
            registry=RunRegistry(),
            approvals=ApprovalService(
                store=MongoPendingActionStore(started.database),
                grant_secret=_grant_secret(resolved_settings),
            ),
            artifacts=artifacts,
            # The SDK client is built but not connected: starting the application must not
            # depend on the background service being up. Whether anything is listening is
            # answered by the launch endpoint, which reports 503 rather than a fake task.
            # The task service gets the same artifact store, so a finished report becomes a
            # file the user can download rather than an id in a record nobody fills.
            async_tasks=build_async_task_service(started.database, artifacts=artifacts),
            settings=resolved_settings,
            graph_provider=graph_provider,
            internal_service_token=internal_service_token(),
            sandboxes=sandboxes,
            mcp_url=_mcp_url(),
        )
        resources = started

    app.state.context = context
    app.include_router(build_history_router(context), prefix="/api")
    app.include_router(build_chat_router(context), prefix="/api")
    app.include_router(build_artifacts_router(context), prefix="/api")
    app.include_router(build_async_tasks_router(context), prefix="/api")

    from api_view.internal.analysis import build_internal_analysis_router
    from api_view.internal.approval import build_internal_router
    from api_view.internal.sandbox import build_internal_sandbox_router

    # Mounted under its own prefix with its own token: the gateway must be able to reach it
    # and the browser must not.
    app.include_router(
        build_internal_router(
            service=context.approvals, service_token=context.internal_service_token
        )
    )
    # T20's two surfaces, for the separate Agent Protocol process. Both authenticate with the
    # same internal token and neither is reachable from the browser.
    app.include_router(
        build_internal_analysis_router(
            mcp_url=context.mcp_url, service_token=context.internal_service_token
        )
    )
    app.include_router(
        build_internal_sandbox_router(
            context=context, service_token=context.internal_service_token
        )
    )

    @app.get("/health")
    async def health(request: Request) -> JSONResponse:
        ctx: WebContext = request.app.state.context
        return JSONResponse(
            {
                "status": "healthy",
                "active_runs": ctx.registry.active_threads(),
                "database": ctx.settings.database,
            }
        )

    return app


def _mcp_url() -> str:
    """The gateway address the internal read surface forwards to.

    An address, so it has a documented default and its absence is not fatal at startup: a
    request that arrives without a gateway configured gets 503 from the endpoint, which is a
    better answer than a process that will not boot.
    """
    from agent.config import ServiceAddresses

    return ServiceAddresses.from_env().mcp_base_url


def _grant_secret(settings: PersistenceSettings) -> str:
    """The HMAC key shared with the gateway.

    Read from the environment; a missing key is a startup failure rather than an empty
    secret, because an empty key would let anyone mint an approval grant.
    """
    import os

    secret = os.environ.get("MCP_GRANT_SECRET", "").strip()
    if not secret:
        from api_view.web_config import PersistenceUnavailable

        raise PersistenceUnavailable(
            "MCP_GRANT_SECRET is required; without it approval grants cannot be signed"
        )
    return secret


__all__ = ["DEFAULT_INTERNAL_SERVICE_TOKEN_ENV", "create_app"]
