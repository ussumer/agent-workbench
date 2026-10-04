"""v2 stream capture helpers.

Two distinct concerns, deliberately kept apart because conflating them is the mistake
the plan warns about:

* **The v2 envelope.** ``graph.stream(..., version="v2")`` yields unified ``StreamPart``
  dictionaries shaped ``{"type", "ns", "data", "interrupts"}``. ``type`` says *what*
  arrived (``values`` / ``updates`` / ``tasks`` / ``messages`` / ``custom``); ``ns``
  says *where* it came from — the empty tuple is the root graph, a non-empty tuple is a
  subgraph or a sub-agent. A consumer that reads ``type`` but ignores ``ns`` will
  attribute a sub-agent's tokens to the main agent.
* **Raw vs normalised tool arguments.** A model emits a tool call's arguments as
  fragments that are only valid JSON once concatenated. Recording the fragments is what
  makes it possible to prove the concatenation works instead of assuming it.

Everything recorded here is redacted: no key, no header, no full prompt.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

#: Stream modes this project uses. ``messages`` carries tokens, ``updates`` carries node
#: outputs, ``tasks`` carries task lifecycle, ``values`` carries full state snapshots.
DEFAULT_STREAM_MODES: tuple[str, ...] = ("messages", "updates", "tasks")

#: Keys of a v2 stream part.
STREAM_PART_KEYS: tuple[str, ...] = ("type", "ns", "data", "interrupts")


@dataclass(frozen=True)
class StreamPartRecord:
    """One v2 part, trimmed to what a consumer needs and safe to persist."""

    type: str
    namespace: tuple[str, ...]
    is_subgraph: bool
    summary: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "ns": list(self.namespace),
            "is_subgraph": self.is_subgraph,
            "summary": self.summary,
        }


@dataclass
class StreamCapture:
    """Accumulated evidence from one v2 run."""

    parts: list[StreamPartRecord] = field(default_factory=list)
    types_seen: list[str] = field(default_factory=list)
    namespaces_seen: list[tuple[str, ...]] = field(default_factory=list)
    tool_arg_fragments: list[str] = field(default_factory=list)
    tool_call_chunks: int = 0
    text_fragments: list[str] = field(default_factory=list)
    subgraphs_requested: bool = True

    @property
    def concatenated_tool_args(self) -> str:
        return "".join(self.tool_arg_fragments)

    def redacted(self) -> dict[str, Any]:
        """Evidence safe to write into a fixture or a receipt."""
        return {
            "part_count": len(self.parts),
            "subgraphs_requested": self.subgraphs_requested,
            "types_seen": sorted(set(self.types_seen)),
            "namespaces_seen": [list(ns) for ns in dict.fromkeys(self.namespaces_seen)],
            "subgraph_parts": sum(1 for part in self.parts if part.is_subgraph),
            "tool_call_chunks": self.tool_call_chunks,
            "tool_arg_fragments": list(self.tool_arg_fragments),
            "concatenated_tool_args": self.concatenated_tool_args,
            "text_fragment_count": len(self.text_fragments),
            "sample_parts": [part.as_dict() for part in self.parts[:8]],
        }


def _summarise_data(data: Any) -> str:
    """A short, type-aware description that never dumps user or model content wholesale."""
    if isinstance(data, dict):
        keys = sorted(str(key) for key in data)
        if "messages" in data and hasattr(data["messages"], "__len__"):
            return f"dict(keys={keys}, messages={len(data['messages'])})"
        return f"dict(keys={keys})"
    if isinstance(data, (list, tuple)):
        return f"{type(data).__name__}(len={len(data)})"
    return type(data).__name__


def iter_v2_parts(
    graph: Any,
    payload: Any,
    *,
    config: dict[str, Any] | None = None,
    stream_mode: Sequence[str] | str = DEFAULT_STREAM_MODES,
    subgraphs: bool = True,
) -> Iterator[dict[str, Any]]:
    """Yield raw v2 stream parts from a graph run.

    ``subgraphs=True`` is required for a part's ``ns`` to ever be non-empty. Without it
    the stream is flattened onto the root namespace, so a consumer cannot tell a
    subgraph's token from the parent's. It is on by default here because this project
    needs that attribution; callers that deliberately want the flat view can turn it off.
    """
    yield from graph.stream(
        payload,
        config=config,
        stream_mode=stream_mode,
        version="v2",
        subgraphs=subgraphs,
    )


def capture_v2(
    graph: Any,
    payload: Any,
    *,
    config: dict[str, Any] | None = None,
    stream_mode: Sequence[str] | str = DEFAULT_STREAM_MODES,
    subgraphs: bool = True,
) -> StreamCapture:
    """Run a graph under v2 streaming and record the envelope plus tool fragments."""
    capture = StreamCapture(subgraphs_requested=subgraphs)
    for part in iter_v2_parts(
        graph, payload, config=config, stream_mode=stream_mode, subgraphs=subgraphs
    ):
        part_type = str(part.get("type", ""))
        namespace = tuple(part.get("ns") or ())
        capture.types_seen.append(part_type)
        capture.namespaces_seen.append(namespace)
        capture.parts.append(
            StreamPartRecord(
                type=part_type,
                namespace=namespace,
                is_subgraph=bool(namespace),
                summary=_summarise_data(part.get("data")),
            )
        )

        if part_type == "messages":
            _absorb_message_chunk(capture, part.get("data"))

    return capture


def _absorb_message_chunk(capture: StreamCapture, data: Any) -> None:
    """Record text and tool-argument fragments from a ``messages`` part."""
    chunk = _message_chunk(data)
    if chunk is None:
        return

    for fragment in getattr(chunk, "tool_call_chunks", None) or []:
        capture.tool_call_chunks += 1
        args = fragment.get("args") if isinstance(fragment, dict) else None
        if args:
            capture.tool_arg_fragments.append(str(args))

    content = getattr(chunk, "content", None)
    if isinstance(content, str) and content:
        capture.text_fragments.append(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                capture.text_fragments.append(block["text"])


def _message_chunk(data: Any) -> Any | None:
    """The AIMessageChunk inside a ``messages`` part, whichever shape it arrives in."""
    if data is None:
        return None
    # v2 may deliver (message, metadata) or the message alone.
    if isinstance(data, tuple):
        return data[0] if data else None
    return data


def capture_tool_argument_fragments(stream: Iterable[Any]) -> tuple[list[str], str]:
    """Collect raw tool-argument fragments from a plain model stream.

    Returns ``(fragments, concatenated)``. Concatenating the fragments is the only way
    to obtain the arguments the model actually produced, because each fragment on its
    own is usually not valid JSON.
    """
    fragments: list[str] = []
    for chunk in stream:
        for call in getattr(chunk, "tool_call_chunks", None) or []:
            args = call.get("args") if isinstance(call, dict) else None
            if args:
                fragments.append(str(args))
    return fragments, "".join(fragments)
