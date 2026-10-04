"""Ownership of in-flight runs.

A run must not be an ``asyncio.Task`` that nobody holds. If it were, three things would go
wrong at once: a client disconnect would either kill the run or leak it, a second request on
the same thread could not be refused because nothing knows the first is still going, and
shutdown could not wait for anything.

So a run is an object with an owner, a queue and a terminal state, kept in a registry keyed
by thread. Disconnecting detaches the *consumer*; the run keeps going and keeps persisting,
which is what lets a returning browser read state from Mongo rather than needing a replay of
tokens it missed.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from api_view.stream_adapter import AgentEvent, StreamAdapter

LOGGER = logging.getLogger("rush_harness.run_registry")

#: Events buffered for a consumer that has not caught up. Bounded on purpose: a consumer
#: that is 1000 events behind is gone, and buffering without limit would turn a slow reader
#: into a memory leak.
QUEUE_LIMIT = 256

SENTINEL = object()


class ThreadBusy(RuntimeError):
    """A run is already active on this thread."""

    def __init__(self, run_id: str) -> None:
        super().__init__(f"thread already has an active run ({run_id})")
        self.run_id = run_id


@dataclass
class RunHandle:
    """One in-flight run and everything needed to observe or stop it."""

    run_id: str
    thread_id: str
    owner_user_id: str
    request_id: str
    adapter: StreamAdapter
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=QUEUE_LIMIT))
    task: asyncio.Task | None = None
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    cancel_requested: bool = False
    _attached: bool = False

    def publish(self, event: AgentEvent) -> None:
        """Hand an event to the (single) consumer, dropping the oldest if it has stalled."""
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(event)
            except (asyncio.QueueEmpty, asyncio.QueueFull):  # pragma: no cover - race
                LOGGER.warning("dropped event %s for run %s", event.event, self.run_id)

    def close(self) -> None:
        self.finished.set()
        try:
            self.queue.put_nowait(SENTINEL)
        except asyncio.QueueFull:  # pragma: no cover - consumer gone
            pass

    async def drain(self) -> AsyncIterator[AgentEvent]:
        """Yield events until the run closes. Only one consumer at a time."""
        self._attached = True
        try:
            while True:
                item = await self.queue.get()
                if item is SENTINEL:
                    return
                yield item
        finally:
            self._attached = False

    @property
    def active(self) -> bool:
        return not self.finished.is_set()


class RunRegistry:
    """Keeps at most one active run per thread, and can stop them on shutdown."""

    def __init__(self, *, queue_limit: int = QUEUE_LIMIT) -> None:
        self._runs: dict[str, RunHandle] = {}
        self._lock = asyncio.Lock()
        self._queue_limit = queue_limit
        self._shutting_down = False

    # ------------------------------------------------------------------ lifecycle

    async def open(
        self,
        *,
        thread_id: str,
        owner_user_id: str,
        request_id: str,
        run_id: str | None = None,
    ) -> RunHandle:
        """Reserve the thread for a new run, or refuse because one is already active.

        ``run_id`` is accepted from the caller so the run recorded in the application
        collection and the run held in memory are the *same* run. Generating it here as well
        produced two ids for one run, and the status update then matched no document — a bug
        that looks like a silent no-op until someone refreshes.
        """
        async with self._lock:
            if self._shutting_down:
                raise ThreadBusy("the service is shutting down")
            existing = self._runs.get(thread_id)
            if existing is not None and existing.active:
                raise ThreadBusy(existing.run_id)

            run_id = run_id or str(uuid.uuid4())
            handle = RunHandle(
                run_id=run_id,
                thread_id=thread_id,
                owner_user_id=owner_user_id,
                request_id=request_id,
                adapter=StreamAdapter(
                    thread_id=thread_id, run_id=run_id, request_id=request_id
                ),
            )
            self._runs[thread_id] = handle
            return handle

    def attach(
        self, handle: RunHandle, body: Callable[[RunHandle], Awaitable[None]]
    ) -> None:
        """Run ``body`` as a task the registry owns, and mark the handle finished after."""
        async def runner() -> None:
            try:
                await body(handle)
            except asyncio.CancelledError:  # pragma: no cover - shutdown path
                LOGGER.info("run %s cancelled", handle.run_id)
                raise
            except Exception:  # noqa: BLE001 - a failed run is reported through the stream
                LOGGER.exception("run %s failed", handle.run_id)
            finally:
                handle.close()

        handle.task = asyncio.create_task(runner(), name=f"run-{handle.run_id}")

    def get(self, thread_id: str) -> RunHandle | None:
        handle = self._runs.get(thread_id)
        return handle if handle is not None and handle.active else None

    def cancel(self, thread_id: str) -> bool:
        """Ask the run on this thread to stop.

        The flag is the request; enforcement happens where the stream is consumed, so a
        cancellation never interrupts a write that has already been committed.
        """
        handle = self._runs.get(thread_id)
        if handle is None or not handle.active:
            return False
        handle.cancel_requested = True
        LOGGER.info("cancellation requested for run %s", handle.run_id)
        return True

    async def release(self, handle: RunHandle) -> None:
        async with self._lock:
            if self._runs.get(handle.thread_id) is handle:
                del self._runs[handle.thread_id]

    async def shutdown(self, *, timeout: float = 10.0) -> int:
        """Stop accepting runs, ask the live ones to finish, then wait briefly for them."""
        async with self._lock:
            self._shutting_down = True
            live = [handle for handle in self._runs.values() if handle.active]

        for handle in live:
            handle.cancel_requested = True

        if live:
            LOGGER.info("waiting for %d active run(s) to stop", len(live))
            await asyncio.wait(
                [asyncio.ensure_future(handle.finished.wait()) for handle in live],
                timeout=timeout,
            )
        for handle in live:
            if handle.task is not None and not handle.task.done():
                handle.task.cancel()
        return len(live)

    # -------------------------------------------------------------------- state

    def active_threads(self) -> list[str]:
        return [thread for thread, handle in self._runs.items() if handle.active]

    def snapshot(self) -> dict[str, Any]:
        return {
            "active": len(self.active_threads()),
            "runs": [
                {
                    "run_id": handle.run_id,
                    "thread_id": handle.thread_id,
                    "owner_user_id": handle.owner_user_id,
                    "cancel_requested": handle.cancel_requested,
                    "terminal": handle.adapter.terminal_status,
                    "done_emitted": handle.adapter.done_emitted,
                }
                for handle in self._runs.values()
            ],
        }


__all__ = [
    "QUEUE_LIMIT",
    "SENTINEL",
    "RunHandle",
    "RunRegistry",
    "ThreadBusy",
]
