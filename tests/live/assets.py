"""The assets the acceptance names, verified as files.

``demo.md``'s cases produce a report and a bar chart. The acceptance additionally asks for a
real search and for bar, line and pie charts that are *valid*, so this drives the same tools
the agent holds — against the same remote services — and then opens what came back.

Checking the files rather than the answers is the point. A chart tool that returned a
convincing envelope with no image behind it would satisfy "the call succeeded" and fail here,
and a search that returned an empty list is not evidence that anything was searched.

Nothing here is asserted from inside: it returns a record and the caller writes it beside the
trials, so a round that half-worked documents which half.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

#: PNG's magic number. A chart that is not an image is not a chart.
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

#: What the artifact route serves, matching the live stack's own template.
DOWNLOAD_URL_TEMPLATE = "/api/artifacts/{artifact_id}/content"

#: One case per chart family the acceptance names. The data is small on purpose: this checks
#: the pipeline end to end, not the renderer's taste.
CHART_CASES: tuple[tuple[str, str, list[dict[str, Any]]], ...] = (
    (
        "bar",
        "各物料建议补货数量",
        [
            {"label": "P001", "value": 42},
            {"label": "P003", "value": 15},
            {"label": "P004", "value": 30},
        ],
    ),
    (
        "line",
        "近四次盘点 P001 库存",
        [
            {"label": "第1次", "value": 30},
            {"label": "第2次", "value": 24},
            {"label": "第3次", "value": 15},
            {"label": "第4次", "value": 8},
        ],
    ),
    (
        "pie",
        "补货金额占比",
        [
            {"label": "P001", "value": 1008.0},
            {"label": "P003", "value": 1020.0},
            {"label": "P004", "value": 525.0},
        ],
    ),
)

#: What is searched. Public information the tool is meant for, not the ERP's own data — the
#: ERP is reached through the gateway, and asking a search engine about it would be the wrong
#: tool answering a question it cannot answer.
#:
#: The wording is load-bearing. The standard engine returns ``link`` for most items but not
#: all, and how many varies by query: over six realistic queries it gave citable results for
#: five and none at all for the sixth. A check whose query is that sixth would report "the
#: search found nothing" about a working tool, so this is a query that was measured to return
#: links rather than the first phrase that came to mind.
SEARCH_QUERY = "钢材 采购 成本控制"


def verify(
    *,
    stack: Any,  # noqa: ANN401 - a LiveStack; typed loosely to keep this import-free of it
    owner: str,
    thread_id: str,
    env: Mapping[str, str],
) -> dict[str, Any]:
    """Search once and render one chart per family, then check what was produced."""
    record: dict[str, Any] = {"charts": [], "search": {}, "problems": []}

    chart_url = (env.get("MODELSCOPE_MCP_URL") or "").strip()
    chart_token = (env.get("MODELSCOPE_API_TOKEN") or "").strip()
    if not chart_url or not chart_token:
        record["problems"].append(
            "没有配置图表远端（MODELSCOPE_MCP_URL / MODELSCOPE_API_TOKEN）；"
            "live 轮次不能在远端不可用时改用本地绘图"
        )
    else:
        from agent.tools import build_chart_generator_tool

        tool = build_chart_generator_tool(
            url=chart_url,
            token=chart_token,
            # The same hook the agent's tool is built with, so this exercises the path that
            # files a chart rather than a parallel one that would have to be kept in step.
            on_asset=stack.artifacts.chart_hook(download_url_template=DOWNLOAD_URL_TEMPLATE),
        )
        for chart_type, title, data in CHART_CASES:
            entry = _one_chart(
                tool,
                stack,
                owner=owner,
                thread_id=thread_id,
                chart_type=chart_type,
                title=title,
                data=data,
            )
            record["charts"].append(entry)
            if not entry["ok"]:
                record["problems"].append(f"{chart_type} 图表：{entry['reason']}")

    record["search"] = _one_search((env.get("ZHIPU_API_KEY") or "").strip())
    if not record["search"].get("ok"):
        record["problems"].append(f"搜索：{record['search'].get('reason')}")

    record["ok"] = not record["problems"]
    return record


def _one_chart(
    tool: Any,  # noqa: ANN401 - a StructuredTool
    stack: Any,  # noqa: ANN401 - a LiveStack
    *,
    owner: str,
    thread_id: str,
    chart_type: str,
    title: str,
    data: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    entry: dict[str, Any] = {"chart_type": chart_type, "ok": False, "reason": ""}
    try:
        raw = tool.invoke(
            {"chart_type": chart_type, "data": list(data), "title": title},
            # The config is what the artifact hook reads the owner and thread from; without it
            # the hook declines and this would be testing the tool with no filing at all.
            config={"configurable": {"owner_user_id": owner, "thread_id": thread_id}},
        )
    except Exception as failure:  # noqa: BLE001 - a failed chart is a result here
        entry["reason"] = f"{type(failure).__name__}: {failure}"[:200]
        return entry

    document = json.loads(raw) if isinstance(raw, str) else dict(raw)
    if not document.get("ok"):
        entry["reason"] = str((document.get("error") or {}).get("message") or document)[:200]
        return entry

    payload = document.get("data") or {}
    artifact_id = str(payload.get("artifact_id") or "")
    if not artifact_id:
        entry["reason"] = "工具没有登记产件；图表的字节只在这里存在，没登记就等于用户拿不到它"
        return entry

    opened = stack.artifacts.open(owner, artifact_id)
    if opened is None:
        entry["reason"] = f"产件 {artifact_id} 的字节不在了（只有元数据）"
        entry["artifact_id"] = artifact_id
        return entry

    meta, content = opened
    digest_ok = hashlib.sha256(content).hexdigest() == meta.sha256
    is_png = content.startswith(PNG_MAGIC)
    entry.update(
        {
            "artifact_id": artifact_id,
            "name": meta.name,
            "size": len(content),
            "sha256": meta.sha256,
            "digest_matches": digest_ok,
            "is_png": is_png,
            "ok": is_png and digest_ok and len(content) > 0,
        }
    )
    if not entry["ok"]:
        entry["reason"] = "产件不是非空 PNG，或摘要与记录对不上"
    return entry


def _one_search(api_key: str) -> dict[str, Any]:
    if not api_key:
        return {"ok": False, "reason": "没有配置搜索凭据（ZHIPU_API_KEY）"}

    from agent.tools.web_search import search as run_search

    try:
        response = run_search(SEARCH_QUERY, api_key=api_key, count=3)
    except Exception as failure:  # noqa: BLE001 - reported, not raised
        return {"ok": False, "reason": f"{type(failure).__name__}: {failure}"[:200]}

    results = list(getattr(response, "results", ()) or ())
    return {
        "ok": bool(results),
        "query": SEARCH_QUERY,
        # Which tier actually served this. ``dependencies.md`` forbids swapping the engine
        # silently, and the way a reader checks that is by finding the engine in the evidence —
        # an unstated engine is indistinguishable from one that was changed in the code.
        "engine": str(getattr(response, "engine", "")),
        "returned": len(results),
        # Links, not snippets: the contract requires a citation to carry its link, so a result
        # with no link would not be usable in an answer even if its text read well.
        "samples": [
            {"title": str(getattr(item, "title", "")), "link": str(getattr(item, "link", ""))}
            for item in results[:3]
        ],
        "reason": "" if results else "搜索返回了 0 条结果",
    }


__all__ = ["CHART_CASES", "PNG_MAGIC", "SEARCH_QUERY", "verify"]
