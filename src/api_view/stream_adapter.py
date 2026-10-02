"""One place that turns framework output into the contract's SSE events.

Everything about "what the browser sees" is decided here, for two reasons. The framework's
stream is a low-level sequence of parts, and rules like *exactly one ``done``* or *an
``error`` must not be followed by ``completed``* are invariants of the run, not of any
single route — they have to be enforced where the run is, not where it is read out.

Four behaviours are load-bearing, and each of them is a thing that quietly breaks if it is
handled by string matching:

``source``
    Sub-agent output must be attributed to the sub-agent. The framework reports it under the
    namespace ``tools:<task-id>``, which is an *id*, not a name; the name comes from the
    ``task`` call's own arguments, parsed once they are complete. Guessing the name from the
    text is exactly what the contract forbids.
``tool_args``
    Arguments arrive as one-character fragments (T06 captured ``["{", '"', "a", ...]`` for
    ``{"a": 17, "b": 25}``). They are concatenated per call id and only parsed when the
    fragment carries the closing brace. A parser that fails on a fragment must not turn that
    into a stream error.
``content``
    A message's content may be a list of blocks. ``str(content)`` on that prints Python
    repr — a bug that looks like success. Only ``text`` blocks become tokens.
``done``
    Emitted once, by :meth:`StreamAdapter.finish`, from the run's terminal state. A model
    that stops talking is not a completed run, and the adapter refuses to guess: if no
    terminal state was observed, the run is reported as ``failed``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

LOGGER = logging.getLogger("rush_harness.stream")

EVENT_VERSION = 1

#: Sources that are not a named sub-agent.
MAIN_SOURCE = "main"

#: Namespace prefix the framework uses for a `task` tool invocation.
TASK_NAMESPACE_PREFIX = "tools:"

#: The run's terminal states, and what the browser is told.
COMPLETED = "completed"
INTERRUPTED = "interrupted"
FAILED = "failed"
CANCELLED = "cancelled"

TERMINAL_STATUSES = frozenset({COMPLETED, INTERRUPTED, FAILED, CANCELLED})

#: Interrupt types the UI distinguishes, in the contract's vocabulary.
SUPPLEMENT_INTERRUPT = "order_info_supplement"
APPROVAL_INTERRUPT = "hitl_approval"


@dataclass(frozen=True)
class AgentEvent:
    """One SSE event, already normalised."""

    event: str
    payload: dict[str, Any]
    seq: int
    thread_id: str
    run_id: str
    source: str
    event_id: str
    tool_call_id: str | None = None

    def envelope(self) -> dict[str, Any]:
        return {
            "v": EVENT_VERSION,
            "thread_id": self.thread_id,
            "run_id": self.run_id,
            "seq": self.seq,
            "source": self.source,
            "payload": self.payload,
            **({"tool_call_id": self.tool_call_id} if self.tool_call_id else {}),
        }

    def to_sse(self) -> str:
        """The wire form: ``id``/``event``/``data``, one blank line apart."""
        body = json.dumps(self.envelope(), ensure_ascii=False, separators=(",", ":"))
        return f"id: {self.event_id}\nevent: {self.event}\ndata: {body}\n\n"


def text_of(content: Any) -> str:
    """Visible text from a message's content, whatever shape it arrived in.

    The framework may hand back a plain string or a list of typed blocks. Reasoning blocks
    are dropped here rather than shown as the answer: the contract says only display text
    counts as a reply.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, Mapping):
        block_type = content.get("type")
        if block_type in ("text", None) and isinstance(content.get("text"), str):
            return content["text"]
        return ""
    if isinstance(content, Sequence):
        parts = []
        for block in content:
            if isinstance(block, Mapping):
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    parts.append(block["text"])
                elif isinstance(block.get("text"), str) and "type" not in block:
                    parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return ""


@dataclass
class _ToolCall:
    """Accumulated state for one tool call."""

    call_id: str
    name: str
    arguments: dict[str, Any] | None = None
    fragments: list[str] = field(default_factory=list)
    started: bool = False
    ended: bool = False


class StreamAdapter:
    """Per-run normaliser. One instance per run, never shared."""

    def __init__(
        self,
        *,
        thread_id: str,
        run_id: str,
        request_id: str = "",
    ) -> None:
        self.thread_id = thread_id
        self.run_id = run_id
        self.request_id = request_id
        self._seq = 0
        self._done_emitted = False
        self._terminal: str | None = None
        self._tool_calls: dict[str, _ToolCall] = {}
        self._subagent_by_task_id: dict[str, str] = {}
        self._cancelled = False
        self._errored = False
        #: Part types with no handler, recorded so a framework upgrade shows up as evidence
        #: rather than as silently missing events.
        self.unknown_types: dict[str, int] = {}
        self._interrupt_emitted: set[str] = set()
        self._last_todos: str | None = None

    # ------------------------------------------------------------------ emitting

    def _emit(
        self,
        event: str,
        payload: dict[str, Any],
        *,
        source: str = MAIN_SOURCE,
        tool_call_id: str | None = None,
        event_id: str | None = None,
    ) -> AgentEvent:
        self._seq += 1
        return AgentEvent(
            event=event,
            payload=payload,
            seq=self._seq,
            thread_id=self.thread_id,
            run_id=self.run_id,
            source=source,
            event_id=event_id or f"{self.run_id}:{self._seq}",
            tool_call_id=tool_call_id,
        )

    def run_started(self) -> AgentEvent:
        return self._emit("run_started", {"request_id": self.request_id, "status": "running"})

    def cancelled(self) -> None:
        """Note that cancellation was requested; the terminal status becomes cancelled."""
        self._cancelled = True

    @property
    def terminal_status(self) -> str | None:
        return self._terminal

    @property
    def done_emitted(self) -> bool:
        return self._done_emitted

    def finish(
        self, *, status: str | None = None, content: str | None = None
    ) -> AgentEvent | None:
        """Emit the single ``done`` for this run.

        Returns ``None`` when one was already emitted — repeating a terminal event would let
        a reconnecting client process two endings for one run.

        ``completed`` is only ever claimed on positive evidence: a run that errored, was
        cancelled, or simply stopped without a terminal signal is reported as ``failed``.
        """
        if self._done_emitted:
            LOGGER.info("done already emitted for run %s; ignoring the repeat", self.run_id)
            return None

        resolved = status or self._terminal
        if resolved is None:
            resolved = FAILED
            LOGGER.warning(
                "run %s ended without a terminal signal; reporting failure rather than success",
                self.run_id,
            )
        if resolved not in TERMINAL_STATUSES:
            raise ValueError(f"unknown terminal status {resolved!r}")
        if resolved == COMPLETED and (self._errored or self._cancelled):
            resolved = FAILED if self._errored else CANCELLED
        if self._cancelled and resolved == COMPLETED:
            resolved = CANCELLED

        self._terminal = resolved
        self._done_emitted = True
        payload: dict[str, Any] = {
            "status": resolved,
            "interrupted": resolved == INTERRUPTED,
        }
        if content:
            payload["content"] = content
        return self._emit("done", payload)

    # ----------------------------------------------------------------- consuming

    def consume(self, part: Mapping[str, Any]) -> Iterator[AgentEvent]:
        """Events produced by one stream part. Unknown kinds are counted, not swallowed."""
        part_type = str(part.get("type") or "")
        namespace = tuple(part.get("ns") or ())

        if part_type == "messages":
            yield from self._messages(part.get("data"), namespace)
        elif part_type == "values":
            yield from self._values(part.get("data"), namespace)
        elif part_type in ("updates", "tasks", "checkpoints", "debug", "custom"):
            # Noted for the record; these carry no browser-facing event of their own in v1.
            return
        else:
            self.unknown_types[part_type or "<empty>"] = (
                self.unknown_types.get(part_type or "<empty>", 0) + 1
            )
            LOGGER.warning("unhandled stream part type %r; recorded for triage", part_type)
            return

        # Interrupts ride alongside the part in v2 and take precedence over anything the
        # part itself would have produced: the run stopped, and the client must be told
        # before it is told the run ended.
        interrupts = part.get("interrupts") or ()
        for interrupt in interrupts:
            event = self._interrupt(interrupt, namespace)
            if event is not None:
                yield event

    def _source_for(self, namespace: Sequence[str]) -> str:
        """The agent a namespace belongs to, by name.

        ``tools:<id>`` is a task id; the name was recorded when that ``task`` call's
        arguments completed. Falling back to the raw segment would put an id where the UI
        expects a name, so an unknown task is reported as the generic sub-agent rather than
        pretending.
        """
        for segment in reversed(tuple(namespace)):
            text = str(segment)
            if text.startswith(TASK_NAMESPACE_PREFIX):
                task_id = text[len(TASK_NAMESPACE_PREFIX) :]
                return self._subagent_by_task_id.get(task_id, "sub-agent")
            if ":" in text:
                return text.split(":", 1)[0]
        return MAIN_SOURCE

    # ---------------------------------------------------------------- messages

    def _messages(self, data: Any, namespace: Sequence[str]) -> Iterator[AgentEvent]:
        source = self._source_for(namespace)
        for message, metadata in _message_pairs(data):
            chunks = getattr(message, "tool_call_chunks", None) or ()
            text = text_of(getattr(message, "content", None))

            # A chunk may carry text and a tool-call fragment at once.
            if text:
                message_id = str(getattr(message, "id", "") or metadata.get("message_id") or "")
                yield self._emit(
                    "token",
                    {"text": text, "message_id": message_id},
                    source=source,
                    event_id=f"{self.run_id}:{message_id}" if message_id else None,
                )

            for chunk in chunks:
                yield from self._tool_chunk(chunk, source)

    def _tool_chunk(self, chunk: Any, source: str) -> Iterator[AgentEvent]:
        call_id = str(_get(chunk, "id") or "")
        name = str(_get(chunk, "name") or "")
        index = _get(chunk, "index")

        if not call_id and index is not None:
            # Some providers omit the id on continuation fragments; fall back to the call
            # that is still open, so fragments are not dropped on the floor.
            open_calls = [call for call in self._tool_calls.values() if not call.ended]
            if len(open_calls) == 1:
                call_id = open_calls[0].call_id

        if not call_id:
            self.unknown_types["tool_chunk_without_id"] = (
                self.unknown_types.get("tool_chunk_without_id", 0) + 1
            )
            return

        call = self._tool_calls.get(call_id)
        if call is None:
            call = _ToolCall(call_id=call_id, name=name)
            self._tool_calls[call_id] = call
        elif name and not call.name:
            call.name = name

        if not call.started:
            call.started = True
            yield self._emit("tool_start", {"name": call.name}, source=source, tool_call_id=call_id)

        fragment = _get(chunk, "args")
        if fragment:
            call.fragments.append(str(fragment))
            yield self._emit(
                "tool_args", {"delta": str(fragment)}, source=source, tool_call_id=call_id
            )
            joined = "".join(call.fragments)
            if _looks_complete(joined):
                parsed = _parse_arguments(joined)
                if parsed is not None:
                    call.arguments = parsed
                    self._remember_subagent(call)

    def _remember_subagent(self, call: _ToolCall) -> None:
        """Record which sub-agent a ``task`` call selected.

        This mapping is what lets sub-agent output be attributed by *name* later, so it is
        taken from the call's own JSON arguments rather than from anything the model wrote.
        """
        if call.name != "task" or not isinstance(call.arguments, Mapping):
            return
        target = call.arguments.get("subagent_type")
        if isinstance(target, str) and target:
            self._subagent_by_task_id[call.call_id] = target

    # ------------------------------------------------------------------ values

    def _values(self, data: Any, namespace: Sequence[str]) -> Iterator[AgentEvent]:
        if not isinstance(data, Mapping):
            return

        todos = data.get("todos")
        if todos:
            fingerprint = json.dumps(todos, ensure_ascii=False, sort_keys=True)
            if fingerprint != self._last_todos:
                self._last_todos = fingerprint
                yield self._emit("todos", {"items": [_todo_item(todo) for todo in todos]})

        # Messages that arrived as whole objects (rather than chunks) end their tool calls.
        messages = data.get("messages")
        if isinstance(messages, Sequence) and not isinstance(messages, (str, bytes)):
            for message in messages:
                tool_calls = getattr(message, "tool_calls", None) or ()
                for call in tool_calls:
                    call_id = str(_get(call, "id") or "")
                    if call_id and call_id in self._tool_calls:
                        tracked = self._tool_calls[call_id]
                        if not tracked.arguments:
                            tracked.arguments = dict(_get(call, "args") or {})
                            self._remember_subagent(tracked)

        yield from self._tool_results(messages)

    def _tool_results(self, messages: Any) -> Iterator[AgentEvent]:
        if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
            return
        for message in messages:
            call_id = str(getattr(message, "tool_call_id", "") or "")
            if not call_id:
                continue
            call = self._tool_calls.get(call_id)
            if call is None:
                continue
            if call.ended:
                continue
            call.ended = True

            status = getattr(message, "status", "success") or "success"
            body = text_of(getattr(message, "content", None)) or _stringify(
                getattr(message, "content", None)
            )
            parsed = _parse_envelope(body)

            if parsed is not None and parsed.get("ok") is False:
                error = parsed.get("error") or {}
                yield self._emit(
                    "tool_result",
                    {
                        "ok": False,
                        "error": {
                            "code": error.get("code", "TOOL_ERROR"),
                            "message": error.get("message", ""),
                            "retryable": bool(error.get("retryable")),
                        },
                    },
                    source=self._source_for(()),
                    tool_call_id=call_id,
                )
            else:
                payload: dict[str, Any] = {"ok": status != "error"}
                if parsed is not None:
                    payload["data"] = parsed.get("data")
                else:
                    payload["data"] = parsed if parsed is not None else _truncate(body)
                yield self._emit(
                    "tool_result", payload, source=MAIN_SOURCE, tool_call_id=call_id
                )

            yield self._emit(
                "tool_end",
                {"status": "success" if status != "error" else "error"},
                source=MAIN_SOURCE,
                tool_call_id=call_id,
            )

    # --------------------------------------------------------------- interrupts

    def _interrupt(self, interrupt: Any, namespace: Sequence[str]) -> AgentEvent | None:
        interrupt_id = str(getattr(interrupt, "id", "") or "")
        if interrupt_id and interrupt_id in self._interrupt_emitted:
            return None
        value = getattr(interrupt, "value", None)
        if not isinstance(value, Mapping):
            value = {"raw": _truncate(str(value))}

        kind, prompt, candidates = describe_interrupt(value, interrupt_id)
        if interrupt_id:
            self._interrupt_emitted.add(interrupt_id)
        self._terminal = INTERRUPTED
        return self._emit(
            "interrupt",
            {
                "interrupt_id": interrupt_id,
                "interrupt_type": kind,
                "prompt": prompt,
                "candidates": candidates,
            },
            source=self._source_for(namespace),
        )

    # ------------------------------------------------------------------- errors

    def error(self, *, code: str, message: str, retryable: bool = False) -> AgentEvent:
        """A run-level failure. Marks the run so ``completed`` can no longer be claimed."""
        self._errored = True
        self._terminal = FAILED
        return self._emit(
            "error", {"code": code, "message": message, "retryable": retryable}
        )

    def artifact(
        self, *, artifact_id: str, name: str, mime: str, size: int
    ) -> AgentEvent:
        return self._emit(
            "artifact", {"artifact_id": artifact_id, "name": name, "mime": mime, "size": size}
        )

    def mark(self, status: str) -> None:
        """Record an observed terminal state without emitting ``done`` yet."""
        if status in TERMINAL_STATUSES:
            self._terminal = status


def describe_interrupt(
    value: Mapping[str, Any], interrupt_id: str
) -> tuple[str, str, list[dict[str, Any]]]:
    """Classify an interrupt by its structured payload.

    The two layers are told apart by what the payload contains, never by matching words in a
    prompt: supplementation carries ``missing_fields``, approval carries ``action_requests``.
    """
    if "missing_fields" in value:
        missing = [str(field) for field in value.get("missing_fields") or ()]
        question = str(value.get("question") or "请补充缺少的信息。")
        return (
            SUPPLEMENT_INTERRUPT,
            question,
            [{"type": "supplement", "missing_fields": missing}],
        )

    requests = value.get("action_requests")
    if isinstance(requests, Sequence) and not isinstance(requests, (str, bytes)) and requests:
        # The action requests are carried through, not summarised away: the API records the
        # pending action from this event, and re-deriving the arguments from a description
        # would make the frozen payload depend on a string that a later edit could change.
        candidates: list[dict[str, Any]] = []
        for request in requests:
            if not isinstance(request, Mapping):
                continue
            candidates.append(
                {
                    "type": "approval",
                    "interrupt_id": interrupt_id,
                    "tool_name": str(request.get("name") or ""),
                    "arguments": dict(request.get("args") or {}),
                    "description": str(request.get("description") or ""),
                }
            )

        decisions: list[str] = []
        for config in value.get("review_configs") or ():
            if isinstance(config, Mapping):
                decisions = [str(item) for item in config.get("allowed_decisions") or ()]
        candidates.append({"type": "decisions", "allowed": decisions})

        return APPROVAL_INTERRUPT, "这笔写操作需要你确认后才能执行。", candidates

    return "unknown", str(value.get("question") or "运行已暂停，等待输入。"), []


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _get(obj: Any, key: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def _message_pairs(data: Any) -> Iterator[tuple[Any, Mapping[str, Any]]]:
    """Yield ``(message, metadata)`` from whatever shape the messages stream uses."""
    if isinstance(data, tuple) and len(data) == 2:
        yield data[0], (data[1] if isinstance(data[1], Mapping) else {})
        return
    if isinstance(data, Sequence) and not isinstance(data, (str, bytes)):
        for item in data:
            if isinstance(item, tuple) and len(item) == 2:
                yield item[0], (item[1] if isinstance(item[1], Mapping) else {})
            else:
                yield item, {}


def _looks_complete(joined: str) -> bool:
    stripped = joined.strip()
    if not stripped.startswith("{"):
        return True  # not JSON at all; nothing more will arrive
    depth = 0
    in_string = False
    escaped = False
    for char in stripped:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return True
    return False


def _parse_arguments(joined: str) -> dict[str, Any] | None:
    """Parse accumulated fragments, or return ``None`` if they are not JSON yet.

    A fragment that cannot be parsed is *not* an error: by definition, fragments are
    incomplete. Returning ``None`` keeps a partial parse from becoming a stream failure.
    """
    text = joined.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _parse_envelope(body: str) -> dict[str, Any] | None:
    """The tool-result envelope, when the tool returned one."""
    text = body.strip()
    if not text.startswith("{"):
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _truncate(text: str, limit: int = 2000) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def _todo_item(todo: Any) -> dict[str, Any]:
    if isinstance(todo, Mapping):
        return {
            "content": str(todo.get("content") or ""),
            "status": str(todo.get("status") or "pending"),
        }
    return {"content": str(todo), "status": "pending"}


def encode_sse(events: Iterable[AgentEvent]) -> Iterator[str]:
    for event in events:
        yield event.to_sse()


__all__ = [
    "APPROVAL_INTERRUPT",
    "CANCELLED",
    "COMPLETED",
    "EVENT_VERSION",
    "FAILED",
    "INTERRUPTED",
    "MAIN_SOURCE",
    "SUPPLEMENT_INTERRUPT",
    "TERMINAL_STATUSES",
    "AgentEvent",
    "StreamAdapter",
    "describe_interrupt",
    "encode_sse",
    "text_of",
]
