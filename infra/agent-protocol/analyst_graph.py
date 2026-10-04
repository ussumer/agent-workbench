"""The read-only procurement analyst served over the Agent Protocol.

This graph runs in the isolated service environment, which has ``langgraph``, ``langchain_core``
and ``httpx`` and **no model client**. That constraint shapes the design rather than being
worked around:

* The graph is a real LangGraph, not a coroutine dressed as one, and it runs on a real
  Protocol server in its own process — which is what the acceptance asks to see.
* It reaches this project through the main process's internal endpoints. It does not import
  ``agent.*`` (impossible here), and it does not touch the sandbox directly: the contract makes
  the main process the only holder of the sandbox lease.
* **It has no write tools at all.** ``WRITE_TOOLS`` is empty and ``READ_TOOLS`` is the complete
  list, so "异步没有写单权限" is a fact about this module rather than a request in a prompt.
  The main process enforces the same list independently, so a future edit here that tried to
  order something would still be refused there.

A model would let the analyst decide *what* to look at. It is not wired in yet, and inventing
a plausible narrative for that step would be worse than a deterministic pipeline that says
exactly what it did.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from typing import Any

import httpx
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

#: Where the main process lives, and the token that proves we are the internal caller.
MAIN_SERVICE_URL_ENV = "MAIN_SERVICE_BASE_URL"
INTERNAL_TOKEN_ENV = "INTERNAL_SERVICE_TOKEN"

DEFAULT_MAIN_SERVICE_URL = "http://127.0.0.1:8000"

#: The complete tool surface of this graph. Empty on purpose — see the module docstring.
READ_TOOLS: tuple[str, ...] = ("inventory_warning", "planning_goal")
WRITE_TOOLS: tuple[str, ...] = ()

#: The graph id this module is registered under, and the graph id of the supervisor-side
#: declaration that points at it.
GRAPH_ID = "procurement-analyst"

READ_TIMEOUT_SECONDS = 60.0

#: What the finished report is filed as. Fixed rather than timestamped so a reader can tell
#: two runs apart by their task ids rather than by a string only the writer understands.
REPORT_NAME = "补货分析报告.md"


class AnalystState(TypedDict, total=False):
    """Input is ``messages``; everything else is produced here."""

    messages: list[Any]
    instruction: str
    warnings: dict[str, Any]
    analysis: dict[str, Any]
    report: dict[str, Any]
    #: The report as a file, written here rather than by whoever files it: the graph is the
    #: only party that knows what it analysed, and a report assembled downstream would be a
    #: second rendering of the same numbers, free to disagree with this one.
    report_markdown: str
    report_name: str
    error: str
    owner_user_id: str
    parent_thread_id: str
    planning: bool


def _main_service() -> tuple[str, str]:
    return (
        os.environ.get(MAIN_SERVICE_URL_ENV, DEFAULT_MAIN_SERVICE_URL).rstrip("/"),
        os.environ.get(INTERNAL_TOKEN_ENV, ""),
    )


def plan(state: AnalystState) -> dict[str, Any]:
    """Take the instruction from the last user message."""
    messages = state.get("messages") or []
    instruction = ""
    for message in reversed(messages):
        if isinstance(message, dict):
            role, content = message.get("role"), message.get("content")
        else:
            role, content = getattr(message, "type", ""), getattr(message, "content", "")
        if role in ("user", "human") and content:
            instruction = str(content)
            break
    return {"instruction": instruction,
            "owner_user_id": str(state.get("owner_user_id") or ""),
            "parent_thread_id": str(state.get("parent_thread_id") or "")}


def read_warnings(state: AnalystState) -> dict[str, Any]:
    """Ask the main process for the inventory warnings, as the task's owner.

    Raises on failure so the run ends as ``error``. A background task that quietly produced an
    empty analysis because a call failed would be indistinguishable from one that found
    nothing to reorder.
    """
    base_url, token = _main_service()
    if not token:
        raise RuntimeError(
            f"{INTERNAL_TOKEN_ENV} is not set; the background analyst cannot authenticate"
        )

    planning = any(word in state.get("instruction", "") for word in ("规划", "报价", "来源", "revision"))
    response = httpx.post(
        f"{base_url}/internal/analysis/read",
        json={
            "owner_user_id": state.get("owner_user_id") or os.environ.get("ASYNC_OWNER_USER_ID", "demo-a"),
            "thread_id": state.get("parent_thread_id") or os.environ.get("ASYNC_PARENT_THREAD_ID", ""),
            "operation_id": "analyst-read",
            "tool": "planning_goal" if planning else READ_TOOLS[0],
            "arguments": {"page": 1, "page_size": 50},
        },
        headers={"x-internal-service-token": token},
        timeout=READ_TIMEOUT_SECONDS,
        trust_env=False,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"read failed with HTTP {response.status_code}: {response.text[:300]}"
        )

    body = response.json()
    payload = body.get("data", {}).get("result")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as failure:
            raise RuntimeError(f"gateway returned unparsable JSON: {failure}") from failure
    if not isinstance(payload, dict):
        raise RuntimeError("gateway returned no readable payload")
    return {"warnings": payload, "planning": planning}


def analyse(state: AnalystState) -> dict[str, Any]:
    """Turn the warnings into quantities and a cost estimate, in Decimal.

    Deliberately arithmetic rather than prose: the numbers a background report carries have to
    be reproducible, and a figure nobody can recompute is not a finding.
    """
    warnings = state.get("warnings") or {}
    if state.get("planning"):
        return {"analysis": {"planning": True, "revision": warnings.get("revision"),
                              "goal_id": warnings.get("goal_id"),
                              "decision": warnings.get("decision"),
                              "order_count": len(warnings.get("orders") or []),
                              "source_count": len(warnings.get("sources") or []),
                              "missing_sources": [o.get("part_id") for o in (warnings.get("problem") or {}).get("offers", [])
                                                   if o.get("source_status") != "verified"]}}
    data = warnings.get("data") if isinstance(warnings.get("data"), dict) else warnings
    items = (data or {}).get("items") or []

    lines: list[dict[str, Any]] = []
    total_quantity = 0
    total_value = Decimal("0.00")
    for item in items:
        if not isinstance(item, dict):
            continue
        quantity = item.get("suggested_quantity")
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
            continue
        price = item.get("catalog_price")
        line: dict[str, Any] = {
            "part_id": str(item.get("part_id") or ""),
            "quantity": quantity,
        }
        if price is not None:
            amount = (Decimal(str(price)) * quantity).quantize(Decimal("0.01"))
            line["unit_price"] = f"{Decimal(str(price)):.2f}"
            line["amount"] = f"{amount:.2f}"
            total_value += amount
        lines.append(line)
        total_quantity += quantity

    return {
        "analysis": {
            "line_count": len(lines),
            "total_quantity": total_quantity,
            "estimated_total": f"{total_value:.2f}",
            "currency": "CNY",
            "lines": lines,
        }
    }


def finish(state: AnalystState) -> dict[str, Any]:
    """Produce the message the Protocol run returns."""
    analysis = state.get("analysis") or {}
    if analysis.get("planning"):
        missing = analysis.get("missing_sources") or []
        summary = (f"规划目标 {analysis.get('goal_id')} 当前为第 {analysis.get('revision')} 版，"
                   f"已创建 {analysis.get('order_count', 0)} 个订单，记录 {analysis.get('source_count', 0)} 个来源。")
        if missing:
            summary += "以下报价来源仍需核对：" + "、".join(missing) + "。"
        return {"report": {"summary": summary, "facts": [], "warnings": missing},
                "report_markdown": f"# 规划目标异步审查\n\n{summary}\n",
                "report_name": "规划目标审查.md",
                "messages": [AIMessage(content=summary)]}
    if not analysis:
        summary = "未取得库存数据，后台分析没有产出"
        return {
            "report": {"summary": summary, "facts": [], "warnings": []},
            # Still a file. A task that finished without data is a fact the user should be able
            # to open, and an empty artifact list would read as "the report was lost".
            "report_markdown": f"# 后台补货分析\n\n{summary}。\n",
            "report_name": REPORT_NAME,
            "messages": [AIMessage(content=f"{summary}。")],
        }

    lines = analysis.get("lines") or []
    facts = [
        f"{line['part_id']} 建议补货 {line['quantity']}"
        + (f"，参考金额 {line.get('amount')}" if line.get("amount") else "")
        for line in lines
    ]
    report = {
        "summary": (
            f"共 {analysis['line_count']} 项需要补货，合计数量 {analysis['total_quantity']}，"
            f"按目录价估算 {analysis['estimated_total']} {analysis['currency']}"
        ),
        "facts": facts,
        "warnings": [],
        "artifact_ids": [],
        "next_action": "如需精确金额，请让主对话比较实时报价后生成报告",
    }
    text = report["summary"] + ("\n" + "\n".join(f"- {fact}" for fact in facts) if facts else "")
    return {
        "report": report,
        "report_markdown": _as_markdown(report, analysis),
        "report_name": REPORT_NAME,
        "messages": [AIMessage(content=text)],
    }


def _as_markdown(report: dict[str, Any], analysis: dict[str, Any]) -> str:
    """The report as a file, from the numbers this graph computed.

    Written here rather than left to the filer. The main process decides *where* a background
    report is kept; what is in it is this graph's answer, and a second rendering downstream
    would be free to disagree with the message the run also returned.
    """
    lines = analysis.get("lines") or []
    rows = [
        "| 物料 | 建议补货 | 参考单价 | 参考金额 |",
        "|---|---|---|---|",
    ]
    for line in lines:
        rows.append(
            f"| {line.get('part_id')} | {line.get('quantity')} | "
            f"{line.get('catalog_price') or '—'} | {line.get('amount') or '—'} |"
        )
    body = "\n".join(rows) if lines else "_没有需要补货的物料。_"
    return (
        "# 后台补货分析\n\n"
        f"{report['summary']}\n\n"
        "## 明细\n\n"
        f"{body}\n\n"
        "## 说明\n\n"
        "- 数量为 `目标库存 − 当前库存`，金额按目录价估算，不是实时报价。\n"
        "- 本报告由后台任务生成；需要精确金额时请让主对话比较实时报价。\n"
    )


def build() -> Any:
    """Compile the graph. Referenced from langgraph.json as ``./analyst_graph.py:build``."""
    builder = StateGraph(AnalystState)
    builder.add_node("plan", plan)
    builder.add_node("read", read_warnings)
    builder.add_node("analyse", analyse)
    builder.add_node("finish", finish)
    builder.add_edge(START, "plan")
    builder.add_edge("plan", "read")
    builder.add_edge("read", "analyse")
    builder.add_edge("analyse", "finish")
    builder.add_edge("finish", END)
    return builder.compile()
