"""Launching, watching and cancelling a background run through the official SDK.

This layer is deliberately thin, because the contract says what it may be: "这层仅做归属、
幂等与官方 SDK 适配，不自创替代 Agent Protocol". So there is no queue, no worker loop and no
in-process task — a task is a real thread and run on a real server process, and everything
here is a translation between that server's vocabulary and this project's owners.

Three refusals are built in, each because the obvious alternative is a lie:

* **An unreachable service never becomes ``completed``.** A poll that fails keeps the last
  known status and records why. Reporting success for a run nobody can see would be the worst
  possible failure mode: the user waits for a report that is not coming.
* **A run the service does not know is ``lost``, not ``failed``.** The dev server's runs do
  not survive a restart; the difference between "it broke" and "we no longer know" matters to
  somebody deciding whether to retry.
* **An update never claims to have rewritten the running input.** The SDK cannot do that, so
  an accepted update starts a follow-up run on the same async thread and is recorded as
  exactly that.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from agent.async_tasks.store import (
    RUN_STATUS_MAP,
    TERMINAL,
    AsyncStatus,
    AsyncTaskRecord,
    MongoAsyncTaskStore,
    new_task_id,
    now,
)

LOGGER = logging.getLogger("rush_harness.async_tasks.service")

#: The graph the background analyst runs, as registered in
#: ``infra/agent-protocol/langgraph.json``. Discovered by ``graph_id`` through the protocol
#: rather than by a hard-coded assistant id, because assistant ids are allocated by the
#: service and differ between a fresh database and a reused one.
ASYNC_ANALYST_GRAPH_ID = "procurement-analyst"


class ProtocolUnavailable(RuntimeError):
    """The Agent Protocol service could not be reached, or answered with an error."""


class RunNotFound(LookupError):
    """The service is up and says it has no such run.

    A distinct type because it means something different from every other failure: not "the
    call broke" but "this run is not there any more". The dev server does not persist runs
    across a restart, so this is the ordinary way a task becomes unknown — and it is *not*
    the same as failed.
    """


class ProtocolClient(Protocol):
    """The slice of the official client this project uses.

    Narrow on purpose: a fake for tests implements four methods, and the real implementation
    is a thin adapter over ``langgraph_sdk``. Nothing here is invented — each method is an
    operation the SDK actually has.
    """

    def assistant_id(self, graph_id: str) -> str: ...

    def create_thread(self) -> str: ...

    def start_run(self, *, thread_id: str, assistant_id: str, instruction: str) -> str: ...

    def run_status(self, *, thread_id: str, run_id: str) -> str: ...

    def run_output(self, *, thread_id: str, run_id: str) -> dict[str, Any]: ...

    def cancel_run(self, *, thread_id: str, run_id: str) -> None: ...


class ArtifactFiler(Protocol):
    """Whatever files a finished background report. ``ArtifactService`` satisfies it.

    Narrower than the service itself on purpose: the only thing this layer is allowed to do
    with an artifact is create one, under an owner and a thread it does not take from a caller.
    """

    def register(
        self,
        *,
        owner_user_id: str,
        thread_id: str,
        name: str,
        content: bytes,
        mime: str,
        source: str,
    ) -> Any: ...


class SdkProtocolClient:
    """``langgraph_sdk`` sync client, adapted to :class:`ProtocolClient`.

    The SDK's sync client is used and called from a worker thread, matching how the rest of
    the application treats blocking I/O. There is no re-implementation of the protocol: the
    URLs, headers and payload shapes all come from the SDK.
    """

    def __init__(self, base_url: str, *, client: Any | None = None) -> None:
        self._base_url = base_url
        self._client = client
        self._assistants: dict[str, str] = {}

    @property
    def client(self) -> Any:
        if self._client is None:
            from langgraph_sdk import get_sync_client

            self._client = get_sync_client(url=self._base_url)
        return self._client

    def assistant_id(self, graph_id: str) -> str:
        if graph_id in self._assistants:
            return self._assistants[graph_id]
        try:
            assistants = self.client.assistants.search()
        except Exception as failure:  # noqa: BLE001 - reported as unavailable, not crashed
            raise ProtocolUnavailable(f"cannot list assistants: {failure}") from failure
        for assistant in assistants:
            if assistant.get("graph_id") == graph_id:
                found = str(assistant["assistant_id"])
                self._assistants[graph_id] = found
                return found
        raise ProtocolUnavailable(
            f"the service exposes no {graph_id!r} graph; got "
            f"{[item.get('graph_id') for item in assistants]}"
        )

    def create_thread(self) -> str:
        try:
            thread = self.client.threads.create()
        except Exception as failure:  # noqa: BLE001
            raise ProtocolUnavailable(f"cannot create a thread: {failure}") from failure
        return str(thread["thread_id"])

    def start_run(self, *, thread_id: str, assistant_id: str, instruction: str) -> str:
        try:
            run = self.client.runs.create(
                thread_id,
                assistant_id=assistant_id,
                input={"messages": [{"role": "user", "content": instruction}]},
            )
        except Exception as failure:  # noqa: BLE001
            raise ProtocolUnavailable(f"cannot start a run: {failure}") from failure
        return str(run["run_id"])

    def run_status(self, *, thread_id: str, run_id: str) -> str:
        try:
            run = self.client.runs.get(thread_id, run_id)
        except Exception as failure:  # noqa: BLE001
            # 404 is the service answering. Everything else is the service failing to
            # answer, and the two must not be collapsed: one means "unknown", the other
            # means "try again", and neither means "finished".
            if getattr(failure, "status_code", None) == 404:
                raise RunNotFound(run_id) from failure
            raise ProtocolUnavailable(f"cannot read run {run_id}: {failure}") from failure
        return str(run.get("status") or "")

    def run_output(self, *, thread_id: str, run_id: str) -> dict[str, Any]:
        """The state the run left behind.

        Read through the thread rather than the run: the SDK's run object carries the status and
        the error, and the values a graph produced live in the state. Both answers come from the
        service, which is what keeps this an adaptation instead of a second implementation of
        something the Protocol already does.
        """
        try:
            state = self.client.threads.get_state(thread_id)
        except Exception as failure:  # noqa: BLE001 - reported as unavailable, not crashed
            raise ProtocolUnavailable(
                f"cannot read the state of run {run_id}: {failure}"
            ) from failure
        values = state.get("values") if isinstance(state, dict) else None
        return dict(values) if isinstance(values, dict) else {}

    def cancel_run(self, *, thread_id: str, run_id: str) -> None:
        try:
            self.client.runs.cancel(thread_id, run_id)
        except Exception as failure:  # noqa: BLE001
            raise ProtocolUnavailable(f"cannot cancel run {run_id}: {failure}") from failure


class TaskNotFound(LookupError):
    """No such task **for this owner**. Callers turn this into a 404, never a 403."""


class AsyncTaskService:
    """The four operations the business endpoints expose."""

    def __init__(
        self,
        *,
        store: MongoAsyncTaskStore,
        protocol: ProtocolClient,
        graph_id: str,
        clock: Callable[[], str] = now,
        artifacts: ArtifactFiler | None = None,
    ) -> None:
        self._store = store
        self._protocol = protocol
        self._graph_id = graph_id
        self._clock = clock
        #: Where a finished report is filed. Optional so a caller that only exercises the
        #: bookkeeping can omit it — but the live stack always supplies one, because a task
        #: whose report nobody keeps is a task the user cannot open.
        self._artifacts = artifacts

    @property
    def graph_id(self) -> str:
        return self._graph_id

    # ------------------------------------------------------------------ launch

    def launch(
        self,
        *,
        owner_user_id: str,
        parent_thread_id: str,
        request_id: str,
        instruction: str,
    ) -> tuple[AsyncTaskRecord, bool]:
        """Start a background run. Returns ``(record, created)``.

        A repeated ``request_id`` returns the original task untouched. That check happens in
        the database rather than here: two submissions arriving together would both pass an
        in-process check and both start a run.
        """
        if not owner_user_id:
            raise ValueError("owner_user_id is required")
        if not instruction.strip():
            raise ValueError("instruction must not be empty")

        existing = self._store.find_by_request(owner_user_id, request_id)
        if existing is not None:
            LOGGER.info("launch request %s already produced %s", request_id, existing.task_id)
            return existing, False

        assistant_id = self._protocol.assistant_id(self._graph_id)
        async_thread_id = self._protocol.create_thread()
        async_run_id = self._protocol.start_run(
            thread_id=async_thread_id, assistant_id=assistant_id, instruction=instruction
        )

        record = AsyncTaskRecord(
            task_id=new_task_id(),
            owner_user_id=owner_user_id,
            parent_thread_id=parent_thread_id,
            instruction=instruction,
            async_thread_id=async_thread_id,
            async_run_id=async_run_id,
            status=str(AsyncStatus.QUEUED),
            created_at=self._clock(),
            updated_at=self._clock(),
            request_id=request_id,
        )
        stored = self._store.create(record)
        if stored is None:
            # Lost the insert race: somebody else's identical request won, and that is the
            # answer this caller should get too.
            return self._store.find_by_request(owner_user_id, request_id) or record, False
        return stored, True

    # -------------------------------------------------------------------- read

    def status(self, *, owner_user_id: str, task_id: str) -> AsyncTaskRecord:
        """The task, refreshed from the service when it is not already finished."""
        record = self._store.find(owner_user_id, task_id)
        if record is None:
            raise TaskNotFound(task_id)
        if str(record.status) in TERMINAL:
            return record
        return self._refresh(record)

    def _refresh(self, record: AsyncTaskRecord) -> AsyncTaskRecord:
        try:
            raw = self._protocol.run_status(
                thread_id=record.async_thread_id, run_id=record.async_run_id
            )
        except RunNotFound:
            # The service answered and does not know this run. Saying "lost" is the honest
            # report; the contract explicitly refuses to promise lossless recovery from the
            # dev server, and calling it completed would be the one unrecoverable mistake.
            return self._store.transition(
                record.owner_user_id,
                record.task_id,
                status=str(AsyncStatus.LOST),
                last_error="Agent Protocol 服务不再认识这个 run（可能重启过），状态未知",
            ) or record
        except ProtocolUnavailable as failure:
            # Keep the last known status. Flipping to failed here would blame the task for the
            # network, and flipping to completed is exactly what must never happen.
            LOGGER.warning("could not refresh %s: %s", record.task_id, failure)
            return self._store.transition(
                record.owner_user_id,
                record.task_id,
                status=str(record.status),
                last_error=str(failure),
            ) or record

        if not raw:
            # An empty status is not a status. Treat it as unknown for the same reason.
            return self._store.transition(
                record.owner_user_id,
                record.task_id,
                status=str(AsyncStatus.LOST),
                last_error="Agent Protocol 返回了空的 run 状态",
            ) or record

        mapped = RUN_STATUS_MAP.get(raw, str(AsyncStatus.RUNNING))
        if mapped == str(record.status):
            return record
        updated = self._store.transition(
            record.owner_user_id,
            record.task_id,
            status=mapped,
            last_error="" if mapped == str(AsyncStatus.COMPLETED) else record.last_error,
        ) or record
        if mapped == str(AsyncStatus.COMPLETED) and not updated.artifact_ids:
            # Filing happens on the way to ``completed`` and only once: the poll that first
            # sees the run finished is the one that files, and the guard means a second poll
            # cannot file the same report twice.
            return self._collect(updated)
        return updated

    def _collect(self, record: AsyncTaskRecord) -> AsyncTaskRecord:
        """File the finished run's report so the user can download it.

        The report's *content* comes from the run — the graph writes the Markdown, because it is
        the only party that knows what it analysed. What happens here is the part the contract
        gives this layer: ownership (the artifact belongs to the task's owner and its parent
        thread, never to whoever happened to poll) and SDK adaptation (reading the state back).
        A failure to file is recorded and the task stays as it is, because a report that could
        not be kept is not a reason to claim the analysis failed.
        """
        if self._artifacts is None:
            return record
        try:
            values = self._protocol.run_output(
                thread_id=record.async_thread_id, run_id=record.async_run_id
            )
        except ProtocolUnavailable as failure:
            LOGGER.warning("cannot read the output of %s: %s", record.task_id, failure)
            return record

        content = values.get("report_markdown")
        if not isinstance(content, str) or not content.strip():
            LOGGER.info("task %s finished with no report to file", record.task_id)
            return record

        name = str(values.get("report_name") or f"{record.task_id}.md")
        try:
            item = self._artifacts.register(
                owner_user_id=record.owner_user_id,
                thread_id=record.parent_thread_id,
                name=name,
                content=content.encode("utf-8"),
                mime="text/markdown",
                source=f"background:{record.task_id}",
            )
        except ValueError as failure:
            LOGGER.warning("cannot file the report of %s: %s", record.task_id, failure)
            return record

        LOGGER.info("task %s filed its report as %s", record.task_id, item.artifact_id)
        return (
            self._store.transition(
                record.owner_user_id,
                record.task_id,
                status=str(AsyncStatus.COMPLETED),
                artifact_ids=[item.artifact_id],
            )
            or record
        )

    def list_for_parent(
        self, *, owner_user_id: str, parent_thread_id: str
    ) -> list[AsyncTaskRecord]:
        return self._store.list_for_parent(owner_user_id, parent_thread_id)

    # ------------------------------------------------------------------ update

    def update(
        self,
        *,
        owner_user_id: str,
        task_id: str,
        request_id: str,
        instruction: str,
    ) -> tuple[AsyncTaskRecord, bool]:
        """Add an instruction. Returns ``(record, accepted)``.

        ``accepted=False`` means this ``request_id`` was already handled — a retried update
        must not start a second follow-up run.
        """
        record = self._store.find(owner_user_id, task_id)
        if record is None:
            raise TaskNotFound(task_id)
        if str(record.status) in TERMINAL:
            raise ValueError(
                f"任务已经是 {record.status}，不能再追加指令；"
                "请开一个新的后台任务，而不是谎称当前任务被改写"
            )
        if not instruction.strip():
            raise ValueError("instruction must not be empty")

        for entry in record.updates:
            if entry.get("request_id") == request_id:
                return record, False

        follow_up_run_id = self._protocol.start_run(
            thread_id=record.async_thread_id,
            assistant_id=self._protocol.assistant_id(self._graph_id),
            instruction=instruction,
        )
        entry = {
            "request_id": request_id,
            "instruction": instruction,
            "handling": "followup_run",
            # The contract's wording, made concrete: the SDK cannot rewrite a running input,
            # so this is a *new* run on the same thread and it is recorded as one.
            "note": "SDK 不支持改写已运行的输入；已按官方能力在同一 async thread 上追加一次 run",
            "run_id": follow_up_run_id,
            "async_run_id": follow_up_run_id,
            "accepted_at": self._clock(),
        }
        updated = self._store.append_update(owner_user_id, task_id, entry)
        if updated is None:
            raise TaskNotFound(task_id)
        # Status now follows the follow-up run, not the original one.
        return (
            self._store.transition(
                owner_user_id,
                task_id,
                status=str(AsyncStatus.QUEUED),
                async_run_id=follow_up_run_id,
            )
            or updated,
            True,
        )

    # ------------------------------------------------------------------ cancel

    def cancel(
        self, *, owner_user_id: str, task_id: str, request_id: str
    ) -> AsyncTaskRecord:
        """Stop the run. Terminal states are final, so a late poll cannot revive it."""
        record = self._store.find(owner_user_id, task_id)
        if record is None:
            raise TaskNotFound(task_id)
        if str(record.status) in TERMINAL:
            return record

        self._protocol.cancel_run(thread_id=record.async_thread_id, run_id=record.async_run_id)

        moved = self._store.transition(
            owner_user_id,
            task_id,
            status=str(AsyncStatus.CANCELLED),
            expect=frozenset(item for item in AsyncStatus if str(item) not in TERMINAL),
        )
        if moved is None:
            # Somebody else reached a terminal state first; theirs stands.
            return self._store.find(owner_user_id, task_id) or record
        return moved

    async def alaunch(self, **kwargs: Any) -> tuple[AsyncTaskRecord, bool]:
        return await asyncio.to_thread(self.launch, **kwargs)

    async def astatus(self, **kwargs: Any) -> AsyncTaskRecord:
        return await asyncio.to_thread(self.status, **kwargs)

    async def aupdate(self, **kwargs: Any) -> tuple[AsyncTaskRecord, bool]:
        return await asyncio.to_thread(self.update, **kwargs)

    async def acancel(self, **kwargs: Any) -> AsyncTaskRecord:
        return await asyncio.to_thread(self.cancel, **kwargs)


def protocol_from_settings(settings: Mapping[str, Any] | Any) -> SdkProtocolClient:
    """Build the SDK client from configuration, failing loudly when it is absent."""
    base_url = getattr(settings, "agent_protocol_base_url", None) or ""
    if not base_url:
        raise ProtocolUnavailable(
            "AGENT_PROTOCOL_BASE_URL 未配置，无法启动后台分析任务"
        )
    return SdkProtocolClient(str(base_url))


def build_async_task_service(
    database: Any,
    *,
    base_url: str | None = None,
    graph_id: str = ASYNC_ANALYST_GRAPH_ID,
    protocol: ProtocolClient | None = None,
    artifacts: ArtifactFiler | None = None,
) -> AsyncTaskService:
    """Assemble the service the way the application does.

    One definition so a second caller cannot wire a different graph id or a different store by
    accident. ``base_url`` defaults to the documented address; nothing here connects, because
    reachability is a runtime question and not a reason to refuse to start the application.
    """
    if protocol is None:
        if base_url is None:
            from agent.config import ServiceAddresses

            base_url = ServiceAddresses.from_env().agent_protocol_base_url
        protocol = SdkProtocolClient(base_url)
    return AsyncTaskService(
        store=MongoAsyncTaskStore(database),
        protocol=protocol,
        graph_id=graph_id,
        artifacts=artifacts,
    )


__all__ = [
    "ASYNC_ANALYST_GRAPH_ID",
    "ArtifactFiler",
    "AsyncTaskService",
    "ProtocolClient",
    "ProtocolUnavailable",
    "RunNotFound",
    "SdkProtocolClient",
    "TaskNotFound",
    "build_async_task_service",
    "protocol_from_settings",
]
