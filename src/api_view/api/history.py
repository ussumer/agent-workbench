"""History, session and artifact endpoints.

Two properties are worth stating because they are easy to get subtly wrong.

**Deleting a thread does not delete anything else.** It removes the thread's display
messages and its checkpoints — the conversation — and leaves the user's published skills,
their preferences and the business orders alone. Those are different kinds of data owned by
different layers (see the ownership table in ``docs/plan/architecture.md``), and a delete
that reached into them would be destroying records the user never asked to remove.

**Another user's thread is a 404, not a 403.** A 403 confirms the resource exists, which is
itself a disclosure. Every lookup here goes through an owner-scoped repository call, so the
answer is simply "no such thread" for anyone who is not the owner.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api_view.api.deps import DEMO_USERS, SESSION_COOKIE, WebContext, require_owner

LOGGER = logging.getLogger("rush_harness.api.history")

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


class SessionRequest(BaseModel):
    user_id: str


def build_history_router(context: WebContext) -> APIRouter:
    router = APIRouter(tags=["history"])

    # ------------------------------------------------------------------- session

    @router.post("/demo/session")
    async def session(body: SessionRequest) -> JSONResponse:
        """Establish one of the two fixed demo identities.

        The cookie is HttpOnly so the page's own JavaScript cannot rewrite it, and it is
        marked SameSite=Lax. It is an identity *switch* for a local demo; it is not
        authentication and the response says so.
        """
        if body.user_id not in DEMO_USERS:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"user_id must be one of {list(DEMO_USERS)}",
            )
        response = JSONResponse(
            {
                "data": {
                    "user_id": body.user_id,
                    "note": "本机演示身份切换，不是生产认证",
                },
                "request_id": "",
            }
        )
        response.set_cookie(
            SESSION_COOKIE,
            body.user_id,
            httponly=True,
            samesite="lax",
            path="/",
        )
        return response

    # ------------------------------------------------------------------- history

    @router.get("/history")
    async def list_history(
        request: Request, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE
    ) -> JSONResponse:
        owner = require_owner(request)
        page = max(1, page)
        page_size = max(1, min(MAX_PAGE_SIZE, page_size))
        # The repository paginates and returns the total, so the route does not slice a list
        # it also has to count.
        records, total = context.repository.list_threads(
            owner_user_id=owner, page=page, page_size=page_size
        )

        items = []
        for record in records:
            runs = context.repository.list_runs(
                owner_user_id=owner, thread_id=record.thread_id
            )
            latest = runs[-1] if runs else None
            items.append(
                {
                    "thread_id": record.thread_id,
                    "title": record.title,
                    "created_at": record.created_at.isoformat(),
                    "updated_at": record.updated_at.isoformat(),
                    "message_count": context.repository.count_display_messages(
                        owner_user_id=owner, thread_id=record.thread_id
                    ),
                    "status": (latest or {}).get("status", "idle"),
                }
            )

        return JSONResponse(
            {
                "data": {"items": items, "total": total, "page": page, "page_size": page_size},
                "request_id": "",
            }
        )

    @router.get("/history/{thread_id}")
    async def thread_history(thread_id: str, request: Request) -> JSONResponse:
        owner = require_owner(request)
        record = context.repository.get_thread(owner_user_id=owner, thread_id=thread_id)
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such thread")

        messages = context.repository.list_display_messages(
            owner_user_id=owner, thread_id=thread_id
        )
        runs = context.repository.list_runs(owner_user_id=owner, thread_id=thread_id)

        from api_view.api.chat import _graph_snapshot

        snapshot = _graph_snapshot(context, owner, thread_id)
        live = context.registry.get(thread_id)

        return JSONResponse(
            {
                "data": {
                    "thread_id": thread_id,
                    "title": record.title,
                    # Display history is stored separately from the graph state, so
                    # summarisation cannot remove a turn the user can still see.
                    "messages": [
                        {
                            "message_id": message["message_id"],
                            "seq": message["seq"],
                            "role": message["role"],
                            "content": message["content"],
                        }
                        for message in messages
                    ],
                    "runs": [
                        {
                            "run_id": run["run_id"],
                            "status": run["status"],
                            "request_id": run.get("request_id", ""),
                            "created_at": run["created_at"].isoformat()
                            if hasattr(run["created_at"], "isoformat")
                            else str(run["created_at"]),
                        }
                        for run in runs
                    ],
                    "todos": snapshot.get("todos") or [],
                    "pending_interrupts": _live_interrupts(context, owner, thread_id),
                    "status": "running" if live else (runs[-1]["status"] if runs else "idle"),
                },
                "request_id": "",
            }
        )

    @router.delete("/history/{thread_id}")
    async def delete_history(thread_id: str, request: Request) -> JSONResponse:
        owner = require_owner(request)
        if context.repository.get_thread(owner_user_id=owner, thread_id=thread_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such thread")

        if context.registry.get(thread_id) is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "thread has an active run; cancel it before deleting",
            )

        removed = context.repository.delete_thread(owner_user_id=owner, thread_id=thread_id)

        # Checkpoints go with the conversation; skills, preferences and orders are separate
        # records and are deliberately untouched.
        purged = _purge_checkpoints(context, thread_id)

        return JSONResponse(
            {
                "data": {
                    "thread_id": thread_id,
                    "deleted": {**removed, "checkpoints": purged},
                    "kept": ["用户技能", "用户偏好", "业务订单与操作记录"],
                },
                "request_id": "",
            }
        )

    # Artifact routes live in ``api_view/api/artifacts.py`` (T16). The placeholder that used
    # to sit here read ``context.extra["artifacts"]``, which nothing ever wrote, so it always
    # answered 404. It was deleted rather than kept alongside the real one: two routes on the
    # same path make behaviour depend on registration order.

    return router


def _live_interrupts(context: WebContext, owner: str, thread_id: str) -> list[dict[str, Any]]:
    from api_view.api.chat import _pending_interrupts

    return _pending_interrupts(context, owner, thread_id)


def _purge_checkpoints(context: WebContext, thread_id: str) -> int:
    """Remove the thread's checkpoints, using the installed checkpointer's own API."""
    try:
        checkpointer = context.resources.checkpointer
    except Exception:  # noqa: BLE001 - resources not started
        return 0
    deleted = 0
    for name in ("delete_thread", "adelete_thread"):
        method = getattr(checkpointer, name, None)
        if method is None:
            continue
        try:
            result = method(thread_id)
            if hasattr(result, "__await__"):
                import asyncio

                asyncio.run(result)
            deleted = 1
            break
        except Exception:  # noqa: BLE001 - reported by count, not by exception
            LOGGER.warning("checkpointer could not purge thread %s via %s", thread_id, name)
    return deleted


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "SessionRequest",
    "build_history_router",
]
