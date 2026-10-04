"""A minimal graph served over the Agent Protocol.

Purpose: prove the Agent Protocol transport works end to end — a real server process,
reached by the official ``langgraph_sdk`` client and by DeepAgents ``AsyncSubAgent``.

The node here is deliberately deterministic. It is *not* standing in for the model:
CAP-01..CAP-05 exercise the real model through the real graph. This graph exists so
that a transport failure cannot be confused with a model failure, and so the async
service can be smoke-tested without spending tokens.

It imports only what the isolated service environment provides (``langgraph``), because
that environment is deliberately separate from the application lock — ``langgraph-api``
pins ``grpcio`` in a way that cannot coexist with ``opensandbox-server``.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict


class EchoState(TypedDict, total=False):
    """Input/output shape of the minimal graph."""

    text: str
    result: str
    seen_by: str


def normalise(state: EchoState) -> dict[str, Any]:
    """Uppercase and trim the incoming text, recording who handled it."""
    raw = state.get("text", "")
    return {"result": raw.strip().upper(), "seen_by": "inmem-async-service"}


def build() -> Any:
    """Compile the graph. Referenced from langgraph.json as ``./graph.py:build``."""
    builder = StateGraph(EchoState)
    builder.add_node("normalise", normalise)
    builder.add_edge(START, "normalise")
    builder.add_edge("normalise", END)
    return builder.compile()
