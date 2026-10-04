"""Agent-layer tools: the ones that are not MCP business tools.

These live in the agent process rather than behind the gateway, and the sub-agent loader
still has to see them: a configuration may only be granted tools that exist in the
catalogue it is validated against. Keeping the names here — next to the tools themselves —
means the catalogue is composed the same way everywhere instead of each caller remembering
to add them.

``chart_generator`` is deliberately **one** entry standing in front of eighteen remote tools.
Registering the remote tools individually would put eighteen descriptions in the prompt on
every call, and the model would still have to know which of them takes ``category`` and which
takes ``time`` — a distinction the remote's own schema does not express.
"""

from agent.tools.chart_generator import (
    CHART_GENERATOR_DESCRIPTION,
    ChartError,
    build_chart_generator_tool,
    discover_catalogue,
    generate_chart,
)
from agent.tools.assign_skill import (
    ASSIGN_SKILL_DESCRIPTION,
    MAX_REPAIRS,
    build_assign_skill_tool,
    publish_skill,
)
from agent.tools.download_sandbox_file import (
    DOWNLOAD_SANDBOX_FILE_DESCRIPTION,
    build_download_sandbox_file_tool,
)
from agent.tools.hitl_tools import (
    REQUEST_ORDER_INFO_DESCRIPTION,
    build_request_order_info_tool,
    missing_fields,
    request_order_info,
)
from agent.tools.web_search import (
    WEB_SEARCH_DESCRIPTION,
    SearchError,
    build_web_search_tool,
    search,
)

#: Business tools that belong to a sub-agent rather than to the main agent.
HITL_TOOL_NAMES: tuple[str, ...] = ("request_order_info",)

#: Tools that produce the analyst's deliverables: the report files it writes in the sandbox and
#: the chart that goes with them. Declared by the analyst sub-agent, so the delegation rule in
#: ``build_main_agent`` takes them off the main agent — the report is one deliverable and one
#: owner, and splitting it across two agents would mean passing numbers between them as prose.
REPORT_TOOL_NAMES: tuple[str, ...] = ("chart_generator", "download_sandbox_file")

#: Tools no sub-agent declares, so the delegation rule leaves them with the main agent.
#: ``assign_skill`` (T17) belongs here on purpose: publishing and assigning a skill is the
#: main agent's own capability, and a sub-agent that could publish would be able to widen its
#: own tool set.
MAIN_AGENT_TOOL_NAMES: tuple[str, ...] = ("assign_skill", "web_search")

#: Everything this package contributes to the catalogue.
LOCAL_TOOL_NAMES: tuple[str, ...] = HITL_TOOL_NAMES + REPORT_TOOL_NAMES + MAIN_AGENT_TOOL_NAMES

__all__ = [
    "ASSIGN_SKILL_DESCRIPTION",
    "CHART_GENERATOR_DESCRIPTION",
    "DOWNLOAD_SANDBOX_FILE_DESCRIPTION",
    "HITL_TOOL_NAMES",
    "LOCAL_TOOL_NAMES",
    "MAIN_AGENT_TOOL_NAMES",
    "MAX_REPAIRS",
    "REPORT_TOOL_NAMES",
    "REQUEST_ORDER_INFO_DESCRIPTION",
    "WEB_SEARCH_DESCRIPTION",
    "ChartError",
    "SearchError",
    "build_assign_skill_tool",
    "build_chart_generator_tool",
    "build_download_sandbox_file_tool",
    "build_request_order_info_tool",
    "build_web_search_tool",
    "discover_catalogue",
    "generate_chart",
    "missing_fields",
    "publish_skill",
    "request_order_info",
    "search",
    "with_local_tools",
]


def with_local_tools(names: tuple[str, ...]) -> tuple[str, ...]:
    """An MCP tool catalogue extended with the agent-layer tools.

    Order is preserved and duplicates are removed, so a caller that already includes them
    does not end up validating against a catalogue containing the same name twice.
    """
    merged = list(names)
    for name in LOCAL_TOOL_NAMES:
        if name not in merged:
            merged.append(name)
    return tuple(merged)
