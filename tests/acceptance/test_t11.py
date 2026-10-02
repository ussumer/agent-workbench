"""T11 acceptance: main agent, YAML sub-agents and planning.

What is really being asserted here:

* **Authority is decided at load time.** A sub-agent's tool set comes from YAML patterns
  that must resolve to a reviewed snapshot. A pattern matching nothing, or matching
  something the catalogue grew afterwards, aborts startup — and the read-only scope is
  checked a second time, independently of the snapshot, so "the analyst cannot write"
  does not rest on a value someone edited.
* **The sub-agent boundary is real.** The analyst is handed exactly the six read tools,
  and the only two write tools are reachable inside the order sub-agent, through a guard
  that refuses without an approval.
* **Delegation is context-isolated.** A sub-agent's message list is its own task plus the
  preferences, not the parent transcript.
* **The data is real.** The read-only query test runs against a live Java ERP through the
  live MCP gateway, and the numbers are compared with a direct gateway call. A scripted
  model chooses *which* tool to call; it cannot choose what the tool returns.

The scripted model is the declared model double for integration mode. It is used only to
steer control flow; every datum in these tests comes from a real service.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
for _path in (str(SRC_DIR), str(REPO_ROOT / "tests")):
    if _path not in sys.path:  # noqa: SIM102 - order matters, keep both insertions explicit
        sys.path.insert(0, _path)

from fixtures import erp_service, loader, mcp_service  # noqa: E402

from agent.main_agent import build_main_agent, guard_write_tools  # noqa: E402
from agent.memory.prompts import (  # noqa: E402
    build_main_prompt,
    build_subagent_context,
    effective_preferences,
    render_preferences,
)
from agent.middleware_config import (  # noqa: E402
    build_middlewares,
    declared_hooks,
    middleware_inventory,
)
from agent.tools import LOCAL_TOOL_NAMES, with_local_tools  # noqa: E402
from agent.schema import (  # noqa: E402
    SHARED_READ_ROOTS,
    SHARED_WRITE_ROOTS,
    SubAgentConfig,
    SubAgentReturn,
    SubAgentReturnError,
    is_within,
)
from agent.subagents.loader import (  # noqa: E402
    SubAgentLoaderError,
    build_config,
    load_all,
)
from mcp_server.tools.registry import (  # noqa: E402
    ANALYST_TOOL_SET,
    ERP_READ_TOOLS,
    ERP_TOOLS,
    ERP_WRITE_TOOLS,
    INVENTORY_WARNING,
    ORDER_TOOL_SET,
)

pytestmark = pytest.mark.integration

CONFIGS_DIR = SRC_DIR / "agent" / "subagents" / "configs"
ANALYST = "procurement-analyst"
ORDER = "procurement-order"
ACTOR_HEADER = "x-actor-id"

#: The catalogue a configuration is validated against: the MCP business tools plus the
#: agent-layer tools. Composed through the shared helper so this test cannot drift from the
#: application's own view of what exists (T12 added `request_order_info`).
CATALOGUE = with_local_tools(ERP_TOOLS)


# --------------------------------------------------------------------------- #
# the declared model double
# --------------------------------------------------------------------------- #


class ScriptedChatModel(BaseChatModel):
    """Replays a fixed script of ``AIMessage``s, recording the context sizes it saw.

    ``bind_tools`` returns ``self`` because the script already names the tools it wants;
    there is no tool selection to perform here, and pretending otherwise would add
    behaviour the tests do not rely on.
    """

    script: list[AIMessage]
    contexts: list[list[BaseMessage]] = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedChatModel:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.contexts.append(list(messages))
        if not self.script:
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="完成"))])
        return ChatResult(generations=[ChatGeneration(message=self.script.pop(0))])

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {}

    def context_sizes(self) -> list[int]:
        return [len(context) for context in self.contexts]


def tool_call(name: str, args: dict[str, Any], call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )


def tool_text(message: ToolMessage) -> str:
    """Flatten a tool message's content to text.

    MCP tools return structured content blocks rather than a plain string, so the payload
    has to be reassembled before it can be parsed.
    """
    content = message.content
    if isinstance(content, str):
        return content
    blocks = content if isinstance(content, list) else [content]
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, dict):
            text = block.get("text")
            parts.append(text if isinstance(text, str) else json.dumps(block, ensure_ascii=False))
        else:
            parts.append(str(block))
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    """A live ERP plus a live gateway wired to it, for the real-data tests."""
    erp_dir = tmp_path_factory.mktemp("t11-erp")
    gateway_dir = tmp_path_factory.mktemp("t11-gateway")
    with erp_service.running_erp(erp_dir, seed_path=loader.SEED_PATH) as erp:
        with mcp_service.running_gateway(
            gateway_dir,
            erp_base_url=erp.base_url,
            erp_token=erp.token,
            grant_secret=mcp_service.DEFAULT_GRANT_SECRET,
        ) as gateway:
            yield erp, gateway


@pytest.fixture(scope="module")
def gateway(stack) -> mcp_service.McpGateway:
    return stack[1]


@contextlib.asynccontextmanager
async def live_tools(gateway: mcp_service.McpGateway, actor: str = "demo-a") -> AsyncIterator[list]:
    """The gateway's tools as LangChain tools, carrying a fixed caller identity.

    Headers ride on a dedicated client so two actors cannot inherit each other's identity.
    """
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient(
        {
            "erp": {
                "url": gateway.mcp_url,
                "transport": "streamable_http",
                "headers": {ACTOR_HEADER: actor},
            }
        }
    )
    yield await client.get_tools()


async def mcp_payload(gateway: mcp_service.McpGateway, name: str, arguments: dict) -> dict:
    """Call one gateway tool directly, for an independent comparison."""
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with httpx.AsyncClient(
        headers={ACTOR_HEADER: "demo-a"}, timeout=httpx.Timeout(30.0, connect=5.0), trust_env=False
    ) as http:
        async with streamable_http_client(gateway.mcp_url, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments)
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and structured:
        return structured.get("result", structured)
    for item in result.content:
        text = getattr(item, "text", None)
        if text:
            return json.loads(text)
    raise AssertionError(f"tool returned no usable payload: {result}")


@pytest.fixture(scope="module")
def configs() -> dict[str, SubAgentConfig]:
    """The real, validated YAML configurations."""
    return load_all(available_tools=CATALOGUE, write_tools=ERP_WRITE_TOOLS)


def document(**overrides: Any) -> dict[str, Any]:
    """A valid order-agent document, for mutation in the negative tests."""
    base: dict[str, Any] = {
        "name": "probe-agent",
        "description": "用于测试的配置",
        "scope": "procurement-order",
        "tool_patterns": ["part_*"],
        "expected_tools": sorted({"part_query", "part_search", "part_by_supplier"}),
        "system_prompt": "只做查询。",
    }
    base.update(overrides)
    return base


def build(document_: dict[str, Any]) -> SubAgentConfig:
    return build_config(
        document_, available_tools=CATALOGUE, write_tools=ERP_WRITE_TOOLS, source="test"
    )


# --------------------------------------------------------------------------- #
# YAML configuration and authority
# --------------------------------------------------------------------------- #


def test_yaml_configs_match_the_contract_roles(configs):
    """The shipped configurations describe exactly the two roles the architecture names.

    Both agents now also carry agent-layer tools — the order agent the supplement tool (T12),
    the analyst the two report tools (T16) — so each is asserted in two parts. Splitting the
    ERP surface out is what keeps this test measuring the *contract* rather than growing
    along with whatever gets added later.
    """
    assert set(configs) == {ANALYST, ORDER}
    assert set(configs[ANALYST].expected_tools) & set(ERP_TOOLS) == set(ANALYST_TOOL_SET)
    assert set(configs[ANALYST].expected_tools) - set(ERP_TOOLS) == {
        "chart_generator",
        "download_sandbox_file",
    }
    assert set(configs[ORDER].expected_tools) - set(ERP_TOOLS) == {"request_order_info"}
    assert set(configs[ORDER].expected_tools) & set(ERP_TOOLS) == set(ORDER_TOOL_SET)
    assert configs[ANALYST].scope == ANALYST
    assert configs[ORDER].scope == ORDER


def test_analyst_grants_no_write_tool(configs):
    """The analyst's resolved set contains no write tool, by two independent routes."""
    analyst = configs[ANALYST]

    assert not analyst.grants_write()
    assert not (analyst.expected_tools & set(ERP_WRITE_TOOLS))
    # The config avoids `order_*` on purpose: it would reach the two write tools.
    assert "order_*" not in analyst.tool_patterns
    assert ORDER not in configs[ORDER].expected_tools  # sanity: names are tools, not agents
    assert set(configs[ORDER].expected_tools) >= set(ERP_WRITE_TOOLS)


def test_a_pattern_that_matches_nothing_aborts_startup():
    """A stale pattern is a configuration that no longer describes the catalogue."""
    with pytest.raises(SubAgentLoaderError) as failure:
        build(document(tool_patterns=["part_del*"], expected_tools=["part_query"]))

    assert "matched no tool" in str(failure.value)


def test_a_pattern_that_grants_more_than_the_snapshot_aborts_startup():
    """The catalogue grew a tool nobody re-reviewed: refuse rather than widen authority."""
    with pytest.raises(SubAgentLoaderError) as failure:
        build(document(tool_patterns=["part_*"], expected_tools=["part_query"]))

    message = str(failure.value)
    assert "disagree" in message
    assert "part_search" in message


def test_a_snapshot_listing_a_tool_the_catalogue_lacks_aborts_startup():
    with pytest.raises(SubAgentLoaderError) as failure:
        build(document(tool_patterns=["part_*"], expected_tools=["part_query", "part_teleport"]))

    assert "not in the catalogue" in str(failure.value)


def test_read_only_scope_is_refused_even_when_the_snapshot_agrees():
    """The second, snapshot-independent guard: a read-only scope may never hold a write tool.

    The patterns and the snapshot here *agree* — the disagreement check would pass — so
    only the scope check can catch it.
    """
    with pytest.raises(SubAgentLoaderError) as failure:
        build(
            document(
                name="sneaky-analyst",
                scope=ANALYST,
                tool_patterns=["order_*"],
                expected_tools=sorted({"order_search_details", "order_create", "order_update"}),
                # Gated as well, so the write-approval rule does not fire first and this
                # test keeps measuring the read-only scope check alone.
                interrupt_on={"order_create": True, "order_update": True},
            )
        )

    assert "read-only" in str(failure.value)
    assert "order_create" in str(failure.value)


def test_pattern_matching_is_by_whole_name_not_substring():
    """``part`` must not reach three tools by accident, and ``order_*`` must be exact."""
    order_hits = build(
        document(
            name="order-agent",
            tool_patterns=["order_*"],
            expected_tools=sorted({"order_search_details", "order_create", "order_update"}),
            interrupt_on={"order_create": True, "order_update": True},
        )
    )
    assert order_hits.expected_tools == {"order_search_details", "order_create", "order_update"}

    with pytest.raises(SubAgentLoaderError):
        build(document(tool_patterns=["part"], expected_tools=["part_query"]))


def test_required_keys_are_enforced_for_every_declared_field():
    """Each required key is refused by name when absent."""
    for key in ("name", "description", "scope", "tool_patterns", "expected_tools", "system_prompt"):
        incomplete = document()
        del incomplete[key]
        with pytest.raises(SubAgentLoaderError) as failure:
            build(incomplete)
        assert key in str(failure.value), f"{key} should be named in the failure"

    with pytest.raises(SubAgentLoaderError):
        build(document(description="   "))


def test_duplicate_subagent_names_are_refused(tmp_path):
    """Two files claiming the same name would make delegation depend on file order."""
    body = (CONFIGS_DIR / "procurement_analyst.yaml").read_text(encoding="utf-8")
    (tmp_path / "a.yaml").write_text(body, encoding="utf-8")
    (tmp_path / "b.yaml").write_text(body, encoding="utf-8")

    with pytest.raises(SubAgentLoaderError) as failure:
        # Against the full catalogue: the point is the duplicate name, and validating against
        # a partial one would fail first for a different reason and hide it.
        load_all(available_tools=CATALOGUE, write_tools=ERP_WRITE_TOOLS, directory=tmp_path)

    assert "duplicate" in str(failure.value)


def test_a_missing_config_directory_is_refused(tmp_path):
    with pytest.raises(SubAgentLoaderError):
        load_all(
            available_tools=ERP_TOOLS,
            write_tools=ERP_WRITE_TOOLS,
            directory=tmp_path / "nope",
        )


# --------------------------------------------------------------------------- #
# sub-agent return envelope
# --------------------------------------------------------------------------- #


def test_subagent_return_parses_the_contract_envelope():
    parsed = SubAgentReturn.parse(
        {
            "summary": "P001 最低报价 24.00（S002）",
            "facts": ["P001 库存 3，低于阈值 10"],
            "artifact_ids": ["artifact-7"],
            "warnings": ["S003 页面超时，未取到报价"],
            "next_action": "建议向 S002 补货 50 件",
        }
    )

    assert parsed.summary.startswith("P001")
    assert parsed.warnings == ["S003 页面超时，未取到报价"]
    assert parsed.next_action is not None


def test_subagent_return_names_missing_fields_instead_of_defaulting():
    """A malformed delegation must not be indistinguishable from a clean empty result."""
    with pytest.raises(SubAgentReturnError) as failure:
        SubAgentReturn.parse({"facts": [], "artifact_ids": [], "warnings": []})

    problems = str(failure.value)
    assert "summary" in problems
    assert "next_action" in problems


def test_subagent_return_rejects_wrong_types():
    with pytest.raises(SubAgentReturnError):
        SubAgentReturn.parse(
            {
                "summary": "ok",
                "facts": "just one string",
                "artifact_ids": [],
                "warnings": [],
                "next_action": None,
            }
        )

    with pytest.raises(SubAgentReturnError):
        SubAgentReturn.parse(["not", "an", "object"])

    # An explicit empty envelope is legal: "nothing to report" is a real answer.
    empty = SubAgentReturn.parse(
        {"summary": "无可报告内容", "facts": [], "artifact_ids": [], "warnings": [], "next_action": None}
    )
    assert empty.facts == []


# --------------------------------------------------------------------------- #
# prompt assembly and context isolation
# --------------------------------------------------------------------------- #


def test_preferences_reach_the_prompt_with_contract_defaults():
    resolved = effective_preferences({"currency": "USD"})

    assert resolved["currency"] == "USD"
    assert resolved["language"] == "zh-CN"  # untouched default
    assert resolved["chart_type"] == "bar"

    rendered = render_preferences({"currency": "USD"})
    assert "USD" in rendered

    # A stray key in the store must not become an instruction the model reads.
    assert "ignore_previous" not in render_preferences({"ignore_previous": "yes"})


def test_subagent_context_is_the_task_not_the_parent_transcript():
    context = build_subagent_context(
        task="比较 P001 的两家报价",
        preferences={"currency": "CNY"},
        relevant_facts=["P001 有 S001、S002 两家供货"],
    )

    assert "比较 P001 的两家报价" in context
    assert "P001 有 S001、S002 两家供货" in context
    assert "CNY" in context
    assert "返回格式" in context


def test_main_prompt_keeps_the_runtime_rules_and_delegates_domain_work():
    prompt = build_main_prompt(preferences={}, skills=[])

    assert "采购" in prompt
    assert "write_todos" in prompt
    assert ANALYST in prompt
    assert ORDER in prompt
    # The main agent is told not to answer domain questions itself.
    assert "必须委派" in prompt
    assert "不要试图自己回答领域问题" in prompt


# --------------------------------------------------------------------------- #
# middleware inventory
# --------------------------------------------------------------------------- #


def test_middleware_inventory_marks_unimplemented_slots_honestly():
    """Declared-but-unbuilt middleware is visible as such, not implied to work."""
    inventory = middleware_inventory()

    by_key = {entry["key"]: entry for entry in inventory}
    assert by_key["todo_list"]["implemented"] is True
    assert by_key["skills_sync"]["implemented"] is True
    # The matrix is complete as of T19: every slot is built, and each names its owner, so
    # nothing is either orphaned or silently claimed.
    assert all(entry["implemented"] for entry in inventory), [
        entry["key"] for entry in inventory if not entry["implemented"]
    ]
    assert by_key["memory_update"]["owner_task"] == "T18"
    assert by_key["sandbox_breaker"]["owner_task"] == "T19"
    assert all(entry["owner_task"] for entry in inventory)


def test_inventory_reports_the_hooks_the_classes_really_override():
    """Intended order is not execution order; the hooks are read back off the classes."""
    from langgraph.store.memory import InMemoryStore

    from agent.middlewares.user_skills_restore import StoreAssignmentReader

    store = InMemoryStore()
    context = {
        "manager": object(),
        "owner_resolver": lambda runtime: "demo-a",
        "backend_provider": lambda: None,
        "skill_reader": StoreAssignmentReader(store),
        # T18's two slots need a store to read preferences from; T19's compaction slot needs
        # a model, a backend and somewhere to offload large results to. Without them the slot
        # is still *declared* implemented but reported as not buildable, which is the
        # distinction the inventory is meant to make.
        "store_provider": lambda owner: store,
        "model": ScriptedChatModel(script=[]),
        "backend": _backend(store),
        "workspace_writer": lambda path, payload: None,
    }
    inventory = middleware_inventory(context)
    by_key = {entry["key"]: entry for entry in inventory}

    assert "before_agent" in by_key["skills_sync"]["hooks"]
    assert by_key["skills_sync"]["hooks"], "a built middleware must declare at least one hook"
    assert "wrap_model_call" in by_key["context_injection"]["hooks"]

    # Without the store the same slot is honestly reported as unbuildable rather than
    # silently dropped or raising.
    without_store = {
        key: value for key, value in context.items() if key != "store_provider"
    }
    degraded = {entry["key"]: entry for entry in middleware_inventory(without_store)}
    assert degraded["context_injection"]["implemented"] is True
    assert degraded["context_injection"]["buildable"] is False
    assert degraded["context_injection"]["hooks"] == []

    built = build_middlewares(context)
    implemented = sum(1 for entry in inventory if entry["implemented"])
    # A slot may expand to several middleware — the budget is two, and the compaction slot is
    # a tool plus the offloader — so the built list is at least as long as the slot count,
    # never equal to it.
    assert len(built) >= implemented
    assert len({type(item).__name__ for item in built}) >= implemented
    # TodoListMiddleware is what puts `write_todos` in the tool set at all.
    names = {type(item).__name__ for item in built}
    assert "TodoListMiddleware" in names
    assert declared_hooks(built[0])


def test_missing_middleware_context_is_an_error_not_a_silent_skip():
    with pytest.raises(KeyError) as failure:
        build_middlewares({"backend_provider": lambda: None})

    assert "sandbox_health" in str(failure.value)


# --------------------------------------------------------------------------- #
# write guard
# --------------------------------------------------------------------------- #


def test_write_tools_refuse_without_authorization():
    """Without an approval the call returns a structured refusal and performs no write."""
    from langchain_core.tools import tool

    @tool
    def order_create(supplier_id: str, currency: str) -> str:
        """Create an order."""
        return "order-created"

    @tool
    def part_query(part_id: str) -> str:
        """Read a part."""
        return "read-ok"

    guarded, names = guard_write_tools(
        [order_create, part_query], write_tools=ERP_WRITE_TOOLS, authorizer=None
    )

    assert names == ["order_create"]
    by_name = {tool_.name: tool_ for tool_ in guarded}
    result = json.loads(by_name["order_create"].invoke({"supplier_id": "S001", "currency": "CNY"}))

    assert result["ok"] is False
    assert result["data"] is None
    assert result["error"]["code"] == "APPROVAL_REQUIRED"
    assert "order-created" not in json.dumps(result)
    # Reads are untouched by the guard.
    assert by_name["part_query"].invoke({"part_id": "P001"}) == "read-ok"


def test_write_tools_run_once_an_authorizer_approves():
    """The guard is a gate, not a permanent block: approvals route through it in T12."""
    from langchain_core.tools import tool

    @tool
    def order_update(order_id: str) -> str:
        """Update an order."""
        return "updated"

    seen: list[tuple[str, dict]] = []

    def authorizer(name: str, args: dict) -> bool:
        seen.append((name, args))
        return args.get("order_id") == "ORD-1"

    guarded, _ = guard_write_tools([order_update], write_tools=ERP_WRITE_TOOLS, authorizer=authorizer)
    tool_ = guarded[0]

    assert tool_.invoke({"order_id": "ORD-1"}) == "updated"
    assert json.loads(tool_.invoke({"order_id": "ORD-2"}))["error"]["code"] == "APPROVAL_REQUIRED"
    assert [item[0] for item in seen] == ["order_update", "order_update"]


# --------------------------------------------------------------------------- #
# file-sharing boundary
# --------------------------------------------------------------------------- #


def test_file_sharing_boundary_matches_the_contract():
    """Context isolation is not file isolation: the shared roots are explicit values."""
    assert SHARED_WRITE_ROOTS == ("/skills",)
    assert is_within("/skills/procurement/web-scraper/SKILL.md", SHARED_WRITE_ROOTS)
    assert is_within("/memories/preferences.json", SHARED_READ_ROOTS)
    # A sibling directory that merely shares a prefix is not inside.
    assert not is_within("/skillsware/evil", SHARED_WRITE_ROOTS)
    assert not is_within("relative/path", SHARED_WRITE_ROOTS)
    assert not is_within("/workspace/output.csv", SHARED_WRITE_ROOTS)


# --------------------------------------------------------------------------- #
# the assembled agent: planning, delegation, real data
# --------------------------------------------------------------------------- #


def test_todos_persist_in_graph_state(configs):
    """`write_todos` writes real state that survives the turn, provided the middleware."""
    from langgraph.store.memory import InMemoryStore

    model = ScriptedChatModel(
        script=[
            tool_call(
                "write_todos",
                {
                    "todos": [
                        {"content": "查看库存预警", "status": "in_progress"},
                        {"content": "比较 P001 报价", "status": "pending"},
                    ]
                },
                "todo-1",
            ),
            AIMessage(content="已规划两步。"),
        ]
    )
    backend = _backend(InMemoryStore())
    assembly = build_main_agent(
        model=model,
        tools=_stub_tools(),
        write_tools=ERP_WRITE_TOOLS,
        backend=backend,
        owner_user_id="demo-a",
        subagents=configs,
        # Only the planning slot is in play here; the sandbox-backed middleware belongs to
        # tests that stand up a sandbox.
        middleware=build_middlewares({}, only={"todo_list"}),
        middleware_inventory=middleware_inventory({}),
    )

    state = asyncio.run(
        assembly.graph.ainvoke({"messages": [HumanMessage("帮我看看哪些物料该补货")]})
    )

    assert [todo["content"] for todo in state["todos"]] == ["查看库存预警", "比较 P001 报价"]
    assert state["todos"][1]["status"] == "pending"
    tool_results = [m.content for m in state["messages"] if isinstance(m, ToolMessage)]
    assert any("Updated todo list" in text for text in tool_results)


def test_delegation_gives_the_subagent_its_own_context(configs):
    """The analyst sees its task, not the parent's transcript."""
    from langgraph.store.memory import InMemoryStore

    noise = "先记录一下背景。" * 40
    first = tool_call(
        "task", {"description": "查库存预警", "subagent_type": ANALYST}, "delegate-1"
    )
    # The parent emits the noise *alongside* the delegation, so it is genuinely part of the
    # parent's transcript at the moment the sub-agent is invoked.
    first.content = noise
    parent_model = ScriptedChatModel(
        script=[first, AIMessage(content="P001 需要补货。")]
    )
    child_model = ScriptedChatModel(script=[AIMessage(content="P001 库存 3，低于阈值。")])

    assembly = _assembly(
        configs, InMemoryStore(), parent_model, subagent_models={ANALYST: child_model}
    )

    state = asyncio.run(
        assembly.graph.ainvoke({"messages": [HumanMessage("哪些物料要补货？")]})
    )

    assert child_model.contexts, "子代理应当被调用"
    first_child_context = child_model.contexts[0]

    assert len(first_child_context) == 2, [type(m).__name__ for m in first_child_context]
    assert isinstance(first_child_context[0], SystemMessage)
    assert "查库存预警" in first_child_context[1].content
    # The parent's unrelated chatter is not in the sub-agent's context.
    assert not any("先记录一下背景" in str(getattr(m, "content", "")) for m in first_child_context)

    task_result = [m for m in state["messages"] if isinstance(m, ToolMessage) and m.name == "task"]
    assert task_result and "P001 库存 3" in task_result[0].content


def test_real_read_only_query_trace_calls_mcp_and_reports_erp_data(configs, gateway):
    """The analyst reaches the live gateway, and the numbers are the ERP's, not the model's.

    The scripted model only *chooses* ``inventory_warning``; the payload is compared with a
    direct gateway call, so a model that invented inventory would fail here.
    """
    from langgraph.store.memory import InMemoryStore

    async def run() -> tuple[Any, dict, ScriptedChatModel, ScriptedChatModel]:
        async with live_tools(gateway) as tools:
            # Ask the gateway first, so the sub-agent's report can be written from the ERP's
            # own answer rather than from anything the test invented.
            reference = await mcp_payload(gateway, INVENTORY_WARNING, {"page": 1, "page_size": 20})
            part_ids = [item["part_id"] for item in reference["data"]["items"]]
            total = reference["data"]["total"]
            assert part_ids, "种子里应当有库存预警数据，否则这个用例没有意义"

            report = json.dumps(
                {
                    "summary": f"共 {total} 条库存预警",
                    "facts": [f"{part_id} 低于阈值" for part_id in part_ids],
                    "artifact_ids": [],
                    "warnings": [],
                    "next_action": "建议补货",
                },
                ensure_ascii=False,
            )

            parent_model = ScriptedChatModel(
                script=[
                    tool_call(
                        "task",
                        {"description": "查库存预警", "subagent_type": ANALYST},
                        "delegate-1",
                    ),
                    AIMessage(content="请查看下方补货建议。"),
                ]
            )
            child_model = ScriptedChatModel(
                script=[
                    tool_call(INVENTORY_WARNING, {"page": 1, "page_size": 20}, "mcp-1"),
                    AIMessage(content=report),
                ]
            )

            assembly = _assembly(
                configs,
                InMemoryStore(),
                parent_model,
                tools=tools,
                subagent_models={ANALYST: child_model},
            )
            state = await assembly.graph.ainvoke(
                {"messages": [HumanMessage("哪些物料需要补货？")]}
            )
        return state, reference, child_model, parent_model

    state, reference, child_model, _ = asyncio.run(run())
    total = reference["data"]["total"]
    expected_part_ids = {item["part_id"] for item in reference["data"]["items"]}

    # 1. The sub-agent really called the gateway: the tool result in its own context is the
    #    gateway's envelope, not something the scripted model produced.
    child_tool_results = [
        message
        for context in child_model.contexts
        for message in context
        if isinstance(message, ToolMessage) and message.name == INVENTORY_WARNING
    ]
    assert child_tool_results, "子代理的上下文里应当有 MCP 工具结果"
    gateway_envelope = json.loads(tool_text(child_tool_results[0]))

    assert gateway_envelope["ok"] is True
    returned_ids = {item["part_id"] for item in gateway_envelope["data"]["items"]}
    assert returned_ids == expected_part_ids, (
        "子代理拿到的库存是 ERP 的，而不是模型编造的"
    )

    # 2. The trace shows a delegation whose argument named the real tool outcome.
    task_results = [m for m in state["messages"] if isinstance(m, ToolMessage) and m.name == "task"]
    assert task_results, "task 调用应当产生工具结果"
    report = SubAgentReturn.parse(json.loads(tool_text(task_results[0])))

    assert str(total) in report.summary
    assert set(report.facts), "报告应当带可核对的事实"
    assert any(part_id in " ".join(report.facts) for part_id in expected_part_ids)


def test_agent_assembly_reports_the_real_inventory(configs, gateway):
    """The assembly snapshot lists what was actually granted, not what was intended."""
    from langgraph.store.memory import InMemoryStore

    async def run():
        async with live_tools(gateway) as tools:
            assembly = _assembly(
                configs, InMemoryStore(), ScriptedChatModel(script=[]), tools=tools
            )
            return assembly

    assembly = asyncio.run(run())
    snapshot = assembly.as_dict()

    # Delegated = what the sub-agents declare. Whatever is left — after T16 only
    # `web_search` — is agent-level and stays with the main agent.
    subagent_tools = {name for config in configs.values() for name in config.expected_tools}
    assert set(assembly.delegated_tools) == subagent_tools
    assert set(assembly.tool_names) == set(CATALOGUE) - subagent_tools
    assert "web_search" in assembly.tool_names
    assert set(assembly.guarded_write_tools) == set(ERP_WRITE_TOOLS)
    assert [entry["name"] for entry in snapshot["subagents"]] == [ANALYST, ORDER]

    by_name = {entry["name"]: entry for entry in snapshot["subagents"]}
    assert by_name[ANALYST]["grants_write"] is False
    assert by_name[ORDER]["grants_write"] is True
    # The analyst's *ERP* surface is still exactly the read set. T16 added the two report
    # tools — the chart and the artifact export — which are deliverables, not business data,
    # so they are checked separately rather than letting them widen the ERP assertion.
    assert set(by_name[ANALYST]["expected_tools"]) & set(ERP_TOOLS) == set(ERP_READ_TOOLS)
    assert set(by_name[ANALYST]["expected_tools"]) - set(ERP_TOOLS) == {
        "chart_generator",
        "download_sandbox_file",
    }

    assert snapshot["middleware"], "the assembly should carry the middleware inventory"


def test_the_main_agent_holds_no_erp_tool(configs, gateway):
    """Delegation is structural, not a request in a prompt.

    A real-model smoke test showed the model answering directly when it held the read
    tools. The main agent now holds none of them, so "plan, dispatch, aggregate" is a fact
    about its tool set: it cannot bypass the analyst even if it tries.
    """
    from langgraph.store.memory import InMemoryStore

    async def run():
        async with live_tools(gateway) as tools:
            return _assembly(configs, InMemoryStore(), ScriptedChatModel(script=[]), tools=tools)

    assembly = asyncio.run(run())

    assert set(assembly.tool_names) & set(ERP_TOOLS) == set(), (
        "主 Agent 不应持有任何 ERP 工具，否则它可以绕过子代理"
    )
    # The complement of that rule: a tool no sub-agent declares stays with the main agent.
    subagent_tools = {name for config in configs.values() for name in config.expected_tools}
    assert set(assembly.delegated_tools) == subagent_tools
    assert set(assembly.tool_names) == set(CATALOGUE) - subagent_tools


def test_the_main_agent_cannot_hold_a_tool_no_subagent_covers():
    """A tool outside every sub-agent's scope stays with the main agent.

    The delegation rule takes *declared* tools away from the main agent; it must not take the
    rest. The example is a name no sub-agent declares, so this keeps measuring the rule
    rather than whichever tool happens to be agent-level at the moment — T16 moved
    ``chart_generator`` into the analyst and would otherwise have turned this into a test of
    nothing.
    """
    from langchain_core.tools import StructuredTool
    from langgraph.store.memory import InMemoryStore

    def render_calendar(**kwargs: Any) -> str:
        return "calendar"

    extra = StructuredTool.from_function(
        func=render_calendar, name="render_calendar", description="Render a calendar."
    )
    base = _stub_tools()

    assembly = build_main_agent(
        model=ScriptedChatModel(script=[]),
        tools=[*base, extra],
        write_tools=ERP_WRITE_TOOLS,
        backend=_backend(InMemoryStore()),
        owner_user_id="demo-a",
        subagents=load_all(available_tools=CATALOGUE, write_tools=ERP_WRITE_TOOLS),
        middleware=build_middlewares({}, only={"todo_list"}),
    )

    assert "render_calendar" in assembly.tool_names
    assert "web_search" in assembly.tool_names
    assert set(assembly.tool_names) & set(ERP_TOOLS) == set()


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _stub_tool(name: str):
    from langchain_core.tools import StructuredTool

    def call(**kwargs: Any) -> str:
        return json.dumps({"ok": True, "data": {"items": [], "total": 0}, "tool": name})

    return StructuredTool.from_function(
        func=call, name=name, description=f"Stand-in for the {name} tool."
    )


def _stub_tools() -> list:
    """Named stand-ins for every tool in the catalogue.

    The graph tests exercise planning and delegation, not the ERP. The names still have to
    be the real ones, because the loader refuses to grant a sub-agent a tool that is not in
    the provided set — which is the property being relied on.
    """
    return [_stub_tool(name) for name in CATALOGUE]


def _backend(store: Any):
    """A virtual backend over a throwaway sandbox handle.

    The filesystem tools are not exercised here; T08/T10 cover them. What matters for T11
    is that assembly does not require a live container.
    """

    class _NoopSandbox:
        # Skill discovery now reads directory metadata even in routing-only unit graphs.
        # No skill files exist here; business and shell calls remain forbidden.
        def ls(self, path):
            from deepagents.backends.protocol import LsResult
            return LsResult(entries=[])

        async def als(self, path):
            return self.ls(path)

        def __getattr__(self, name: str):
            def _missing(*args: Any, **kwargs: Any):
                raise AssertionError(f"sandbox.{name} should not be called in this test")

            return _missing

    from agent.main_agent import build_virtual_backend

    return build_virtual_backend(
        sandbox_backend=_NoopSandbox(), store=store, owner_user_id="demo-a"
    )


def _assembly(
    configs,
    store: Any,
    model: ScriptedChatModel,
    *,
    tools: list | None = None,
    subagent_models: dict[str, Any] | None = None,
):
    """Assemble the real graph with the given models.

    ``subagent_models`` is the framework's own seam for giving a sub-agent its own model,
    so the tests drive the analyst without reaching into compiled-graph internals.
    """
    # Every agent-layer tool has to be present whichever catalogue the caller supplies — the
    # live gateway does not expose them, they are not ERP tools. Stubs are enough here: this
    # file is about tool *routing*, and each tool's own behaviour is covered where it lives.
    catalogue = list(tools if tools is not None else _stub_tools())
    present = {getattr(tool, "name", "") for tool in catalogue}
    for name in LOCAL_TOOL_NAMES:
        if name not in present:
            catalogue.append(_stub_tool(name))
    return build_main_agent(
        model=model,
        tools=catalogue,
        write_tools=ERP_WRITE_TOOLS,
        backend=_backend(store),
        owner_user_id="demo-a",
        subagents=configs,
        middleware=build_middlewares({}, only={"todo_list"}),
        middleware_inventory=middleware_inventory({}),
        subagent_models=subagent_models,
        store=store,
    )
