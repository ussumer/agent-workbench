"""Real web search, with failures that stay failures.

The one property this module exists to guarantee: **an authentication, permission or quota
failure must not become an empty result list.** An empty list looks like "the search found
nothing", which the model will then narrate as a finding. The acceptance criteria name this
explicitly, so every non-success path raises.

Two things learned from calling the live API rather than from its documentation:

* **`count` is not honoured.** Asking for 2 returned 50. The limit is therefore applied here,
  on our side, or the model would receive fifty results per call and the context would fill
  with them.
* **Failure codes carry the fix.** ``1113`` means the account has no balance or no search
  resource package; ``401`` means the key is wrong. Turning both into "no results" throws away
  the only information that distinguishes them.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
from langchain_core.tools import StructuredTool

LOGGER = logging.getLogger("rush_harness.tools.web_search")

#: The search endpoint. Confirmed by probing the live API; the guide page documents the
#: parameters but not the path.
SEARCH_URL = "https://open.bigmodel.cn/api/paas/v4/web_search"

#: The engine this project runs on. ``search_std`` is Zhipu's standard tier: 0.01 元/call
#: against ``search_pro_sogou``'s 0.05, which is what ``contracts/external.md`` originally
#: named. Both documents were updated in the same change, because ``dependencies.md`` forbids
#: swapping the engine silently and an engine that differs from the one written down is
#: exactly that.
#:
#: The trade-off is measured, not assumed. Every result is worth nothing without a ``link``
#: (see ``_parse``), and the standard tier omits it more often: over six realistic queries it
#: returned citable results for five and none at all for the sixth, where the Sogou tier
#: returned links for all six. So this is a real 80% saving with a real failure mode — one
#: query in six comes back empty — and the failure is *loud*: an empty search result is
#: reported as empty rather than as an answer. Two cheaper tiers exist and neither is usable
#: here: ``search_std`` is this one, and ``search_pro`` at 0.03 元 behaved identically on every
#: query tried. ``search_pro_quark`` returns links and costs the same as Sogou, so switching to
#: it would buy nothing.
DEFAULT_ENGINE = "search_std"

SUPPORTED_ENGINES: tuple[str, ...] = (
    "search_std",
    "search_pro",
    "search_pro_sogou",
    "search_pro_quark",
)

#: Bounds we enforce ourselves. The API's ``count`` cannot be relied on to hold them: the
#: Sogou tier ignores it outright (asked for 2, got 50) while the standard tier honours it
#: exactly. That disagreement is the reason rather than a footnote — the same code would then
#: behave differently on a different tier, so the limit must not be left to the engine.
MAX_QUERY_CHARS = 200
MAX_COUNT = 20
DEFAULT_COUNT = 8

#: Retries apply to transport problems only. A 401 does not become a 200 by asking again.
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 0.4

#: Response body fields that must survive into a citation.
CITATION_FIELDS: tuple[str, ...] = ("title", "link", "content", "publish_date", "media")


class SearchError(RuntimeError):
    """Search failed. Never raised as an empty result."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class SearchResult:
    title: str
    link: str
    snippet: str
    source: str
    publish_date: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "link": self.link,
            "snippet": self.snippet,
            "source": self.source,
            "publish_date": self.publish_date,
        }


@dataclass(frozen=True)
class SearchResponse:
    query: str
    engine: str
    results: list[SearchResult] = field(default_factory=list)
    intent: str | None = None
    #: How many the API actually returned, before our own truncation.
    returned_by_api: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "engine": self.engine,
            "intent": self.intent,
            "returned_by_api": self.returned_by_api,
            "results": [result.as_dict() for result in self.results],
        }


def normalise_query(query: str) -> str:
    cleaned = " ".join((query or "").split())
    if not cleaned:
        raise SearchError("INVALID_ARGUMENT", "搜索词不能为空", retryable=False)
    if len(cleaned) > MAX_QUERY_CHARS:
        raise SearchError(
            "INVALID_ARGUMENT", f"搜索词超过 {MAX_QUERY_CHARS} 字符，请缩短后再试", retryable=False
        )
    return cleaned


def clamp_count(count: int | None) -> int:
    if count is None:
        return DEFAULT_COUNT
    return max(1, min(MAX_COUNT, int(count)))


def describe_error(status: int, body: Any) -> SearchError:
    """Map a failed response onto an error that says what to do about it."""
    code = ""
    message = ""
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            code = str(error.get("code", ""))
            message = str(error.get("message", ""))

    if status == 401 or code in {"1000", "1001"}:
        return SearchError(
            "SEARCH_UNAUTHORIZED",
            f"智谱搜索鉴权失败（{code or status}）：{message or 'key 无效或已停用'}",
            retryable=False,
        )
    if code == "1113" or status == 429:
        return SearchError(
            "SEARCH_QUOTA_EXCEEDED",
            f"智谱搜索额度不足（{code or status}）：{message or '余额不足或无可用资源包'}。"
            "搜索是独立计费的接口，需要充值或购买资源包。",
            retryable=False,
        )
    if status == 403:
        return SearchError(
            "SEARCH_PERMISSION_DENIED",
            f"智谱搜索无权限（{status}）：{message or '未开通网络搜索'}",
            retryable=False,
        )
    if status >= 500:
        return SearchError("SEARCH_UPSTREAM_ERROR", f"智谱服务异常（{status}）：{message}", retryable=True)
    return SearchError(
        "SEARCH_FAILED", f"搜索失败（{status}）：{message or '未知错误'}", retryable=status >= 500
    )


def search(
    query: str,
    *,
    api_key: str,
    count: int | None = None,
    engine: str = DEFAULT_ENGINE,
    timeout: float = 30.0,
    transport: httpx.BaseTransport | None = None,
) -> SearchResponse:
    """Run one search. Raises :class:`SearchError` on every non-success path."""
    if not api_key:
        raise SearchError(
            "SEARCH_UNAUTHORIZED", "ZHIPU_API_KEY 未配置，无法搜索", retryable=False
        )
    if engine not in SUPPORTED_ENGINES:
        raise SearchError(
            "INVALID_ARGUMENT",
            f"不支持的搜索引擎 {engine!r}；支持：{', '.join(SUPPORTED_ENGINES)}",
            retryable=False,
        )

    cleaned = normalise_query(query)
    wanted = clamp_count(count)
    payload = {
        "search_engine": engine,
        "search_query": cleaned,
        # Sent, but not trusted: the API ignores it, so the real limit is applied below.
        "count": wanted,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    last: SearchError | None = None
    with httpx.Client(timeout=timeout, trust_env=False, transport=transport) as client:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = client.post(SEARCH_URL, headers=headers, json=payload)
            except httpx.TransportError as failure:
                last = SearchError(
                    "SEARCH_UNREACHABLE", f"无法连接智谱搜索：{failure}", retryable=True
                )
                LOGGER.warning("search attempt %d/%d unreachable: %s", attempt, MAX_ATTEMPTS, failure)
            else:
                if response.status_code == 200:
                    return _parse(cleaned, engine, response.json(), wanted)
                error = describe_error(response.status_code, _maybe_json(response))
                LOGGER.warning("search attempt %d/%d failed: %s", attempt, MAX_ATTEMPTS, error.code)
                if not error.retryable:
                    raise error
                last = error

            if attempt < MAX_ATTEMPTS:
                time.sleep(BACKOFF_SECONDS * (2 ** (attempt - 1)))

    raise last or SearchError("SEARCH_FAILED", "搜索失败且无可用错误信息", retryable=True)


def _maybe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {"error": {"message": response.text[:200]}}


def _parse(query: str, engine: str, body: Any, wanted: int) -> SearchResponse:
    if not isinstance(body, dict):
        raise SearchError("SEARCH_BAD_RESPONSE", "搜索返回的不是 JSON 对象", retryable=False)

    raw = body.get("search_result")
    if raw is None:
        # A 200 without results is legitimate ("nothing found"), but a 200 without the
        # *field* is a shape change and must not be read as "nothing found".
        raise SearchError(
            "SEARCH_BAD_RESPONSE", "搜索响应缺少 search_result 字段，可能接口已变更", retryable=False
        )
    if not isinstance(raw, list):
        raise SearchError("SEARCH_BAD_RESPONSE", "search_result 不是数组", retryable=False)

    results: list[SearchResult] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        link = str(item.get("link") or "").strip()
        if not link:
            # A result with no link cannot be cited, which is the whole point of searching.
            continue
        results.append(
            SearchResult(
                title=str(item.get("title") or "").strip(),
                link=link,
                snippet=str(item.get("content") or "").strip(),
                source=str(item.get("media") or "").strip(),
                publish_date=(str(item["publish_date"]) if item.get("publish_date") else None),
            )
        )

    intent = None
    raw_intent = body.get("search_intent")
    if isinstance(raw_intent, dict):
        intent = str(raw_intent.get("intent") or "") or None

    return SearchResponse(
        query=query,
        engine=engine,
        results=results[:wanted],
        intent=intent,
        returned_by_api=len(raw),
    )


WEB_SEARCH_DESCRIPTION = (
    "在公网上搜索资料。用于查询供应商公开信息、行业价格参考等网页数据。"
    "返回带 title/link/snippet/publish_date 的结果，**引用时必须带上 link**。"
    "搜索失败会抛错而不是返回空列表；额度或鉴权问题需要用户处理，不要重试。"
)


def build_web_search_tool(*, api_key: str, engine: str = DEFAULT_ENGINE) -> StructuredTool:
    """The search tool, shaped for the framework.

    The key is captured at build time from configuration rather than accepted as a tool
    argument: the model must not be able to name a credential.
    """

    def call(query: str, count: int = DEFAULT_COUNT) -> str:
        import json

        response = search(query, api_key=api_key, count=count, engine=engine)
        return json.dumps(
            {"ok": True, "data": response.as_dict(), "error": None, "request_id": None},
            ensure_ascii=False,
        )

    return StructuredTool.from_function(
        func=call, name="web_search", description=WEB_SEARCH_DESCRIPTION
    )


def summarise_for_prompt(results: Sequence[SearchResult], *, limit: int = 5) -> str:
    """A compact, citable rendering for the prompt."""
    lines = []
    for index, result in enumerate(results[:limit], start=1):
        when = f"（{result.publish_date}）" if result.publish_date else ""
        lines.append(f"{index}. {result.title}{when}\n   {result.link}\n   {result.snippet[:120]}")
    return "\n".join(lines) if lines else "（没有检索到结果）"


__all__ = [
    "CITATION_FIELDS",
    "DEFAULT_COUNT",
    "DEFAULT_ENGINE",
    "MAX_COUNT",
    "MAX_QUERY_CHARS",
    "SEARCH_URL",
    "SUPPORTED_ENGINES",
    "SearchError",
    "SearchResponse",
    "SearchResult",
    "WEB_SEARCH_DESCRIPTION",
    "build_web_search_tool",
    "clamp_count",
    "describe_error",
    "normalise_query",
    "search",
    "summarise_for_prompt",
]
