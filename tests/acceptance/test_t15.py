"""T15 acceptance: real search and the single chart entry point.

Live mode: both external services are called for real. Nothing here is skipped when a
credential is missing — the gate blocks the task instead, because a skipped live test reads as
a pass in the report.

What is asserted, and why each one is worth the network call:

* **Search returns results with links.** "可引用" means a `link` exists; a result without one
  cannot be cited, so those are dropped and the count reflects it.
* **A failure stays a failure.** A 401 or a quota error must raise. An empty list would let the
  model narrate "nothing found" as a finding, which is the specific conflation the acceptance
  criteria forbid.
* **The chart wrapper shapes data per family.** The remote's schema does not say that line
  charts need `time`, so this is the one place that knowledge lives — and the test proves the
  difference is real by calling the endpoint both ways.
* **The three required charts really render.** The asset is decoded and identified by its magic
  bytes, so "非空可读取" is checked rather than asserted.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
for _path in (str(SRC_DIR), str(REPO_ROOT / "tests")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from agent.env_utils import load_env  # noqa: E402
from agent.tools.chart_generator import (  # noqa: E402
    BESPOKE_TYPES,
    CATEGORY_TYPES,
    CHART_TO_TOOL,
    SUPPORTED_TYPES,
    TIME_TYPES,
    ChartError,
    build_arguments,
    build_rows,
    call_chart_tool,
    decode_asset,
    discover_catalogue,
    generate_chart,
)
from agent.tools.web_search import (  # noqa: E402
    MAX_COUNT,
    MAX_QUERY_CHARS,
    SearchError,
    clamp_count,
    describe_error,
    normalise_query,
    search,
)

pytestmark = pytest.mark.live

REQUIRED_CHARTS = ("bar", "line", "pie")

#: Chart evidence is written here so the receipt has something to point at.
EVIDENCE_DIR = REPO_ROOT / "artifacts" / "tasks" / "T15"


@pytest.fixture(scope="module")
def env() -> dict[str, str]:
    resolved = load_env()
    # Not skipped on absence: the gate decides whether this task may run at all.
    assert resolved.get("ZHIPU_API_KEY"), "ZHIPU_API_KEY 未配置；gate 本应阻塞本任务"
    assert resolved.get("MODELSCOPE_MCP_URL"), "MODELSCOPE_MCP_URL 未配置"
    assert resolved.get("MODELSCOPE_API_TOKEN"), "MODELSCOPE_API_TOKEN 未配置"
    return resolved


ROWS = [
    {"label": "刹车片", "value": 8},
    {"label": "传动链条", "value": 5},
    {"label": "火花塞", "value": 0},
]


# --------------------------------------------------------------------------- #
# search
# --------------------------------------------------------------------------- #


def test_search_returns_citable_results_with_links(env):
    response = search("刹车片 供应商 汽车配件", api_key=env["ZHIPU_API_KEY"], count=5)

    assert response.results, "真实搜索应当返回结果"
    assert len(response.results) <= 5
    for result in response.results:
        assert result.link.startswith("http"), f"结果缺少可引用链接：{result.title!r}"
        assert result.title


def test_the_api_ignores_count_so_we_enforce_it_ourselves(env):
    """Measured on the live API: asking for 2 returned 50. The cap has to be ours."""
    response = search("汽车配件", api_key=env["ZHIPU_API_KEY"], count=2)

    assert len(response.results) <= 2
    assert response.returned_by_api >= len(response.results)


def test_a_bad_key_raises_instead_of_returning_no_results(env):
    """The conflation the acceptance criteria name explicitly."""
    with pytest.raises(SearchError) as failure:
        search("汽车配件", api_key="definitely-not-a-valid-key")

    assert failure.value.code == "SEARCH_UNAUTHORIZED"
    assert failure.value.retryable is False


def test_an_unauthorised_response_is_not_retried():
    """A 401 does not become a 200 by asking again, and retrying wastes the quota."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401, json={"error": {"code": "1000", "message": "认证失败"}})

    with pytest.raises(SearchError):
        search("x", api_key="k", transport=httpx.MockTransport(handler))

    assert len(calls) == 1, "鉴权失败不应重试"


def test_a_transient_failure_is_retried_then_succeeds():
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(503, json={"error": {"code": "500", "message": "临时故障"}})
        return httpx.Response(
            200,
            json={"search_result": [{"title": "t", "link": "https://example.com/a", "content": "c"}]},
        )

    response = search("x", api_key="k", transport=httpx.MockTransport(handler))

    assert len(attempts) == 2
    assert response.results[0].link == "https://example.com/a"


def test_a_success_without_a_result_field_is_a_shape_change_not_an_empty_search():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    with pytest.raises(SearchError) as failure:
        search("x", api_key="k", transport=httpx.MockTransport(handler))

    assert failure.value.code == "SEARCH_BAD_RESPONSE"


def test_a_genuinely_empty_search_is_reported_as_empty():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"search_result": []})

    response = search("x", api_key="k", transport=httpx.MockTransport(handler))

    assert response.results == []


def test_quota_and_permission_errors_keep_their_meaning():
    quota = describe_error(429, {"error": {"code": "1113", "message": "余额不足或无可用资源包"}})
    permission = describe_error(403, {"error": {"code": "1210", "message": "无权限"}})

    assert quota.code == "SEARCH_QUOTA_EXCEEDED"
    assert "充值" in str(quota) or "资源包" in str(quota)
    assert quota.retryable is False
    assert permission.code == "SEARCH_PERMISSION_DENIED"
    assert permission.retryable is False


def test_query_and_count_are_bounded_before_any_call():
    with pytest.raises(SearchError):
        normalise_query("   ")
    with pytest.raises(SearchError):
        normalise_query("字" * (MAX_QUERY_CHARS + 1))

    assert clamp_count(None) == 8
    assert clamp_count(0) == 1
    assert clamp_count(9999) == MAX_COUNT


# --------------------------------------------------------------------------- #
# the chart catalogue
# --------------------------------------------------------------------------- #


def test_the_remote_catalogue_is_discovered_and_has_the_required_types(env):
    catalogue = asyncio.run(discover_catalogue(env["MODELSCOPE_MCP_URL"], env["MODELSCOPE_API_TOKEN"]))

    names = catalogue.names
    assert len(names) >= 15, f"目录过小，可能端点变了：{names}"
    for chart_type in REQUIRED_CHARTS:
        assert CHART_TO_TOOL[chart_type] in names, f"缺少 {chart_type} 对应的远端工具"

    # Every mapped type must exist remotely, or the mapping is fiction.
    unknown = sorted(set(CHART_TO_TOOL.values()) - set(names))
    assert unknown == [], f"映射里有远端不存在的工具：{unknown}"

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    (EVIDENCE_DIR / "chart-catalog.json").write_text(
        json.dumps(catalogue.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def test_the_mapping_and_the_families_cover_the_supported_types():
    assert set(SUPPORTED_TYPES) == set(CATEGORY_TYPES | TIME_TYPES)
    assert set(CHART_TO_TOOL) >= set(SUPPORTED_TYPES)
    assert not (CATEGORY_TYPES & TIME_TYPES)


def test_rows_are_shaped_per_family():
    """The distinction the remote schema does not express."""
    bar = build_rows("bar", ROWS)
    line = build_rows("line", ROWS)

    assert all("category" in row and "time" not in row for row in bar)
    assert all("time" in row and "category" not in row for row in line)
    assert [row["value"] for row in bar] == [8, 5, 0]


def test_passing_category_rows_to_a_line_chart_is_caught_before_the_call():
    """A naive forwarder would send them and fail at the remote; here it fails locally."""
    arguments = build_arguments("line", ROWS)
    assert all("time" in row for row in arguments["data"])
    # And the shape the naive forwarder would have used is genuinely wrong:
    naive = {"data": [{"category": row["label"], "value": row["value"]} for row in ROWS]}
    assert any("time" not in row for row in naive["data"])


def test_an_unsupported_type_is_refused_with_the_supported_list():
    with pytest.raises(ChartError) as failure:
        build_rows("donut", ROWS)

    assert failure.value.code == "CHART_TYPE_UNSUPPORTED"
    assert "bar" in str(failure.value)


def test_a_type_needing_bespoke_arguments_is_refused_rather_than_guessed():
    for chart_type in sorted(BESPOKE_TYPES):
        with pytest.raises(ChartError) as failure:
            build_rows(chart_type, ROWS)
        assert failure.value.code == "CHART_TYPE_UNSUPPORTED"


def test_bad_rows_are_refused():
    with pytest.raises(ChartError):
        build_rows("bar", [])
    with pytest.raises(ChartError):
        build_rows("bar", [{"label": "x", "value": "not-a-number"}])
    with pytest.raises(ChartError):
        build_rows("bar", [{"label": "", "value": 1}])


def test_an_empty_or_undecodable_asset_is_rejected():
    with pytest.raises(ChartError) as failure:
        decode_asset("")

    assert failure.value.code == "CHART_BAD_ASSET"

    with pytest.raises(ChartError):
        decode_asset("bm90IGFuIGltYWdl")  # "not an image"


# --------------------------------------------------------------------------- #
# the three real charts
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("chart_type", REQUIRED_CHARTS)
def test_a_real_chart_is_rendered_and_the_asset_is_readable(chart_type, env):
    """Base64 decodes and the magic bytes say PNG — that is what "可读取" means here."""
    asset = generate_chart(
        chart_type,
        ROWS,
        url=env["MODELSCOPE_MCP_URL"],
        token=env["MODELSCOPE_API_TOKEN"],
        title=f"库存 {chart_type}",
    )

    assert asset.tool == CHART_TO_TOOL[chart_type]
    assert asset.chart_type == chart_type
    assert asset.size > 1000, f"{chart_type} 返回的资产过小，可能是空图"
    raw, detected = decode_asset(asset.payload)
    assert detected == "png"
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE_DIR / f"chart-{chart_type}.{detected}"
    path.write_bytes(raw)
    assert path.read_bytes()[:4] == b"\x89PNG"


def test_the_wrapper_exposes_one_tool_not_eighteen(env):
    """`包装器不暴露 26 个冗长工具到主 prompt` — here it is the tool list itself."""
    from agent.tools import LOCAL_TOOL_NAMES, build_chart_generator_tool

    tool = build_chart_generator_tool(
        url=env["MODELSCOPE_MCP_URL"], token=env["MODELSCOPE_API_TOKEN"]
    )

    assert tool.name == "chart_generator"
    assert "chart_generator" in LOCAL_TOOL_NAMES
    # The credential is not a parameter the model can see or supply.
    properties = set((tool.args_schema.model_json_schema().get("properties") or {}))
    assert properties == {"chart_type", "data", "title", "output_type"}


def test_the_single_tool_renders_every_required_chart_end_to_end(env):
    from agent.tools import build_chart_generator_tool

    tool = build_chart_generator_tool(
        url=env["MODELSCOPE_MCP_URL"], token=env["MODELSCOPE_API_TOKEN"]
    )

    for chart_type in REQUIRED_CHARTS:
        payload = json.loads(tool.invoke({"chart_type": chart_type, "data": ROWS}))
        assert payload["ok"] is True, chart_type
        assert payload["data"]["tool"] == CHART_TO_TOOL[chart_type]
        assert decode_asset(payload["data"]["base64"])[1] == "png"


def test_a_remote_error_is_surfaced_not_masked(env):
    """A missing required field at the remote must arrive as an error, not an empty chart."""
    with pytest.raises(ChartError) as failure:
        asyncio.run(
            call_chart_tool(
                env["MODELSCOPE_MCP_URL"],
                env["MODELSCOPE_API_TOKEN"],
                "generate_line_chart",
                # The shape a naive forwarder would send: no `time`.
                {"data": [{"category": "x", "value": 1}], "outputType": "png"},
            )
        )

    assert failure.value.code == "CHART_REMOTE_ERROR"
    assert "time" in str(failure.value), "远端错误里应当能看到缺的是 time"


def test_no_local_drawing_fallback_exists():
    """`缺凭据标 blocked，不能自动改用本地绘图` — asserted against the module, not promised."""
    source = (SRC_DIR / "agent" / "tools" / "chart_generator.py").read_text(encoding="utf-8")

    for forbidden in ("matplotlib", "plotly", "pyecharts", "PIL", "ImageDraw"):
        assert forbidden not in source, f"不允许本地绘图兜底：{forbidden}"
