"""One chart tool in front of a remote catalogue of eighteen.

Two problems this module solves, both found by calling the live endpoint rather than reading
its documentation.

**The tool list is long and its shape varies.** The remote exposes eighteen
``generate_*_chart`` tools with different parameters. Exposing all of them would put eighteen
tool descriptions in the model's prompt on every call, and the model would still have to guess
which one takes ``category`` and which takes ``time``. So there is exactly one tool,
``chart_generator``, which takes a chart type and rows and does the mapping itself.

**The declared schema is incomplete, and incomplete in a way that fails at runtime.**
``generate_line_chart`` declares ``required: ["data"]``, but every row must carry ``time`` —
passing the ``category``/``value`` shape that works for bar and pie returns
``Input validation error: path ["data", 0, "time"] Required``. A wrapper that forwards one
data shape to every chart type therefore passes two of the three required charts and fails the
third. The families are therefore modelled explicitly, and the shaping happens here.

Nothing here falls back to drawing locally: if the remote is unreachable, that is the answer.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool

LOGGER = logging.getLogger("rush_harness.tools.chart")

#: Chart type → remote tool name. The remote does not use bare `bar`/`line`/`pie`.
CHART_TO_TOOL: dict[str, str] = {
    "bar": "generate_bar_chart",
    "line": "generate_line_chart",
    "pie": "generate_pie_chart",
    "area": "generate_area_chart",
    "scatter": "generate_scatter_chart",
    "radar": "generate_radar_chart",
    "sankey": "generate_sankey_chart",
    "funnel": "generate_funnel_chart",
    "gauge": "generate_gauge_chart",
    "treemap": "generate_treemap_chart",
    "sunburst": "generate_sunburst_chart",
    "heatmap": "generate_heatmap_chart",
    "candlestick": "generate_candlestick_chart",
    "boxplot": "generate_boxplot_chart",
    "graph": "generate_graph_chart",
    "parallel": "generate_parallel_chart",
    "tree": "generate_tree_chart",
    "echarts": "generate_echarts",
}

#: How each family expects its rows. This is the part the remote schema does not tell you.
#:
#: ``category`` rows are ``{category, value}``; ``time`` rows are ``{time, value}``. The
#: distinction is not cosmetic — mixing them is a runtime validation error, not a warning.
CATEGORY_TYPES: frozenset[str] = frozenset({"bar", "pie", "funnel", "treemap", "sunburst", "radar"})
TIME_TYPES: frozenset[str] = frozenset({"line", "area", "scatter", "heatmap", "boxplot"})

#: Types that take neither shape and need bespoke arguments, so they are refused rather than
#: guessed at. Refusing is the honest outcome; inventing a shape would fail at the remote.
BESPOKE_TYPES: frozenset[str] = frozenset({"echarts", "parallel", "graph", "tree", "sankey", "gauge", "candlestick"})

SUPPORTED_TYPES: tuple[str, ...] = tuple(sorted(CATEGORY_TYPES | TIME_TYPES))

MAX_ROWS = 200
MAX_LABEL_CHARS = 60

#: Asset formats the remote can return.
SUPPORTED_OUTPUT_TYPES: tuple[str, ...] = ("png", "svg")

#: Magic prefixes used to prove the returned asset is really an image.
MAGIC_PREFIXES: dict[bytes, str] = {
    b"\x89PNG\r\n\x1a\n": "png",
    b"<svg": "svg",
    b"<?xml": "svg",
    b"\xff\xd8\xff": "jpeg",
}


class ChartError(RuntimeError):
    """Chart generation failed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CatalogueTool:
    name: str
    required: list[str] = field(default_factory=list)
    properties: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "required": list(self.required), "properties": list(self.properties)}


@dataclass(frozen=True)
class ChartCatalogue:
    tools: list[CatalogueTool]

    @property
    def names(self) -> list[str]:
        return [tool.name for tool in self.tools]

    def as_dict(self) -> dict[str, Any]:
        return {"tool_count": len(self.tools), "tools": [tool.as_dict() for tool in self.tools]}


@dataclass(frozen=True)
class ChartAsset:
    chart_type: str
    tool: str
    mime: str
    payload: str  # base64
    size: int
    detected_format: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "chart_type": self.chart_type,
            "tool": self.tool,
            "mime": self.mime,
            "bytes": self.size,
            "format": self.detected_format,
            "base64": self.payload,
        }

    def envelope(self) -> str:
        """The tool result. The asset is carried in full; truncating it would corrupt it."""
        return json.dumps(
            {"ok": True, "data": self.as_dict(), "error": None, "request_id": None},
            ensure_ascii=False,
        )


def decode_asset(payload: str) -> tuple[bytes, str]:
    """Decode base64 and identify the format from its magic bytes.

    Identifying by content rather than trusting ``mimeType`` is what makes "资产非空可读取"
    a check rather than an assertion: a truncated or empty payload has no magic.
    """
    try:
        raw = base64.b64decode(payload, validate=False)
    except (ValueError, TypeError) as failure:
        raise ChartError("CHART_BAD_ASSET", f"返回的图片数据无法 base64 解码：{failure}") from failure
    if not raw:
        raise ChartError("CHART_BAD_ASSET", "返回的图片数据为空")
    for magic, name in MAGIC_PREFIXES.items():
        if raw.startswith(magic):
            return raw, name
    raise ChartError(
        "CHART_BAD_ASSET", f"返回的数据不是可识别的图片（前 8 字节 {raw[:8]!r}）"
    )


def build_rows(chart_type: str, rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Shape rows into what *this* chart family expects.

    Accepts the neutral ``{label, value}`` form from the caller and emits the family's own
    field names. This is the single place the category/time distinction is applied, so a
    caller cannot get it wrong by picking the wrong chart type.
    """
    if chart_type not in SUPPORTED_TYPES:
        if chart_type in BESPOKE_TYPES:
            raise ChartError(
                "CHART_TYPE_UNSUPPORTED",
                f"{chart_type!r} 需要专用参数（不是简单的 label/value），本包装器不代为构造",
            )
        raise ChartError(
            "CHART_TYPE_UNSUPPORTED",
            f"不支持的图表类型 {chart_type!r}；支持：{', '.join(SUPPORTED_TYPES)}",
        )
    if not rows:
        raise ChartError("CHART_NO_DATA", "没有数据可画")
    if len(rows) > MAX_ROWS:
        raise ChartError("CHART_TOO_MANY_ROWS", f"数据行数 {len(rows)} 超过上限 {MAX_ROWS}")

    key = "time" if chart_type in TIME_TYPES else "category"
    shaped: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        label = row.get("label", row.get(key, row.get("category", row.get("time", ""))))
        value = row.get("value")
        label = " ".join(str(label).split())[:MAX_LABEL_CHARS]
        if not label:
            raise ChartError("CHART_BAD_ROW", f"第 {index} 行缺少标签")
        if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ChartError("CHART_BAD_ROW", f"第 {index} 行的 value 必须是数字")
        shaped.append({key: label, "value": value})
    return shaped


def build_arguments(
    chart_type: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    title: str | None = None,
    output_type: str = "png",
    width: int | None = None,
    height: int | None = None,
) -> dict[str, Any]:
    if output_type not in SUPPORTED_OUTPUT_TYPES:
        raise ChartError(
            "CHART_BAD_OUTPUT", f"outputType 只能是 {', '.join(SUPPORTED_OUTPUT_TYPES)}"
        )
    arguments: dict[str, Any] = {"data": build_rows(chart_type, rows), "outputType": output_type}
    if title:
        arguments["title"] = str(title)[:120]
    if width:
        arguments["width"] = int(width)
    if height:
        arguments["height"] = int(height)
    return arguments


# --------------------------------------------------------------------------- #
# remote access
# --------------------------------------------------------------------------- #


async def discover_catalogue(url: str, token: str) -> ChartCatalogue:
    """List the remote tools and their declared schemas."""
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    if not url or not token:
        raise ChartError("CHART_NOT_CONFIGURED", "MODELSCOPE_MCP_URL 或 MODELSCOPE_API_TOKEN 未配置")

    async with sse_client(url, headers={"Authorization": f"Bearer {token}"}) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()

    tools: list[CatalogueTool] = []
    for tool in listed.tools:
        schema = tool.inputSchema or {}
        tools.append(
            CatalogueTool(
                name=tool.name,
                required=[str(item) for item in (schema.get("required") or [])],
                properties=sorted((schema.get("properties") or {}).keys()),
            )
        )
    return ChartCatalogue(tools=sorted(tools, key=lambda item: item.name))


async def call_chart_tool(
    url: str,
    token: str,
    tool_name: str,
    arguments: Mapping[str, Any],
    *,
    timeout_seconds: float = 90.0,
) -> ChartAsset:
    """Invoke one remote chart tool and return the decoded asset."""
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    async with asyncio.timeout(timeout_seconds):
        async with sse_client(url, headers={"Authorization": f"Bearer {token}"}) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, dict(arguments))

    if getattr(result, "isError", False):
        detail = ""
        for block in getattr(result, "content", None) or []:
            text = getattr(block, "text", None)
            if text:
                detail = str(text)[:300]
                break
        raise ChartError("CHART_REMOTE_ERROR", f"{tool_name} 返回错误：{detail}")

    payload = ""
    mime = ""
    for block in getattr(result, "content", None) or []:
        if getattr(block, "type", "") == "image":
            payload = str(getattr(block, "data", "") or "")
            mime = str(getattr(block, "mimeType", "") or "")
            break
    if not payload:
        # Structured results are not what this endpoint returns; say so rather than
        # returning an empty asset that looks like a chart.
        raise ChartError("CHART_BAD_ASSET", f"{tool_name} 没有返回图片内容")

    raw, detected = decode_asset(payload)
    return ChartAsset(
        chart_type="",
        tool=tool_name,
        mime=mime or f"image/{detected}",
        payload=payload,
        size=len(raw),
        detected_format=detected,
    )


def generate_chart(
    chart_type: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    url: str,
    token: str,
    title: str | None = None,
    output_type: str = "png",
) -> ChartAsset:
    """Synchronous entry point: map the type, shape the rows, call the remote."""
    tool_name = CHART_TO_TOOL.get(chart_type)
    if tool_name is None:
        raise ChartError(
            "CHART_TYPE_UNSUPPORTED",
            f"不支持的图表类型 {chart_type!r}；支持：{', '.join(SUPPORTED_TYPES)}",
        )
    arguments = build_arguments(chart_type, rows, title=title, output_type=output_type)
    asset = asyncio.run(call_chart_tool(url, token, tool_name, arguments))
    return ChartAsset(**{**asset.__dict__, "chart_type": chart_type})


CHART_GENERATOR_DESCRIPTION = (
    "生成图表图片。参数：chart_type（bar/line/pie/area/scatter/radar 等）、"
    "data（每项 {label, value}）、可选 title 与 output_type。"
    "工具会自动按图表族转换数据形状，不需要你区分 category 与 time。"
    "返回 base64 图片；远端不可用时会报错，不会改用本地绘图。"
)

CHART_ARTIFACT_NOTE = "本工具还会把图片登记为可下载产件，结果里带 artifact_id 与 download_url。"


def build_chart_generator_tool(
    *,
    url: str,
    token: str,
    on_asset: Callable[..., Mapping[str, Any] | None] | None = None,
) -> StructuredTool:
    """The single chart tool. The credential is captured here, not exposed as an argument.

    ``on_asset`` is the artifact hook, and it lives here because this is the only place the
    image bytes exist: the chart is drawn by a remote service and reaches the agent process,
    never the sandbox, so there is nothing for a "download from the sandbox" path to find.
    Without the hook the tool behaves exactly as before — the base64 comes back and no
    artifact is registered.

    The hook receives the decoded bytes, the detected mime, and the run config, so it can
    resolve the trusted owner/thread itself rather than being told them by the caller.
    """

    def _envelope(asset: ChartAsset, extra: Mapping[str, Any] | None) -> str:
        data = asset.as_dict()
        if extra:
            data.update(dict(extra))
        return json.dumps(
            {"ok": True, "data": data, "error": None, "request_id": None},
            ensure_ascii=False,
        )

    def _register(asset: ChartAsset, config: Any, title: str | None) -> Mapping[str, Any] | None:
        if on_asset is None:
            return None
        raw, detected = decode_asset(asset.payload)
        return on_asset(
            raw,
            asset.mime or f"image/{detected}",
            chart_type=asset.chart_type,
            title=title,
            config=config,
        )

    # ``config`` is annotated exactly ``RunnableConfig`` (not a union) so the framework
    # injects the run config instead of asking the model for it; see the note on
    # ``build_download_sandbox_file_tool`` for the measurement behind that.
    def call(
        chart_type: str,
        data: list[dict[str, Any]],
        title: str | None = None,
        output_type: str = "png",
        config: RunnableConfig = None,
    ) -> str:  # type: ignore[assignment]
        asset = generate_chart(
            chart_type, data, url=url, token=token, title=title, output_type=output_type
        )
        return _envelope(asset, _register(asset, config, title))

    async def acall(
        chart_type: str,
        data: list[dict[str, Any]],
        title: str | None = None,
        output_type: str = "png",
        config: RunnableConfig = None,
    ) -> str:  # type: ignore[assignment]
        # Native async path, so a graph running on an event loop does not need a nested loop.
        tool_name = CHART_TO_TOOL.get(chart_type)
        if tool_name is None:
            raise ChartError(
                "CHART_TYPE_UNSUPPORTED",
                f"不支持的图表类型 {chart_type!r}；支持：{', '.join(SUPPORTED_TYPES)}",
            )
        arguments = build_arguments(chart_type, data, title=title, output_type=output_type)
        asset = await call_chart_tool(url, token, tool_name, arguments)
        asset = ChartAsset(**{**asset.__dict__, "chart_type": chart_type})
        return _envelope(asset, _register(asset, config, title))

    description = CHART_GENERATOR_DESCRIPTION
    if on_asset is not None:
        description = f"{description}{CHART_ARTIFACT_NOTE}"

    return StructuredTool.from_function(
        func=call,
        coroutine=acall,
        name="chart_generator",
        description=description,
    )


__all__ = [
    "BESPOKE_TYPES",
    "CATEGORY_TYPES",
    "CHART_ARTIFACT_NOTE",
    "CHART_GENERATOR_DESCRIPTION",
    "CHART_TO_TOOL",
    "SUPPORTED_OUTPUT_TYPES",
    "SUPPORTED_TYPES",
    "TIME_TYPES",
    "ChartAsset",
    "ChartCatalogue",
    "ChartError",
    "CatalogueTool",
    "build_arguments",
    "build_chart_generator_tool",
    "build_rows",
    "call_chart_tool",
    "decode_asset",
    "discover_catalogue",
    "generate_chart",
]
