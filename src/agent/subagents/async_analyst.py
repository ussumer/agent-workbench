"""The supervisor-side declaration of the background analyst.

`AsyncSubAgent` is the framework's own shape, and ``graph_id`` is what makes it *async*:
``deepagents`` splits the sub-agent specs on that field (``graph.py``: a spec with
``graph_id`` goes to ``AsyncSubAgentMiddleware``, one without goes to the synchronous
``task`` tool). Supplying the declaration is therefore the whole of the integration — the
framework generates the ``launch``/``check``/``update``/``cancel``/``list`` tools itself, and
the contract is explicit that this project must not build a second Agent Protocol.

Two things this module deliberately does **not** do:

* It does not give the background analyst any ERP tool. Its capability surface lives in the
  graph (``infra/agent-protocol/analyst_graph.py``, read-only) and is enforced again by the
  main process's internal endpoints. A sub-agent that could order would be able to widen its
  own authority from a background thread where nobody is watching.
* It does not hard-code the service URL. The address has a documented default and is passed
  in, so a test can point at the service it started.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.async_tasks.service import ASYNC_ANALYST_GRAPH_ID

#: Name the supervisor's tools refer to. Distinct from the synchronous ``procurement-analyst``
#: so a request can choose which one it means rather than getting whichever registered first.
ASYNC_ANALYST_NAME = "procurement-analyst-async"

WRITE_TOOL_NAMES: tuple[str, ...] = ()

DESCRIPTION = (
    "后台采购分析：在独立服务上跑一次只读分析，不阻塞当前对话。"
    "适合「顺便看看哪些物料要补货」这类可以稍后取结果的问题。"
    "它没有下单权限，也不能改单；需要写操作时用同步的 procurement-order。"
)


def build_async_analyst_spec(
    *,
    url: str,
    headers: Mapping[str, str] | None = None,
    name: str = ASYNC_ANALYST_NAME,
    graph_id: str = ASYNC_ANALYST_GRAPH_ID,
) -> dict[str, Any]:
    """The ``AsyncSubAgent`` spec, as a plain dict.

    A dict rather than a ``TypedDict`` instance because the framework's type is a
    ``TypedDict``, which at runtime is just a dict — and building it here keeps ``deepagents``
    out of this module's import graph, so the declaration can be asserted without the
    framework's async extras installed.
    """
    spec: dict[str, Any] = {
        "name": name,
        "description": DESCRIPTION,
        "graph_id": graph_id,
        "url": url,
    }
    if headers:
        spec["headers"] = dict(headers)
    return spec


def async_analyst_tool_names(spec: Mapping[str, Any] | None = None) -> tuple[str, ...]:
    """The tools the framework generates for this sub-agent.

    Named here so a test can assert the surface without reaching into the middleware: the
    contract's "no write permission" claim is about this list, and a list nobody can read is
    not a claim anybody can check.
    """
    return ("launch", "check", "update", "cancel", "list")


__all__ = [
    "ASYNC_ANALYST_NAME",
    "DESCRIPTION",
    "WRITE_TOOL_NAMES",
    "async_analyst_tool_names",
    "build_async_analyst_spec",
]
