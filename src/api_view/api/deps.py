"""Request-scoped dependencies: who is calling, and what they may touch.

The owner is read from a server-set cookie, never from a body or a path. That is the whole
of this demo's identity model — two fixed accounts, established by the server, used only for
switching identity on a local machine. It is deliberately not described as authentication.

Every route resolves the owner through :func:`require_owner` and then goes through
owner-scoped repository methods, so a cross-user read is a 404 from the data layer rather
than a check that a route might forget to perform.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException, Request, status

from agent.approval.service import ApprovalService
from agent.artifacts.service import ArtifactService
from agent.async_tasks.service import AsyncTaskService
from agent.middlewares.tools_summarization import BudgetConfig
from agent.persistence.repository import ApplicationRepository
from api_view.run_registry import RunRegistry
from api_view.web_config import MongoResources, PersistenceSettings

#: The cookie the server sets. HttpOnly so the page's own scripts cannot rewrite identity.
SESSION_COOKIE = "demo_user"

#: Header a test or a script can use instead of a cookie. Same two values only.
DEMO_USER_HEADER = "X-Demo-User"

#: The only two identities this demo knows. Anything else is refused rather than
#: auto-created, so a typo cannot become a new account with its own data.
DEMO_USERS: tuple[str, ...] = ("demo-a", "demo-b")

#: Message bounds from contracts/chat.md.
MAX_MESSAGE_CHARS = 20000


@dataclass
class WebContext:
    """Everything the routes need, assembled once at startup."""

    resources: MongoResources
    repository: ApplicationRepository
    registry: RunRegistry
    approvals: ApprovalService
    #: Report and chart files the agent produced, owner-scoped. Same lifetime rules as the
    #: approvals service: built once at startup from the shared database.
    artifacts: ArtifactService
    #: Background analysis runs. Unlike the others this one holds no state of its own — the
    #: run lives in a separate service process and this only maps it to an owner.
    async_tasks: AsyncTaskService
    settings: PersistenceSettings
    #: ``owner_user_id -> compiled graph``. Injected so tests can supply a real graph
    #: built around a scripted model without the routes knowing.
    graph_provider: Any
    internal_service_token: str = ""
    #: The sandbox pool, when this process owns one. Optional because a process that only
    #: serves the foreground conversation needs no lease of its own — and because the
    #: internal sandbox surface must report "not configured" (503) rather than start
    #: containers it was never asked to own.
    sandboxes: Any = None
    #: MCP gateway address, for the internal read-only surface the background analyst uses.
    mcp_url: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    budget_config: BudgetConfig = field(default_factory=BudgetConfig)


def context_of(request: Request) -> WebContext:
    context = getattr(request.app.state, "context", None)
    if context is None:  # pragma: no cover - only if the app was built wrong
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="application context is not configured",
        )
    return context


def require_owner(request: Request, demo_user: str | None = None) -> str:
    """The caller's identity, from the cookie the server set.

    The header exists so a test (or a script) can act as one of the two demo accounts without
    a browser; it accepts the same two values and nothing else, so it cannot widen identity.

    Both are read from the request. An earlier version declared ``demo_user`` as a FastAPI
    ``Header(...)`` parameter, which never worked: this function is called *from* route bodies
    and from the run registry, it is not used as a ``Depends``, so nothing injects the
    parameter and it arrived as the ``Header`` default object itself — truthy, so a
    cookie-less request was reported as ``unknown demo user Header(None)`` instead of the
    "start a session first" message. Reading the request is correct in both call styles.
    """
    cookie = request.cookies.get(SESSION_COOKIE)
    owner = cookie or demo_user or request.headers.get(DEMO_USER_HEADER)
    if not owner:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"no session; call POST /api/demo/session first ({'|'.join(DEMO_USERS)})",
        )
    if owner not in DEMO_USERS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"unknown demo user {owner!r}",
        )
    return owner


__all__ = [
    "DEMO_USERS",
    "DEMO_USER_HEADER",
    "MAX_MESSAGE_CHARS",
    "SESSION_COOKIE",
    "WebContext",
    "context_of",
    "require_owner",
]
