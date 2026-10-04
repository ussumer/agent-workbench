"""Business endpoints for background analysis tasks.

The routes and payloads are fixed by ``contracts/external.md``; this module's whole job is
ownership, idempotency and adapting to the official SDK — the contract is explicit that this
layer must not grow into a replacement for the Agent Protocol.

Two conventions match the rest of the application and are worth stating because they are what
makes the isolation real rather than asserted:

**Another owner's task is a 404, not a 403.** Every lookup goes through an owner-scoped store
call, so the answer for a foreign id is the same as for an id that never existed.

**The parent thread has to be the caller's.** A task is launched *into* a conversation, so a
caller who cannot see the conversation cannot start work in it. Without that check the
endpoints would let one demo user attach background work to another's thread.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from api_view.api.deps import WebContext, require_owner

LOGGER = logging.getLogger("rush_harness.api.async_tasks")

#: Bounds from the chat contract's message limit, applied to an instruction as well.
MAX_INSTRUCTION_CHARS = 20000

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


class LaunchRequest(BaseModel):
    parent_thread_id: str
    request_id: str
    instruction: str = ""


class UpdateRequest(BaseModel):
    request_id: str
    instruction: str = ""


class CancelRequest(BaseModel):
    request_id: str = ""


def build_async_tasks_router(context: WebContext) -> APIRouter:
    router = APIRouter(tags=["async-tasks"])

    def _require_parent(owner: str, parent_thread_id: str) -> None:
        """Refuse to attach work to a conversation the caller cannot see."""
        if not parent_thread_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "parent_thread_id is required")
        if context.repository is None:  # pragma: no cover - only in a hand-built context
            return
        if context.repository.get_thread(owner_user_id=owner, thread_id=parent_thread_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such thread")

    def _validate(instruction: str) -> str:
        text = (instruction or "").strip()
        if not text:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "instruction must not be empty")
        if len(text) > MAX_INSTRUCTION_CHARS:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"instruction exceeds {MAX_INSTRUCTION_CHARS} characters",
            )
        return text

    @router.post("/async-tasks")
    async def launch(body: LaunchRequest, request: Request) -> JSONResponse:
        """Start a background run. A repeated ``request_id`` returns the original task."""
        from agent.async_tasks.service import ProtocolUnavailable

        owner = require_owner(request)
        _require_parent(owner, body.parent_thread_id)
        instruction = _validate(body.instruction)
        if not body.request_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "request_id is required")

        try:
            record, created = await context.async_tasks.alaunch(
                owner_user_id=owner,
                parent_thread_id=body.parent_thread_id,
                request_id=body.request_id,
                instruction=instruction,
            )
        except ProtocolUnavailable as failure:
            # 503, never a task in a fake state: if the service is down there is nothing
            # running, and saying otherwise would have the user wait for a report.
            LOGGER.warning("cannot launch a background task: %s", failure)
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                f"后台分析服务不可用：{failure}",
            ) from failure

        return JSONResponse(
            {"data": record.public(), "request_id": body.request_id, "created": created},
            status_code=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK,
        )

    @router.get("/async-tasks")
    async def list_tasks(
        request: Request, parent_thread_id: str, page_size: int = DEFAULT_PAGE_SIZE
    ) -> JSONResponse:
        owner = require_owner(request)
        _require_parent(owner, parent_thread_id)
        limit = max(1, min(MAX_PAGE_SIZE, page_size))
        records = context.async_tasks.list_for_parent(
            owner_user_id=owner, parent_thread_id=parent_thread_id
        )
        return JSONResponse(
            {
                "data": {
                    "items": [record.public() for record in records[:limit]],
                    "total": len(records),
                },
                "request_id": "",
            }
        )

    @router.get("/async-tasks/{task_id}")
    async def task_status(task_id: str, request: Request) -> JSONResponse:
        owner = require_owner(request)
        record = await _resolve(context, owner, task_id)
        return JSONResponse({"data": record.public(), "request_id": ""})

    @router.post("/async-tasks/{task_id}/update")
    async def update_task(
        task_id: str, body: UpdateRequest, request: Request
    ) -> JSONResponse:
        from agent.async_tasks.service import ProtocolUnavailable

        owner = require_owner(request)
        instruction = _validate(body.instruction)
        if not body.request_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "request_id is required")

        try:
            record, accepted = await context.async_tasks.aupdate(
                owner_user_id=owner,
                task_id=task_id,
                request_id=body.request_id,
                instruction=instruction,
            )
        except LookupError as failure:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such task") from failure
        except ValueError as failure:
            raise HTTPException(status.HTTP_409_CONFLICT, str(failure)) from failure
        except ProtocolUnavailable as failure:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, f"后台分析服务不可用：{failure}"
            ) from failure

        return JSONResponse(
            {"data": record.public(), "request_id": body.request_id, "accepted": accepted}
        )

    @router.post("/async-tasks/{task_id}/cancel")
    async def cancel_task(
        task_id: str, body: CancelRequest, request: Request
    ) -> JSONResponse:
        from agent.async_tasks.service import ProtocolUnavailable

        owner = require_owner(request)
        try:
            record = await context.async_tasks.acancel(
                owner_user_id=owner, task_id=task_id, request_id=body.request_id
            )
        except LookupError as failure:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such task") from failure
        except ProtocolUnavailable as failure:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, f"后台分析服务不可用：{failure}"
            ) from failure

        return JSONResponse({"data": record.public(), "request_id": body.request_id})

    return router


async def _resolve(context: WebContext, owner: str, task_id: str):
    from agent.async_tasks.service import ProtocolUnavailable, TaskNotFound

    try:
        return await context.async_tasks.astatus(owner_user_id=owner, task_id=task_id)
    except (TaskNotFound, LookupError) as failure:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such task") from failure
    except ProtocolUnavailable as failure:
        # The task exists; the *service* is what is unreachable. Reporting 404 here would
        # tell the user their task vanished, which is a different and wrong story.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"后台分析服务暂时不可达：{failure}"
        ) from failure


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_INSTRUCTION_CHARS",
    "MAX_PAGE_SIZE",
    "CancelRequest",
    "LaunchRequest",
    "UpdateRequest",
    "build_async_tasks_router",
]
