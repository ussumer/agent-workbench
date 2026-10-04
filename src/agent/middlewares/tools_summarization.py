"""Offloading, archiving, and the budget the main and sub-agents share.

Three rules from the contract are implemented here, and each is a thing that fails quietly
if it is only documented:

**A large tool response must become a file, not vanish.** Truncating it loses data the user
may have paid for; dropping it loses the ability to answer a follow-up. So a result over the
threshold is written to the workspace and replaced by a reference that says where it went.

**The full history is archived before anything summarises it.** Summarisation is lossy by
design; if the only copy of a turn is inside a summary, the loss is permanent. The archive
write therefore happens first, and the summariser is only allowed to run once it has.

**The main agent and its sub-agents share one invocation budget.** The API installs a
shared callback across delegated calls and atomically accounts for the thread in Mongo.
The framework middleware here provides additional graph-local guards; its private state
counters do not become shared merely by reusing an instance.

This module deliberately does not add a second tool-error handler: the framework's ToolNode
already turns a tool exception into a visible error result, and a second layer that swallowed
errors would hide the first one.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage

LOGGER = logging.getLogger("rush_harness.middleware.summarization")

#: Results larger than this are written to a file instead of being carried in the transcript.
#: Chosen to sit well under the model's window while staying larger than a normal page of
#: results, so ordinary answers are not turned into files.
OFFLOAD_THRESHOLD_BYTES = 8192

#: Where offloaded results live, inside the sandbox workspace.
OFFLOAD_DIR = "/workspace/offload"

#: Where the *complete* transcript is archived, in the owner's store namespace.
ARCHIVE_KEY = "archive"

#: Message fields that must survive compaction. Losing any of these strands a run.
PRESERVED_KEYS: tuple[str, ...] = ("todos", "__interrupt__")


def serialised_size(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return len(str(value).encode("utf-8"))


def should_offload(text: str, *, threshold: int = OFFLOAD_THRESHOLD_BYTES) -> bool:
    return len(text.encode("utf-8")) > threshold


def offload_name(tool_name: str, call_id: str) -> str:
    """A stable, collision-resistant file name for one tool result."""
    digest = hashlib.sha256(f"{tool_name}:{call_id}".encode()).hexdigest()[:12]
    safe_tool = "".join(char if char.isalnum() or char in "-_" else "_" for char in tool_name)
    return f"{OFFLOAD_DIR}/{safe_tool}-{digest}.json"


def offload_reference(*, path: str, size: int, preview: str, tool_name: str) -> str:
    """The envelope that replaces an oversized result.

    It is a *successful* result that points at the data. Returning a failure here would tell
    the model the call did not work, and it did.
    """
    return json.dumps(
        {
            "ok": True,
            "data": {
                "offloaded": True,
                "path": path,
                "bytes": size,
                "tool": tool_name,
                "preview": preview,
                "note": "结果过大，已完整写入上述路径；需要细节请读取该文件，不要凭摘要推断。",
            },
            "error": None,
            "request_id": None,
        },
        ensure_ascii=False,
    )


class ToolsSummarizationMiddleware(AgentMiddleware):
    """Writes oversized tool results to the workspace and hands back a reference."""

    def __init__(
        self,
        *,
        writer: Callable[[str, bytes], None],
        threshold: int = OFFLOAD_THRESHOLD_BYTES,
    ) -> None:
        super().__init__()
        self._writer = writer
        self._threshold = threshold
        self.offloaded: list[dict[str, Any]] = []

    def _maybe_offload(self, result: Any) -> Any:
        """Offload an oversized tool result; pass anything else straight through.

        A tool does not have to return a message. The framework's own filesystem tools answer
        with a ``Command`` — a state update — and there is no "content" to offload in one.
        Reading ``.content`` without checking is a crash in the middle of the tool node rather
        than a no-op, which is how this was found: the live stack was the first thing to run
        the real tool set through this middleware.
        """
        if not isinstance(result, ToolMessage):
            return result

        body = result.content
        if not isinstance(body, str) or not should_offload(body, threshold=self._threshold):
            return result

        name = str(getattr(result, "name", "") or "tool")
        call_id = str(getattr(result, "tool_call_id", "") or "")
        path = offload_name(name, call_id)
        try:
            self._writer(path, body.encode("utf-8"))
        except Exception as failure:  # noqa: BLE001 - the result is still worth returning
            # If the file cannot be written the original body must be kept. Returning a
            # reference to a file that does not exist would be worse than a long message.
            LOGGER.warning("offload to %s failed (%s); keeping the result inline", path, failure)
            return result

        preview = body[:200]
        self.offloaded.append({"path": path, "bytes": len(body.encode("utf-8")), "tool": name})
        LOGGER.info("offloaded %s result (%d bytes) to %s", name, len(body.encode("utf-8")), path)
        return ToolMessage(
            content=offload_reference(
                path=path, size=len(body.encode("utf-8")), preview=preview, tool_name=name
            ),
            tool_call_id=call_id,
            name=name,
            status=getattr(result, "status", "success"),
        )

    def wrap_tool_call(self, request: ToolCallRequest, handler: Callable[..., Any]) -> Any:
        return self._maybe_offload(handler(request))

    async def awrap_tool_call(self, request: ToolCallRequest, handler: Callable[..., Any]) -> Any:
        return self._maybe_offload(await handler(request))


# --------------------------------------------------------------------------- #
# archiving before summarisation
# --------------------------------------------------------------------------- #


@dataclass
class ArchiveRecord:
    thread_id: str
    key: str
    message_count: int
    bytes: int
    created_at: str
    digest: str


def archive_thread_history(
    scoped_store: Any,
    *,
    thread_id: str,
    messages: Sequence[Any],
    run_id: str = "",
    checkpoint_ns: str = "",
    scope: str = "main",
    preserved_state: Mapping[str, Any] | None = None,
) -> ArchiveRecord:
    """Write the complete transcript to the owner's store.

    Called *before* any summarisation. The archive is deliberately not read by the agent:
    it exists so a lossy summary is never the only surviving copy.
    """
    from agent.main_agent import memories_namespace

    payload = [_serialise_message(message) for message in messages]
    encoded = json.dumps(payload, ensure_ascii=False, default=str)
    key = f"{ARCHIVE_KEY}/{thread_id}/{uuid.uuid4().hex}"
    value = {"thread_id": thread_id, "run_id": run_id, "messages": payload,
             "checkpoint_ns": checkpoint_ns, "scope": scope,
             "preserved_state": dict(preserved_state or {})}
    scoped_store.put(
        memories_namespace(scoped_store.user_id),
        key,
        value,
    )
    item = scoped_store.get(memories_namespace(scoped_store.user_id), key)
    if item is None or item.value != value:
        raise RuntimeError("conversation archive readback failed; summarization refused")
    record = ArchiveRecord(
        thread_id=thread_id,
        key=key,
        message_count=len(payload),
        bytes=len(encoded.encode("utf-8")),
        created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        digest=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    )
    LOGGER.info("archived %d messages for thread %s at %s", len(payload), thread_id, key)
    return record


def _serialise_message(message: Any) -> dict[str, Any]:
    if isinstance(message, Mapping):
        return {"type": str(message.get("type") or message.get("role") or "unknown"), **dict(message)}
    if hasattr(message, "model_dump"):
        return message.model_dump(mode="json")
    return {
        "type": type(message).__name__,
        "content": getattr(message, "content", ""),
        "id": getattr(message, "id", ""),
    }


def compaction_preserves(before: Mapping[str, Any], after: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """Whether a compaction kept everything that must survive it.

    Returns ``(ok, problems)``. Checked rather than assumed, because a summariser that drops
    the todo list or a pending interrupt leaves a run that cannot be continued and gives no
    sign that anything was lost.
    """
    problems: list[str] = []
    for key in PRESERVED_KEYS:
        expected = before.get(key)
        actual = after.get(key)
        if expected in (None, (), [], {}):
            continue
        if actual in (None, (), [], {}):
            problems.append(f"压缩后丢失了 {key}")
        elif key == "todos" and actual != expected:
            problems.append(f"压缩后 todos 发生变化：{expected} -> {actual}")
    return (not problems, problems)


# --------------------------------------------------------------------------- #
# budgets
# --------------------------------------------------------------------------- #


@dataclass
class BudgetConfig:
    """Shared limits. Small defaults are the point: a runaway run must stop, visibly.

    ``model_calls_per_run`` is 80 rather than the 40 first chosen here, and the reason is a
    measurement rather than a preference: a live run of D07 — create a skill → verify it against
    a fixed sample → publish → assign → rebuild the sandbox → use it again — reached 40 while
    still making progress, with 140 tool calls already behind it, and was cut off mid-task
    (`Model call limits exceeded: run limit (40/40)`). The 40 had been chosen without a real
    case exercising it. The guard still stops a runaway; it no longer stops a chain the demo
    itself asks for.
    """

    model_calls_per_run: int = 80
    model_calls_per_thread: int = 200
    #: 120 for the same reason as the line above, and from the same measurement: the D07 chain
    #: is read-heavy (it lists and reads its way around two skill trees before writing
    #: anything), so a run that was still making progress stopped on ``Tool call limit
    #: exceeded`` at 60 with the skill half-built. The guard is unchanged in kind — a runaway
    #: still stops, visibly — it just sits past what this case needs.
    tool_calls_per_run: int = 120
    tool_calls_per_thread: int = 400
    run_seconds: float = 900.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_calls_per_run": self.model_calls_per_run,
            "model_calls_per_thread": self.model_calls_per_thread,
            "tool_calls_per_run": self.tool_calls_per_run,
            "tool_calls_per_thread": self.tool_calls_per_thread,
            "run_seconds": self.run_seconds,
        }


@dataclass
class RunBudget:
    """Wall-clock budget for one run, tracked outside the graph.

    The model and tool limits are the framework's middleware; elapsed time has no equivalent,
    so it is tracked here and checked by the run loop.
    """

    limit_seconds: float
    clock: Callable[[], float] = time.monotonic
    started_at: float | None = None

    def __post_init__(self) -> None:
        # Start from the *injected* clock. Defaulting to time.monotonic() here would mix two
        # time sources, and the elapsed calculation would then compare a real timestamp with
        # a fake one — which is the kind of bug that only shows up as a nonsensical number.
        if self.started_at is None:
            self.started_at = self.clock()

    def elapsed(self) -> float:
        assert self.started_at is not None  # Established by __post_init__.
        return self.clock() - self.started_at

    def exhausted(self) -> bool:
        return self.elapsed() >= self.limit_seconds

    def remaining(self) -> float:
        return max(0.0, self.limit_seconds - self.elapsed())


def build_budget_middlewares(config: BudgetConfig | None = None) -> list[Any]:
    """Framework graph-local guards, additional to the API's aggregate callback budget."""
    from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware

    resolved = config or BudgetConfig()
    return [
        ModelCallLimitMiddleware(
            thread_limit=resolved.model_calls_per_thread,
            run_limit=resolved.model_calls_per_run,
            # The API keeps committed ERP results and reports failed on exhaustion.
            # A synthetic terminal AI message must not turn an exhausted run into success.
            exit_behavior="error",
        ),
        ToolCallLimitMiddleware(
            thread_limit=resolved.tool_calls_per_thread,
            run_limit=resolved.tool_calls_per_run,
            exit_behavior="error",
        ),
    ]


def build_compaction_tool(model: Any, backend: Any) -> Any:
    """The active ``compact_conversation`` tool, from the framework.

    Uses the framework's own middleware rather than hand-rolling a summariser, so the
    summarisation policy stays in one place and the tool cannot diverge from it.
    """
    from agent.middlewares.conversation_archive import build_archived_compaction

    return build_archived_compaction(model, backend)


__all__ = [
    "ARCHIVE_KEY",
    "OFFLOAD_DIR",
    "OFFLOAD_THRESHOLD_BYTES",
    "PRESERVED_KEYS",
    "ArchiveRecord",
    "BudgetConfig",
    "RunBudget",
    "ToolsSummarizationMiddleware",
    "archive_thread_history",
    "build_budget_middlewares",
    "build_compaction_tool",
    "compaction_preserves",
    "offload_name",
    "offload_reference",
    "serialised_size",
    "should_offload",
]
