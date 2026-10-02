"""Record the signatures of the installed framework pieces.

T06 exists partly so that T07/T08/T10/T11/T20 are written against what is actually
installed rather than against a recollection of the documentation. This module is that
record: it resolves each symbol, captures its signature, and reports failures instead of
hiding them.

It also captures the v2 streaming vocabulary, because "which stream modes exist" and
"what a part looks like" are the two things most likely to be assumed incorrectly.
"""

from __future__ import annotations

import inspect
import typing
from typing import Any

#: Symbols later tasks construct. ``module:attribute`` so a rename is a test failure.
TRACKED_SYMBOLS: tuple[tuple[str, str], ...] = (
    ("deepagents", "create_deep_agent"),
    ("deepagents", "SubAgent"),
    ("deepagents", "AsyncSubAgent"),
    ("deepagents.backends", "CompositeBackend"),
    ("deepagents.backends", "StoreBackend"),
    ("deepagents.backends", "FilesystemBackend"),
    ("deepagents.backends", "StateBackend"),
    ("deepagents.backends.protocol", "BackendProtocol"),
    ("deepagents.backends.protocol", "SandboxBackendProtocol"),
    ("langgraph.checkpoint.mongodb", "MongoDBSaver"),
    ("langgraph.store.mongodb", "MongoDBStore"),
    ("langgraph.store.memory", "InMemoryStore"),
    ("langgraph.types", "Command"),
    ("langgraph.types", "interrupt"),
    ("mcp.server.fastmcp", "FastMCP"),
    ("langchain_mcp_adapters.client", "MultiServerMCPClient"),
    ("langchain.agents.middleware", "AgentMiddleware"),
    ("langchain_openai", "ChatOpenAI"),
    ("opensandbox", "Sandbox"),
    ("opensandbox.sync", "SandboxSync"),
)


def _signature_of(obj: Any) -> str | None:
    try:
        return str(inspect.signature(obj))
    except (TypeError, ValueError):
        return None


def collect_signatures() -> dict[str, Any]:
    """Resolve every tracked symbol and record its signature.

    Returns a mapping with ``resolved`` and ``failures`` sections so a caller can assert
    both "it exists" and "I know its shape".
    """
    resolved: dict[str, Any] = {}
    failures: dict[str, str] = {}

    for module_name, attribute in TRACKED_SYMBOLS:
        key = f"{module_name}.{attribute}"
        try:
            import importlib

            module = importlib.import_module(module_name)
        except Exception as failure:  # noqa: BLE001 - reported, not swallowed
            failures[key] = f"import failed: {type(failure).__name__}: {failure}"
            continue
        obj = getattr(module, attribute, None)
        if obj is None:
            failures[key] = "attribute missing"
            continue
        entry: dict[str, Any] = {"signature": _signature_of(obj)}
        annotations = getattr(obj, "__annotations__", None)
        if annotations:
            entry["fields"] = sorted(annotations)
        if inspect.isclass(obj):
            entry["abstract_methods"] = sorted(getattr(obj, "__abstractmethods__", ()) or ())
        resolved[key] = entry

    return {"resolved": resolved, "failures": failures}


def streaming_vocabulary() -> dict[str, Any]:
    """The v2 streaming contract: allowed modes, version literal and part keys.

    Recorded separately from the class signatures because later work depends on the
    *distinction* between a part's ``type`` (what arrived) and its ``ns`` (where it came
    from), and that distinction is easy to lose.
    """
    from langgraph.graph.state import CompiledStateGraph

    hints = typing.get_type_hints(CompiledStateGraph.stream)
    stream_mode = hints.get("stream_mode")
    version = hints.get("version")

    return {
        "stream_mode_literals": _literals(stream_mode),
        "version_literals": _literals(version),
        "part_keys": ["type", "ns", "data", "interrupts"],
        "root_namespace": [],
        "note": (
            "type identifies what arrived; ns identifies where from. An empty ns is the "
            "root graph, a non-empty ns is a subgraph or sub-agent."
        ),
    }


def _literals(annotation: Any) -> list[str]:
    """Extract string literals from a ``Literal`` or an iterable of them."""
    found: list[str] = []
    for argument in typing.get_args(annotation):
        if isinstance(argument, str):
            found.append(argument)
        else:
            found.extend(item for item in typing.get_args(argument) if isinstance(item, str))
    return sorted(dict.fromkeys(found))


def signature_report() -> dict[str, Any]:
    """Everything T06 records about the installed API surface."""
    return {
        "symbols": collect_signatures(),
        "streaming": streaming_vocabulary(),
    }
