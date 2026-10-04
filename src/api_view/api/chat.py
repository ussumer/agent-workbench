"""Chat endpoints: stream, resume, state and cancel.

The streaming route is the only place that touches the framework's iterator, and it does so
through :class:`~api_view.stream_adapter.StreamAdapter`. Everything it decides — when a run
has ended, whether it ended interrupted or completed, what the browser is told — is derived
from the checkpoint and the adapter's terminal state, never from the absence of further
tokens.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from agent.approval.models import ApprovalError
from api_view.api.deps import (
    MAX_MESSAGE_CHARS,
    WebContext,
    require_owner,
)
from api_view.run_registry import RunHandle, ThreadBusy
from api_view.stream_adapter import (
    CANCELLED,
    COMPLETED,
    FAILED,
    INTERRUPTED,
    AgentEvent,
)

LOGGER = logging.getLogger("rush_harness.api.chat")

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

#: SSE comment used as a keep-alive, per contracts/chat.md.
HEARTBEAT = ": heartbeat\n\n"


class ChatRequest(BaseModel):
    thread_id: str | None = None
    request_id: str = Field(min_length=1, max_length=128)
    message: str


class ResumeRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=128)
    interrupt_id: str = Field(min_length=1, max_length=128)
    resume: dict[str, Any]


def build_chat_router(context: WebContext) -> APIRouter:
    router = APIRouter(prefix="/chat", tags=["chat"])

    # ------------------------------------------------------------------ streaming

    @router.post("/stream")
    async def stream(request: Request, body: ChatRequest) -> Response:
        owner = require_owner(request)
        text = (body.message or "").strip()
        if not text:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "message must not be blank")
        if len(body.message) > MAX_MESSAGE_CHARS:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"message exceeds {MAX_MESSAGE_CHARS} characters",
            )

        thread_id = body.thread_id or _new_thread_id()
        context.repository.ensure_thread(
            owner_user_id=owner, thread_id=thread_id, title=text[:80]
        )
        run_id = _new_run_id()
        reservation = _reserve(context, owner, thread_id, body.request_id, text, run_id)

        if not reservation.is_new:
            # The same request id means a retry: report the original run instead of
            # starting a second one.
            LOGGER.info("request %s replayed as run %s", body.request_id, reservation.run_id)
            return _replay_response(context, owner, thread_id, reservation.run_id)

        handle = await _open(context, owner, thread_id, body.request_id, run_id)
        context.repository.update_run_status(
            owner_user_id=owner, run_id=handle.run_id, status="running"
        )
        _persist_user_message(context, owner, thread_id, handle, text)

        context.registry.attach(
            handle,
            lambda run: _run_turn(
                context,
                run,
                owner=owner,
                thread_id=thread_id,
                payload={"messages": [{"role": "user", "content": text}]},
                cancel_status=CANCELLED,
            ),
        )
        return _sse_response(context, handle)

    @router.post("/{thread_id}/resume")
    async def resume(
        thread_id: str, request: Request, body: ResumeRequest
    ) -> Response:
        owner = require_owner(request)
        # Ownership is decided by the data layer: another user's thread is simply absent.
        if context.repository.get_thread(owner_user_id=owner, thread_id=thread_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such thread")

        supplement, decisions = _resume_payload(body.resume)
        digest = f"resume:{body.interrupt_id}:{supplement or ''}:{decisions or ''}"

        if decisions:
            # A decision is a first-class, single-shot act: it moves the pending action and
            # is refused if somebody already decided. Doing this before starting the run
            # means a duplicate resume cannot even reach the graph.
            try:
                _decide(context, owner, thread_id, body.interrupt_id, body.request_id, decisions)
            except ApprovalError as failure:
                raise HTTPException(
                    status.HTTP_409_CONFLICT if failure.code == "ALREADY_DECIDED" else status.HTTP_400_BAD_REQUEST,
                    f"{failure.code}: {failure}",
                ) from failure

        run_id = _new_run_id()
        reservation = _reserve(context, owner, thread_id, body.request_id, digest, run_id)
        if not reservation.is_new:
            return _replay_response(context, owner, thread_id, reservation.run_id)

        handle = await _open(context, owner, thread_id, body.request_id, run_id)
        context.repository.update_run_status(
            owner_user_id=owner, run_id=handle.run_id, status="running"
        )

        from langgraph.types import Command

        payload: Any = Command(
            resume={"supplement": supplement} if supplement is not None else {"decisions": decisions}
        )
        context.registry.attach(
            handle,
            lambda run: _run_turn(
                context,
                run,
                owner=owner,
                thread_id=thread_id,
                payload=payload,
                interrupt_id=body.interrupt_id,
                cancel_status=CANCELLED,
            ),
        )
        return _sse_response(context, handle)

    # ---------------------------------------------------------------------- state

    @router.get("/{thread_id}/state")
    async def state(thread_id: str, request: Request) -> JSONResponse:
        owner = require_owner(request)
        if context.repository.get_thread(owner_user_id=owner, thread_id=thread_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such thread")

        runs = context.repository.list_runs(owner_user_id=owner, thread_id=thread_id)
        latest = runs[-1] if runs else None
        live = context.registry.get(thread_id)

        snapshot = _graph_snapshot(context, owner, thread_id)
        interrupts = _pending_interrupts(context, owner, thread_id, snapshot)
        if live is not None:
            current_status = "running"
        elif interrupts and latest is not None:
            current_status = INTERRUPTED
        else:
            current_status = (latest or {}).get("status", "idle")

        return JSONResponse(
            {
                "data": {
                    "thread_id": thread_id,
                    "run_id": (live.run_id if live else (latest or {}).get("run_id")),
                    "status": current_status,
                    "todos": snapshot.get("todos") or [],
                    "pending_interrupts": interrupts,
                    "last_event_id": f"{live.run_id}:{live.adapter._seq}" if live else None,
                    "cancel_requested": bool(live and live.cancel_requested),
                },
                "request_id": "",
            }
        )

    # --------------------------------------------------------------------- cancel

    @router.post("/{thread_id}/cancel")
    async def cancel(thread_id: str, request: Request) -> JSONResponse:
        owner = require_owner(request)
        if context.repository.get_thread(owner_user_id=owner, thread_id=thread_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such thread")

        stopped = context.registry.cancel(thread_id)
        # Deliberately not a rollback: a write that reached the ERP stays written. The UI is
        # told the run stopped, not that the transaction was undone.
        return JSONResponse(
            {
                "data": {
                    "thread_id": thread_id,
                    "cancel_requested": stopped,
                    "note": "已提交的 ERP 写操作不会回滚",
                },
                "request_id": "",
            }
        )

    return router


# --------------------------------------------------------------------------- #
# running one turn
# --------------------------------------------------------------------------- #


async def _run_turn(
    context: WebContext,
    handle: RunHandle,
    *,
    owner: str,
    thread_id: str,
    payload: Any,
    interrupt_id: str | None = None,
    cancel_status: str = CANCELLED,
) -> None:
    """Stream one turn, publishing normalised events and persisting as it goes."""
    adapter = handle.adapter
    config = _graph_config(owner, thread_id, interrupt_id)
    config["configurable"]["application_run_id"] = handle.run_id
    from agent.middlewares.context_injection import ContextInjectionMiddleware, evaluate_run
    from agent.middlewares.memory_update import MemoryUpdateMiddleware
    from agent.persistence.scoped_store import UserScopedStore
    from agent.run_budget import SharedRunBudget
    from agent.run_evidence import RunEvidence

    evidence = RunEvidence()
    budget = SharedRunBudget(config=context.budget_config, repository=context.repository,
                             owner=owner, thread_id=thread_id)
    config["callbacks"] = [*config.get("callbacks", []), budget, evidence]

    def store_provider(user: str) -> UserScopedStore:
        return UserScopedStore(context.resources.store, user)
    preferences = ContextInjectionMiddleware(store_provider=store_provider)
    user_text = ""
    if isinstance(payload, Mapping):
        for message in payload.get("messages", []):
            if isinstance(message, Mapping) and message.get("role") == "user":
                user_text = str(message.get("content") or "")
    query = user_text
    handle.publish(adapter.run_started())

    assistant_text: list[str] = []
    cancelled = False
    interrupts: list[dict[str, Any]] = []
    episode_store = context.extra.get("planning_episode_store")
    episode_id = None
    episode_error = None
    episode_guard = None
    try:
        async with asyncio.timeout(budget.clock.remaining()):
            if episode_store is not None:
                goal = context.resources.database["planning_goals"].find_one({
                    "owner_user_id": owner, "thread_id": thread_id, "goal_id": thread_id})
                if goal is not None:
                    from agent.evolution.orchestration import EpisodeCallbacks
                    identity = context.extra["planning_model_identity"]
                    episode_id = episode_store.begin_run(owner, thread_id, thread_id, handle.run_id,
                        model=identity, interrupt_id=interrupt_id,
                        public={"user_text": user_text, "goal": _episode_goal(goal)})
                    config["configurable"]["planning_episode_id"] = episode_id
                    episode_guard = EpisodeCallbacks(episode_store, owner, episode_id, handle.run_id)
                    config["configurable"]["planning_evidence_guard"] = episode_guard
                    config["callbacks"].append(episode_guard)
            graph = await _setup_call(handle, lambda: _provide_graph(context, owner, thread_id))
            # Resume reads the original question from the same graph. A failed or expired
            # initializer must never be called again during settlement.
            if not query:
                snapshot = await _setup_call(handle, lambda: _graph_snapshot(
                    context, owner, thread_id, graph=graph))
                for message in reversed(snapshot.get("messages") or []):
                    role = message.get("role") if isinstance(message, Mapping) else getattr(message, "type", None)
                    if role in {"user", "human"}:
                        content = message.get("content") if isinstance(message, Mapping) else message.content
                        query = str(content or "")
                        break
            if user_text:
                await _setup_call(handle, lambda: preferences.apply_user_preferences(
                    owner_user_id=owner, message=user_text, source_is_user=True))
                payload = await _setup_call(handle, lambda: _resume_revised_goal(
                    context, owner, thread_id, graph, payload))
            async for part in graph.astream(
                payload,
                config,
                stream_mode=["messages", "values"],
                subgraph=True,
                version="v2",
            ):
                if handle.cancel_requested:
                    cancelled = True
                    adapter.cancelled()
                    break
                for event in adapter.consume(part):
                    if event.event == "token":
                        assistant_text.append(str(event.payload.get("text") or ""))
                    if event.event == "interrupt":
                        _record_interrupt(context, owner, thread_id, event)
                    handle.publish(event)
            if not cancelled and adapter.terminal_status != FAILED:
                interrupts = await _setup_call(handle, lambda: _pending_interrupts(
                    context, owner, thread_id, graph=graph))
    except asyncio.CancelledError:
        cancelled = True
        adapter.cancelled()
    except TimeoutError:
        failure = budget.expired()
        episode_error = failure.code
        handle.publish(adapter.error(code=failure.code, message=str(failure), retryable=False))
    except Exception as failure:  # noqa: BLE001 - surfaced to the client, not swallowed
        episode_error = _error_code(failure)
        LOGGER.exception("run %s failed while streaming", handle.run_id)
        handle.publish(
            adapter.error(code=_error_code(failure), message=str(failure), retryable=False)
        )
    # ToolNode may turn a child's budget exception into an error ToolMessage, after which
    # the parent can still answer. That must not convert exhaustion into a completed run.
    if budget.failure is not None and adapter.terminal_status != FAILED:
        handle.publish(adapter.error(code=budget.failure.code, message=str(budget.failure), retryable=False))
    if episode_guard is not None and episode_guard.failure is not None:
        episode_error = episode_guard.failure.code
        handle.publish(adapter.error(code=episode_error, message=str(episode_guard.failure), retryable=False))

    # The terminal state comes from the checkpoint and the adapter, never from "no more
    # tokens arrived".
    if cancelled:
        final = cancel_status
    elif adapter.terminal_status == FAILED:
        final = FAILED
    elif interrupts:
        final = INTERRUPTED
    else:
        final = COMPLETED

    text = "".join(assistant_text).strip()
    if text:
        _persist_assistant_message(context, owner, thread_id, handle, text)

    try:
        calls = evidence.calls()
        should_update, reason = evaluate_run(
            run_status=final, tool_calls=calls, denied_writes=evidence.denied_writes()
        )
        MemoryUpdateMiddleware(store_provider=store_provider).apply(
            owner_user_id=owner, query=query, run_status=final, tool_calls=calls,
            should_update=should_update, reason=reason,
        )
    except Exception:  # noqa: BLE001 - persistence failure must be visible, not a false pass
        LOGGER.exception("run %s could not persist procurement memory", handle.run_id)
        final = FAILED
        handle.publish(adapter.error(code="MEMORY_PERSISTENCE_FAILED",
                                     message="采购运行已经结束，但记忆保存失败；请查看已完成的工具结果。", retryable=False))

    if episode_id is not None and episode_store is not None:
        try:
            episode_store.export(owner, episode_id)
            goal = context.resources.database["planning_goals"].find_one({
                "owner_user_id": owner, "thread_id": thread_id, "goal_id": thread_id})
            approvals = [{**{k: getattr(action, k) for k in (
                "interrupt_id", "tool_name", "payload_sha256", "status", "planning_binding", "decided_at")},
                "payload": json.loads(action.payload_bytes)}
                for action in context.approvals.store.list_for_thread(owner, thread_id)]
            episode_store.settle(owner, episode_id, handle.run_id, status=final,
                budget=budget.as_dict(), goal=_episode_goal(goal or {}), approvals=approvals,
                error_code=episode_error or (budget.failure.code if budget.failure else None))
        except Exception:  # noqa: BLE001 - an evidence gap cannot become a pass
            LOGGER.exception("run %s could not persist planning evidence", handle.run_id)
            final = FAILED
            handle.publish(adapter.error(code="EPISODE_EVIDENCE_FAILED",
                message="规划轨迹归档失败；已写入的订单不会回滚。", retryable=False))

    context.repository.update_run_status(
        owner_user_id=owner, run_id=handle.run_id, status=final,
        budget_usage=budget.as_dict(),
    )

    # Settle every record *before* telling the client the run ended. A browser that reacts to
    # `done` by refreshing state must not find the run still marked active in memory while
    # the database already says otherwise — that window is exactly what a refresh hits.
    await context.registry.release(handle)

    done = adapter.finish(status=final, content=text or None)
    if done is not None:
        handle.publish(done)


async def _setup_call(handle: RunHandle, call: Callable[..., Any], *args: Any) -> Any:
    """Wait for synchronous setup without blocking cancellation or the run deadline.

    Python cannot kill a running worker thread. Cancel only its waiter; a late result
    is discarded and cannot enter astream or publish events for this run.
    """
    pending = asyncio.create_task(asyncio.to_thread(call, *args))
    try:
        while not pending.done():
            if handle.cancel_requested:
                raise asyncio.CancelledError
            await asyncio.wait({pending}, timeout=0.05)
        if handle.cancel_requested:
            raise asyncio.CancelledError
        return pending.result()
    finally:
        if not pending.done():
            pending.cancel()
        # Retrieve any concurrent failure before leaving the waiter, even on cancellation.
        await asyncio.gather(pending, return_exceptions=True)


def _error_code(failure: Exception) -> str:
    from agent.backends.sandbox_proxy import SandboxCircuitOpenError
    from agent.evolution.episodes import EvidenceError
    from agent.run_budget import BudgetExceeded

    if isinstance(failure, (BudgetExceeded, SandboxCircuitOpenError, EvidenceError)):
        return failure.code
    return type(failure).__name__.upper()


# --------------------------------------------------------------------------- #
# persistence helpers
# --------------------------------------------------------------------------- #


def _message_seq(
    context: WebContext, owner: str, thread_id: str, run_id: str, *, offset: int
) -> int:
    """Where this message sits in the thread's display order.

    Derived from the run's position, so a turn's question and answer stay adjacent. Numbering
    by *role* instead (0 for the user, 1 for the assistant) passes a single-turn test and
    then puts every question before every answer in a real conversation — which is exactly
    what the first version of this did.
    """
    runs = context.repository.list_runs(owner_user_id=owner, thread_id=thread_id)
    index = next(
        (position for position, run in enumerate(runs) if run["run_id"] == run_id), len(runs)
    )
    return index * 2 + offset


def _persist_user_message(
    context: WebContext, owner: str, thread_id: str, handle: RunHandle, text: str
) -> None:
    context.repository.upsert_display_message(
        owner_user_id=owner,
        thread_id=thread_id,
        message_id=f"user:{handle.request_id}",
        seq=_message_seq(context, owner, thread_id, handle.run_id, offset=0),
        role="user",
        content=text,
    )


def _persist_assistant_message(
    context: WebContext, owner: str, thread_id: str, handle: RunHandle, text: str
) -> None:
    context.repository.upsert_display_message(
        owner_user_id=owner,
        thread_id=thread_id,
        message_id=f"assistant:{handle.run_id}",
        seq=_message_seq(context, owner, thread_id, handle.run_id, offset=1),
        role="assistant",
        content=text,
    )


def _record_interrupt(
    context: WebContext, owner: str, thread_id: str, event: AgentEvent
) -> None:
    """Persist an approval interrupt as a pending action, before ``done`` is sent.

    Ordering matters: the client learns the run is interrupted only after the record exists,
    so a refresh can never show a pending interrupt with nothing behind it.
    """
    payload = event.payload
    candidates = payload.get("candidates") or []
    if payload.get("interrupt_type") != "hitl_approval":
        return

    requests = [item for item in candidates if item.get("type") == "approval"]
    decisions = next(
        (item.get("allowed") or [] for item in candidates if item.get("type") == "decisions"),
        [],
    )
    if not requests:
        LOGGER.warning("approval interrupt without an action request; nothing recorded")
        return

    try:
        planning_refs = [r for r in requests if r.get("planning_action_id")]
        if planning_refs:
            if len(requests) != 1:
                raise ApprovalError("BATCH_NOT_SUPPORTED", "planning approvals are reviewed individually")
            item = planning_refs[0]
            action = context.approvals.store.find(owner, thread_id, item["planning_action_id"])
            if action is None or action.planning_binding is None:
                raise ApprovalError("ACTION_NOT_FOUND", "no recorded planning action")
            from agent.approval.service import canonical_bytes
            if (item["tool_name"] != action.tool_name
                    or not action.matches_bytes(canonical_bytes(action.tool_name, item["arguments"]))):
                raise ApprovalError("PARAMETERS_CHANGED", "planning interrupt differs from recorded action")
            context.approvals._check_planning(action)
            if str(payload["interrupt_id"]) != action.interrupt_id:
                raise ApprovalError("INVALID_INTERRUPT_BINDING", "planning approval ID differs")
            return
        context.approvals.record(
            owner_user_id=owner,
            thread_id=thread_id,
            interrupt_id=str(payload["interrupt_id"]),
            interrupt_value={
                "action_requests": [
                    {
                        "name": item.get("tool_name"),
                        "args": item.get("arguments") or {},
                        "description": item.get("description") or "",
                    }
                    for item in requests
                ],
                "review_configs": [
                    {
                        "action_name": item.get("tool_name"),
                        "allowed_decisions": list(decisions),
                    }
                    for item in requests
                ],
            },
        )
    except ApprovalError as failure:
        LOGGER.warning("could not record pending action: %s", failure)


def _decide(
    context: WebContext,
    owner: str,
    thread_id: str,
    interrupt_id: str,
    request_id: str,
    decisions: Sequence[Mapping[str, Any]],
) -> None:
    action = context.approvals.store.find(owner, thread_id, interrupt_id)
    if action is not None and action.planning_binding is not None:
        if not any(i["interrupt_id"] == interrupt_id
                   for i in _pending_interrupts(context, owner, thread_id)):
            raise ApprovalError("STALE_INTERRUPT", "this planning order is not awaiting a decision")
    approve = any(str(item.get("type")) == "approve" for item in decisions)
    if approve:
        context.approvals.approve(
            owner_user_id=owner,
            thread_id=thread_id,
            interrupt_id=interrupt_id,
            request_id=request_id,
        )
    else:
        context.approvals.reject(
            owner_user_id=owner,
            thread_id=thread_id,
            interrupt_id=interrupt_id,
            request_id=request_id,
        )


# --------------------------------------------------------------------------- #
# graph access
# --------------------------------------------------------------------------- #


def _graph_config(owner: str, thread_id: str, interrupt_id: str | None) -> dict[str, Any]:
    """Run config, carrying the trusted identity the write gate reads."""
    configurable: dict[str, Any] = {"thread_id": thread_id, "owner_user_id": owner}
    if interrupt_id:
        configurable["approved_interrupt_id"] = interrupt_id
    return {"configurable": configurable}


def _episode_goal(goal: dict[str, Any]) -> dict[str, Any]:
    return {k: goal[k] for k in ("goal_id", "revision", "problem", "sources", "proposal", "orders", "revision_change")
            if k in goal}


def _resume_revised_goal(context: WebContext, owner: str, thread: str, graph: Any, payload: Any) -> Any:
    """A fresh user message may retire a stale planning tool interrupt, never approve it."""
    from langgraph.types import Command
    goal = context.resources.database["planning_goals"].find_one({
        "owner_user_id": owner, "thread_id": thread, "goal_id": thread})
    if goal is None:
        return payload
    state = graph.get_state(_graph_config(owner, thread, None))
    stale = False
    for interrupt in getattr(state, "interrupts", ()):
        value = interrupt.value
        action_id = value.get("planning_action_id") if isinstance(value, Mapping) else None
        if not action_id:
            continue
        action = context.approvals.store.find(owner, thread, action_id)
        if action is not None and action.planning_binding is not None:
            binding = action.planning_binding
            if binding["revision"] != goal["revision"] or binding["proposal_id"] != (goal["proposal"] or {}).get("id"):
                stale = True
    return Command(resume={"type": "planning_goal_revised"}, update=payload) if stale else payload


def _provide_graph(context: WebContext, owner: str, thread_id: str) -> Any:
    provider = context.extra.get("planning_graph_provider")
    if provider is not None:
        from agent.persistence.indexes import COLLECTION_PLANNING_GOALS
        if context.resources.database[COLLECTION_PLANNING_GOALS].find_one({
            "owner_user_id": owner, "thread_id": thread_id, "goal_id": thread_id,
        }) is not None:
            return provider(owner)
    return context.graph_provider(owner)


def _graph_snapshot(context: WebContext, owner: str, thread_id: str, *, graph: Any = None) -> dict[str, Any]:
    """Current checkpoint values for a thread, or an empty mapping."""
    try:
        graph = graph if graph is not None else _provide_graph(context, owner, thread_id)
        state = graph.get_state(_graph_config(owner, thread_id, None))
    except Exception:  # noqa: BLE001 - an absent checkpoint is normal for a new thread
        LOGGER.info("no checkpoint for thread %s yet", thread_id)
        return {}
    return dict(getattr(state, "values", None) or {})


def _pending_interrupts(
    context: WebContext, owner: str, thread_id: str, snapshot: Mapping[str, Any] | None = None,
    *, graph: Any = None,
) -> list[dict[str, Any]]:
    """Interrupts the checkpoint is currently parked on.

    Read from the graph state rather than from the application's own records, because the
    checkpoint is what a resume actually continues from — a stored record that the graph has
    moved past would be a lie the UI acts on.
    """
    try:
        graph = graph if graph is not None else _provide_graph(context, owner, thread_id)
        state = graph.get_state(_graph_config(owner, thread_id, None))
    except Exception:  # noqa: BLE001
        return []

    found: list[dict[str, Any]] = []
    for task in getattr(state, "tasks", ()) or ():
        for interrupt in getattr(task, "interrupts", ()) or ():
            value = getattr(interrupt, "value", None)
            if not isinstance(value, Mapping):
                continue
            from api_view.stream_adapter import describe_interrupt, public_interrupt_id

            kind, prompt, candidates = describe_interrupt(
                value, str(getattr(interrupt, "id", "") or "")
            )
            found.append(
                {
                    "interrupt_id": public_interrupt_id(value, str(getattr(interrupt, "id", "") or "")),
                    "interrupt_type": kind,
                    "prompt": prompt,
                    "candidates": candidates,
                }
            )
    return found


# --------------------------------------------------------------------------- #
# request plumbing
# --------------------------------------------------------------------------- #


def _new_thread_id() -> str:
    import uuid

    return str(uuid.uuid4())


def _new_run_id() -> str:
    """One id for one run, shared by the application record and the in-memory handle."""
    import uuid

    return str(uuid.uuid4())


def _reserve(
    context: WebContext, owner: str, thread_id: str, request_id: str, body: str, run_id: str
):
    import hashlib

    from agent.persistence.repository import RequestConflict

    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    try:
        return context.repository.reserve_run(
            owner_user_id=owner,
            thread_id=thread_id,
            run_id=run_id,
            request_id=request_id,
            request_digest=digest,
        )
    except RequestConflict as failure:
        raise HTTPException(status.HTTP_409_CONFLICT, str(failure)) from failure


async def _open(
    context: WebContext, owner: str, thread_id: str, request_id: str, run_id: str
) -> RunHandle:
    try:
        return await context.registry.open(
            thread_id=thread_id,
            owner_user_id=owner,
            request_id=request_id,
            run_id=run_id,
        )
    except ThreadBusy as failure:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"thread already has an active run ({failure.run_id})",
        ) from failure


def _sse_response(context: WebContext, handle: RunHandle) -> StreamingResponse:
    async def feed() -> AsyncIterator[str]:
        try:
            async for event in handle.drain():
                yield event.to_sse()
        except Exception:  # noqa: BLE001 - a broken client must not fail the run
            LOGGER.info("consumer for run %s disconnected", handle.run_id)

    return StreamingResponse(
        feed(), media_type="text/event-stream", headers=SSE_HEADERS
    )


def _replay_response(
    context: WebContext, owner: str, thread_id: str, run_id: str
) -> JSONResponse:
    """The answer to a retried request: the original run, not a new one."""
    run = context.repository.get_run(owner_user_id=owner, run_id=run_id) or {}
    interrupts = (_pending_interrupts(context, owner, thread_id)
                  if run.get("status") == INTERRUPTED else [])
    return JSONResponse(
        {
            "data": {
                "thread_id": thread_id,
                "run_id": run_id,
                "status": run.get("status", "unknown"),
                "replayed": True,
                "pending_interrupts": interrupts,
            },
            "request_id": "",
        }
    )


def _resume_payload(resume: Mapping[str, Any]) -> tuple[str | None, list[dict[str, Any]]]:
    """Split a resume body into a supplement or a set of decisions.

    The two are different acts, so exactly one must be present. A body carrying both is
    refused rather than silently preferring one.
    """
    has_supplement = "supplement" in resume and resume.get("supplement") is not None
    decisions = resume.get("decisions")
    has_decisions = isinstance(decisions, Sequence) and not isinstance(decisions, (str, bytes)) and bool(decisions)

    if has_supplement and has_decisions:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "resume must carry either a supplement or decisions, not both",
        )
    if not has_supplement and not has_decisions:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "resume must carry a supplement or decisions"
        )
    if isinstance(decisions, Sequence) and has_decisions:
        return None, [dict(item) for item in decisions]

    supplement = resume["supplement"]
    if not isinstance(supplement, str):
        # Refused rather than stringified. ``str()`` on the dict a caller might send produces
        # Python repr — single quotes, no valid JSON — which the tool cannot parse, so the
        # user's answer would be silently discarded and the question asked again forever. A
        # 400 says which field is wrong; the contract's type for it is a string, and a
        # structured answer travels as its JSON text.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "resume.supplement must be a string; send a structured answer as its JSON text",
        )
    return supplement, []


__all__ = ["HEARTBEAT", "SSE_HEADERS", "ChatRequest", "ResumeRequest", "build_chat_router"]
