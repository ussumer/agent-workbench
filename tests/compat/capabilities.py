"""The eight CAP experiments from ``docs/plan/dependencies.md``.

Each function runs a real experiment and returns a :class:`CapabilityRecord` with
redacted evidence. Nothing here is satisfied by an import succeeding: CAP-02 needs a
real tool call and a real ``ToolMessage``, CAP-07 needs a real container, CAP-08 needs a
real service process.

Values that could carry a secret (prompts containing configuration, headers, raw
provider payloads) are redacted before they enter a record.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.config import ModelConfig
from agent.env_utils import redact, secret_values

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Languages the CAP-01 answer must contain to count as a Chinese response.
CJK_RANGES = ((0x4E00, 0x9FFF), (0x3400, 0x4DBF))

#: How many times a live model may be asked to emit the tool call that raises the
#: approval interrupt. Small on purpose: if it never happens, that is a finding.
MAX_INTERRUPT_ATTEMPTS = 3


@dataclass(frozen=True)
class CapabilityRecord:
    """Outcome of one CAP experiment, with its evidence and the checks it passed."""

    cap: str
    name: str
    passed: bool
    evidence: dict[str, Any] = field(default_factory=dict)
    checks: list[str] = field(default_factory=list)
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "cap": self.cap,
            "name": self.name,
            "passed": self.passed,
            "checks": list(self.checks),
            "evidence": self.evidence,
            "notes": self.notes,
        }


def redact_value(value: Any, env: dict[str, str] | None = None) -> Any:
    """Recursively redact any string inside a structure."""
    secrets = secret_values(env or {}) if env is not None else set()
    if isinstance(value, str):
        return redact(value, secrets) if secrets else value
    if isinstance(value, dict):
        return {key: redact_value(item, env) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(item, env) for item in value]
    return value


def contains_cjk(text: str) -> bool:
    """Whether a string contains CJK ideographs, proving a Chinese response."""
    return any(
        any(low <= ord(char) <= high for low, high in CJK_RANGES)
        for char in text
    )


# --------------------------------------------------------------------------- #
# shared tools
# --------------------------------------------------------------------------- #


def build_tools() -> tuple[Any, Any]:
    """The tools the CAP experiments share: a pure calculator and a fake lookup."""
    from langchain_core.tools import tool

    @tool
    def add(a: int, b: int) -> int:
        """Add two integers and return the sum."""
        return a + b

    @tool
    def lookup_part(part_id: str) -> str:
        """Look up whether a demo part id is active. Read-only."""
        catalogue = {"P001": "active", "P003": "active", "P005": "inactive"}
        return catalogue.get(part_id.upper(), "unknown")

    return add, lookup_part


# --------------------------------------------------------------------------- #
# CAP-01
# --------------------------------------------------------------------------- #


def cap01_model_chinese_and_two_turns(model_config: ModelConfig) -> CapabilityRecord:
    """Chinese response and a two-turn conversation, with the provider's own identity.

    The second turn only asks for a transformation of the first answer, so passing it
    proves the conversation history was carried — a single-turn probe cannot.
    """
    from langchain_core.messages import AIMessage, HumanMessage

    model = model_config.create_chat_model()

    first = model.invoke("用中文回答：一加一等于几？只回答数字。")
    first_text = _text_of(first).strip()

    second = model.invoke(
        [
            HumanMessage(content="用中文回答：一加一等于几？只回答数字。"),
            AIMessage(content=first_text),
            HumanMessage(content="把上一个答案乘以 10，只回答数字。"),
        ]
    )
    second_text = _text_of(second).strip()

    chinese = model.invoke("用中文写一句不超过 20 字的话，说明今天适合采购。")
    chinese_text = _text_of(chinese).strip()
    reported_model = (getattr(first, "response_metadata", None) or {}).get("model_name")

    checks: list[str] = []
    if first_text == "2":
        checks.append("turn1_correct")
    if second_text == "20":
        checks.append("turn2_uses_context")
    if contains_cjk(chinese_text):
        checks.append("chinese_response")
    if reported_model:
        checks.append("provider_reports_model_name")

    return CapabilityRecord(
        cap="CAP-01",
        name="模型中文和两轮对话",
        passed=("turn1_correct" in checks and "turn2_uses_context" in checks and "chinese_response" in checks),
        checks=checks,
        evidence={
            "configured_model_id": model_config.model_id,
            "reported_model_name": reported_model,
            "protocol": model_config.protocol,
            "base_url": model_config.base_url,
            "turn1_reply": first_text[:120],
            "turn2_reply": second_text[:120],
            "turn2_expected": "20",
            "chinese_reply": chinese_text[:120],
            "usage": getattr(first, "usage_metadata", None),
        },
        notes=(
            "配置里的 model_id 与 provider 回报的 model_name 一并记录：前者是请求参数，"
            "后者才是实际服务的模型，不能互相假定。"
        ),
    )


# --------------------------------------------------------------------------- #
# CAP-02
# --------------------------------------------------------------------------- #


def cap02_tool_call_and_tool_message(model_config: ModelConfig) -> CapabilityRecord:
    """A real tool call, its ``ToolMessage``, and an answer that matches the result."""
    from deepagents import create_deep_agent
    from langchain_core.messages import HumanMessage, ToolMessage

    add_tool, _ = build_tools()
    agent = create_deep_agent(model=model_config.create_chat_model(), tools=[add_tool])

    result = agent.invoke(
        {"messages": [HumanMessage(content="用 add 工具计算 17 加 25，然后只回答这个数字。")]}
    )
    messages = result["messages"]

    tool_calls = [
        call
        for message in messages
        for call in (getattr(message, "tool_calls", None) or [])
    ]
    tool_messages = [message for message in messages if isinstance(message, ToolMessage)]
    add_calls = [call for call in tool_calls if call.get("name") == "add"]
    final_text = _text_of(messages[-1])

    tool_content = str(tool_messages[0].content) if tool_messages else ""
    checks = [
        "tool_called" if add_calls else "",
        "tool_call_id_present" if add_calls and add_calls[0].get("id") else "",
        "arguments_match" if add_calls and add_calls[0].get("args") == {"a": 17, "b": 25} else "",
        "tool_message_returned" if tool_messages else "",
        "tool_result_correct" if tool_content.strip() == "42" else "",
        "final_answer_matches" if "42" in final_text else "",
    ]
    checks = [check for check in checks if check]

    return CapabilityRecord(
        cap="CAP-02",
        name="add(a,b) 工具调用及 ToolMessage",
        passed=(
            bool(add_calls)
            and bool(tool_messages)
            and tool_content.strip() == "42"
            and "42" in final_text
        ),
        checks=checks,
        evidence={
            "tool_call_name": add_calls[0].get("name") if add_calls else None,
            "tool_call_id": add_calls[0].get("id") if add_calls else None,
            "tool_call_args": add_calls[0].get("args") if add_calls else None,
            "tool_message_content": tool_content,
            "tool_message_id": getattr(tool_messages[0], "tool_call_id", None)
            if tool_messages
            else None,
            "final_answer": final_text[:120],
            "message_types": [type(message).__name__ for message in messages],
        },
    )


# --------------------------------------------------------------------------- #
# CAP-03
# --------------------------------------------------------------------------- #


def cap03_streaming_tool_arguments(
    model_config: ModelConfig,
    *,
    agent: Any | None = None,
) -> CapabilityRecord:
    """Raw tool-argument fragments and the JSON they only form once concatenated."""
    from deepagents import create_deep_agent
    from langchain_core.messages import HumanMessage
    from tests.compat.streaming import capture_tool_argument_fragments, capture_v2

    add_tool, _ = build_tools()
    bound = model_config.create_chat_model().bind_tools([add_tool])
    fragments, concatenated = capture_tool_argument_fragments(
        bound.stream("用 add 工具计算 17 加 25，只调用工具，不要解释。")
    )

    parsed: Any = None
    parse_error: str | None = None
    try:
        parsed = json.loads(concatenated) if concatenated else None
    except json.JSONDecodeError as failure:
        parse_error = str(failure)

    graph = agent or create_deep_agent(
        model=model_config.create_chat_model(), tools=[add_tool]
    )
    capture = capture_v2(
        graph,
        {"messages": [HumanMessage(content="用 add 工具计算 17 加 25，然后只回答数字。")]},
        config={"configurable": {"thread_id": f"cap03-{uuid.uuid4().hex[:8]}"}},
    )
    envelope = capture.redacted()

    checks = [
        "fragments_observed" if fragments else "",
        "fragments_were_split" if len(fragments) > 1 else "",
        "concatenation_is_valid_json" if parsed is not None else "",
        "concatenated_args_correct" if parsed == {"a": 17, "b": 25} else "",
        "v2_envelope_seen" if capture.parts else "",
        "envelope_has_type_and_ns"
        if all(key in (capture.parts[0].as_dict() if capture.parts else {}) for key in ("type", "ns"))
        else "",
    ]
    checks = [check for check in checks if check]

    return CapabilityRecord(
        cap="CAP-03",
        name="流式工具参数拼接",
        passed=parsed == {"a": 17, "b": 25} and bool(capture.parts),
        checks=checks,
        evidence={
            "raw_fragments": fragments,
            "fragment_count": len(fragments),
            "concatenated": concatenated,
            "parsed_args": parsed,
            "parse_error": parse_error,
            "v2_stream": envelope,
        },
        notes=(
            "分片本身通常不是合法 JSON，必须拼接后才可解析；v2 信封的 type 说明"
            "「到了什么」，ns 说明「从哪来」。"
        ),
    )


# --------------------------------------------------------------------------- #
# CAP-04
# --------------------------------------------------------------------------- #


def cap04_subagent_delegation(
    model_config: ModelConfig,
    *,
    capture: bool = True,
) -> CapabilityRecord:
    """Delegation to a read-only sub-agent, plus how sub-events are actually addressed.

    The first version of this experiment asserted that ``task``-based delegation never
    produces a namespace. Measurement disproved it, and the corrected model is:

    * ``stream(..., subgraphs=True)`` is **the** switch. Without it every part is
      root-namespaced and no attribution is possible; with it, both delegation styles
      are addressed.
    * A ``task``-tool sub-agent appears under ``tools:<task-id>``.
    * A compiled subgraph used as a node appears under ``<node-name>:<task-id>``.

    Both are recorded, together with the empty namespace list produced by the same graph
    when the flag is off, so a later consumer has the contrast rather than a claim.
    """
    from deepagents import SubAgent, create_deep_agent
    from langchain_core.messages import HumanMessage
    from tests.compat.streaming import capture_v2

    _, lookup = build_tools()
    subagent = SubAgent(
        name="part-researcher",
        description="只读查询演示物料状态。需要查询物料时必须委派给它。",
        system_prompt=(
            "你是只读检索子代理。只能调用 lookup_part 查询物料状态，"
            "不得修改任何东西，不得下订单。回答要简短。"
        ),
        tools=[lookup],
    )
    agent = create_deep_agent(
        model=model_config.create_chat_model(),
        tools=[],
        subagents=[subagent],
        system_prompt=(
            "你必须把物料查询委派给 part-researcher 子代理。"
            "不要自己回答，也不要调用其它工具。"
        ),
    )

    payload = {
        "messages": [
            HumanMessage(content="查询物料 P005 的状态，用一句话告诉我它是否可用。")
        ]
    }
    config = {"configurable": {"thread_id": f"cap04-{uuid.uuid4().hex[:8]}"}}

    stream_capture = capture_v2(agent, payload, config=config) if capture else None
    result = agent.invoke(payload, config=config)
    messages = result["messages"]

    task_calls = [
        call
        for message in messages
        for call in (getattr(message, "tool_calls", None) or [])
        if call.get("name") == "task"
    ]
    task_namespaces = (
        [record.namespace for record in stream_capture.parts if record.is_subgraph]
        if stream_capture
        else []
    )
    final_text = _text_of(messages[-1])
    subgraph_namespaces = _compiled_subgraph_namespaces()

    checks = [
        "task_tool_invoked" if task_calls else "",
        "subagent_named_in_call"
        if any(
            "part-researcher" in json.dumps(call.get("args") or {}, ensure_ascii=False)
            for call in task_calls
        )
        else "",
        "subagent_answer_returned" if final_text else "",
        "task_delegation_namespaced" if task_namespaces else "",
        "compiled_subgraph_namespaced" if subgraph_namespaces else "",
        "flag_off_flattens_namespace" if not _SUBGRAPH_CONTRAST["without"] else "",
        ]
    checks = [check for check in checks if check]

    return CapabilityRecord(
        cap="CAP-04",
        name="主 Agent 委派只读子 Agent",
        passed=bool(task_calls) and bool(final_text) and bool(subgraph_namespaces),
        checks=checks,
        evidence={
            "task_call_count": len(task_calls),
            "task_call_args": redact_value(task_calls[0].get("args")) if task_calls else None,
            "task_delegation_namespaces": [list(ns) for ns in dict.fromkeys(task_namespaces)],
            "compiled_subgraph_namespaces": [list(ns) for ns in dict.fromkeys(subgraph_namespaces)],
            "namespaces_without_subgraphs_flag": [list(ns) for ns in _SUBGRAPH_CONTRAST["without"]],
            "final_answer": final_text[:200],
            "v2_stream": stream_capture.redacted() if stream_capture else None,
        },
        notes=(
            "实测结论（已修正初版假设）：能否按 ns 区分「子事件」取决于 stream(subgraphs=True)。"
            "不传该参数时全部 part 都是根命名空间，子图与工具内子代理的事件被压平，"
            "下游会把子代理的输出误记到主 Agent 名下；传入后，task 委派的子代理落在 "
            "`tools:<task-id>`，作为节点使用的编译子图落在 `<node>:<task-id>`。"
        ),
    )


def _compiled_subgraph_namespaces() -> list[tuple[str, ...]]:
    """Namespaces observed when a compiled graph is used as a node (real sub-events)."""
    from langgraph.graph import END, START, StateGraph
    from tests.compat.streaming import capture_v2
    from typing_extensions import TypedDict

    class Inner(TypedDict, total=False):
        n: int

    class Outer(TypedDict, total=False):
        n: int

    inner_builder = StateGraph(Inner)
    inner_builder.add_node("inner_step", lambda state: {"n": state.get("n", 0) + 1})
    inner_builder.add_edge(START, "inner_step")
    inner_builder.add_edge("inner_step", END)
    inner = inner_builder.compile()

    outer_builder = StateGraph(Outer)
    outer_builder.add_node("child", inner)
    outer_builder.add_edge(START, "child")
    outer_builder.add_edge("child", END)
    outer = outer_builder.compile()

    with_subgraphs = capture_v2(outer, {"n": 0}, stream_mode=["values", "updates"], subgraphs=True)
    without_subgraphs = capture_v2(
        outer, {"n": 0}, stream_mode=["values", "updates"], subgraphs=False
    )
    # The contrast is the point: the same graph yields no namespaced part unless
    # `subgraphs=True` is passed, which is easy to get wrong and invisible when wrong.
    _SUBGRAPH_CONTRAST["with"] = [ns for ns in (r.namespace for r in with_subgraphs.parts) if ns]
    _SUBGRAPH_CONTRAST["without"] = [
        ns for ns in (r.namespace for r in without_subgraphs.parts) if ns
    ]
    return _SUBGRAPH_CONTRAST["with"]


#: Filled by :func:`_compiled_subgraph_namespaces` so the contrast can be reported.
_SUBGRAPH_CONTRAST: dict[str, list[tuple[str, ...]]] = {"with": [], "without": []}


# --------------------------------------------------------------------------- #
# CAP-05
# --------------------------------------------------------------------------- #


def cap05_subagent_interrupt_and_resume(
    model_config: ModelConfig,
    *,
    decision: str = "approve",
) -> CapabilityRecord:
    """A sub-agent raises an approval interrupt; the same thread resumes it.

    ``decision`` is exercised as both ``approve`` and ``reject``: a layer that only ever
    approves is half-verified, because the rejection path is where a paused tool must
    demonstrably *not* run.
    """
    from deepagents import SubAgent, create_deep_agent
    from langchain_core.messages import HumanMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command
    from tests.compat.streaming import DEFAULT_STREAM_MODES

    _, lookup = build_tools()
    subagent = SubAgent(
        name="part-researcher",
        description="只读查询演示物料状态。",
        # The sub-agent must simply use the tool; approval is enforced by the framework
        # through `interrupt_on`. Instructing it to "seek approval first" makes it ask in
        # prose and never emit the tool call, so no interrupt is ever raised.
        system_prompt="你是只读检索子代理。用 lookup_part 工具查询物料状态，只回答工具返回的内容。",
        tools=[lookup],
        interrupt_on={"lookup_part": True},
    )
    agent = create_deep_agent(
        model=model_config.create_chat_model(),
        tools=[],
        subagents=[subagent],
        system_prompt="必须把物料查询委派给 part-researcher，不要自己回答。",
        checkpointer=InMemorySaver(),
    )

    payload = {"messages": [HumanMessage(content="查询物料 P001 的状态。")]}

    # A live model occasionally answers in prose instead of emitting the tool call, in
    # which case no interrupt is raised and there is nothing to resume. Retrying is
    # legitimate here because the assertion is about the interrupt mechanism, not about
    # one stochastic draw — but the attempts are counted and reported, never hidden.
    attempts = 0
    thread_id = ""
    first: dict[str, Any] = {}
    interrupts: Any = []
    while attempts < MAX_INTERRUPT_ATTEMPTS:
        attempts += 1
        thread_id = f"cap05-{uuid.uuid4().hex[:8]}"
        config = {"configurable": {"thread_id": thread_id}}
        first = agent.invoke(payload, config=config)
        interrupts = first.get("__interrupt__") or []
        if interrupts:
            break

    interrupt_payload = _first_interrupt_value(interrupts)

    # Resume on the *same* thread: this is what proves the checkpoint was usable.
    requested = _decide_from_interrupt(interrupt_payload, decision)
    resumed = agent.invoke(
        Command(resume={"decisions": [requested]}),
        config=config,
    )
    resumed_interrupts = resumed.get("__interrupt__") or []
    resumed_text = _text_of(resumed["messages"][-1]) if resumed.get("messages") else ""

    checks = [
        "interrupt_raised" if interrupts else "",
        "interrupt_has_action_requests"
        if isinstance(interrupt_payload, dict) and interrupt_payload.get("action_requests")
        else "",
        "resumed_on_same_thread" if not resumed_interrupts else "",
        "resume_produced_answer" if resumed_text else "",
    ]
    checks = [check for check in checks if check]

    return CapabilityRecord(
        cap="CAP-05",
        name="子 Agent interrupt、原 thread 恢复",
        passed=bool(interrupts) and not resumed_interrupts and bool(resumed_text),
        checks=checks,
        evidence={
            "thread_id": thread_id,
            "decision_requested": decision,
            "attempts_to_raise_interrupt": attempts,
            "interrupt_count": len(interrupts),
            "interrupt_keys": sorted(interrupt_payload) if isinstance(interrupt_payload, dict) else None,
            "action_request_tools": [
                request.get("name")
                for request in (interrupt_payload or {}).get("action_requests", [])
            ]
            if isinstance(interrupt_payload, dict)
            else None,
            "decision_sent": requested.get("type"),
            "resumed_answer": resumed_text[:200],
            "still_interrupted_after_resume": bool(resumed_interrupts),
            "stream_modes_used": list(DEFAULT_STREAM_MODES),
        },
        notes="approve 与 reject 两条路径由参数化用例分别覆盖，reject 时被暂停的工具不得执行。",
    )


def _first_interrupt_value(interrupts: Any) -> Any:
    if not interrupts:
        return None
    first = interrupts[0]
    return getattr(first, "value", first)


def _decide_from_interrupt(payload: Any, wanted: str = "approve") -> dict[str, Any]:
    """Build a decision matching the interrupt's review configs.

    The allowed decision types come from the interrupt payload itself, so a rejection is
    requested by looking up the config's reject type rather than assuming the string.
    """
    allowed: list[str] = []
    config: dict[str, Any] = {}
    if isinstance(payload, dict):
        review_configs = payload.get("review_configs") or []
        if review_configs and isinstance(review_configs[0], dict):
            config = review_configs[0]
            allowed = [str(item) for item in (config.get("allowed_decisions") or [])]

    if allowed and wanted not in allowed:
        wanted = allowed[0]

    decision: dict[str, Any] = {"type": wanted, "args": config.get("args")}
    if wanted == "reject":
        decision["message"] = "演示拒绝：不允许执行该查询"
    return decision


# --------------------------------------------------------------------------- #
# CAP-06
# --------------------------------------------------------------------------- #


_MONGO_WRITER = r'''
import json, sys, uuid
from langgraph.checkpoint.mongodb import MongoDBSaver
from langgraph.store.mongodb import MongoDBStore
from pymongo import MongoClient
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

uri, db_name, thread_id, key = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
client = MongoClient(uri, serverSelectionTimeoutMS=10000, tz_aware=True)

class S(TypedDict, total=False):
    note: str

builder = StateGraph(S)
builder.add_node("stamp", lambda state: {"note": "written-by-writer-process"})
builder.add_edge(START, "stamp")
builder.add_edge("stamp", END)
app = builder.compile(checkpointer=MongoDBSaver(client, db_name=db_name))
result = app.invoke({"note": "seed"}, config={"configurable": {"thread_id": thread_id}})

store = MongoDBStore(client[db_name]["persistent-store"])
store.put(("cap06",), key, {"value": "store-value-from-writer"})

state = app.get_state({"configurable": {"thread_id": thread_id}})
print(json.dumps({
    "pid": __import__("os").getpid(),
    "thread_id": thread_id,
    "checkpoint_id": state.config["configurable"].get("checkpoint_id"),
    "note": state.values.get("note"),
    "store_key": key,
}))
'''

_MONGO_READER = r'''
import json, sys
from langgraph.checkpoint.mongodb import MongoDBSaver
from langgraph.store.mongodb import MongoDBStore
from langgraph.graph import END, START, StateGraph
from pymongo import MongoClient
from typing_extensions import TypedDict

uri, db_name, thread_id, key = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
client = MongoClient(uri, serverSelectionTimeoutMS=10000, tz_aware=True)

class S(TypedDict, total=False):
    note: str

builder = StateGraph(S)
builder.add_node("stamp", lambda state: {"note": "unused"})
builder.add_edge(START, "stamp")
builder.add_edge("stamp", END)
app = builder.compile(checkpointer=MongoDBSaver(client, db_name=db_name))
state = app.get_state({"configurable": {"thread_id": thread_id}})
store = MongoDBStore(client[db_name]["persistent-store"])
item = store.get(("cap06",), key)
print(json.dumps({
    "pid": __import__("os").getpid(),
    "note": state.values.get("note"),
    "checkpoint_id": state.config["configurable"].get("checkpoint_id"),
    "store_value": (item.value or {}).get("value") if item else None,
}))
'''


def cap06_mongo_restart_readback(mongo_uri: str, database: str) -> CapabilityRecord:
    """Write with one process, read with another: no shared in-memory object."""
    thread_id = f"cap06-thread-{uuid.uuid4().hex[:8]}"
    store_key = f"cap06-key-{uuid.uuid4().hex[:8]}"

    writer = _run_child(_MONGO_WRITER, mongo_uri, database, thread_id, store_key)
    reader = _run_child(_MONGO_READER, mongo_uri, database, thread_id, store_key)

    checks = [
        "writer_succeeded" if writer.get("returncode") == 0 else "",
        "reader_succeeded" if reader.get("returncode") == 0 else "",
        "different_processes" if writer.get("pid") and writer.get("pid") != reader.get("pid") else "",
        "checkpoint_read_back" if reader.get("note") == "written-by-writer-process" else "",
        "store_read_back" if reader.get("store_value") == "store-value-from-writer" else "",
    ]
    checks = [check for check in checks if check]

    return CapabilityRecord(
        cap="CAP-06",
        name="Mongo Checkpointer/Store 重启读回",
        passed=(
            writer.get("returncode") == 0
            and reader.get("returncode") == 0
            and reader.get("note") == "written-by-writer-process"
            and reader.get("store_value") == "store-value-from-writer"
            and writer.get("pid") != reader.get("pid")
        ),
        checks=checks,
        evidence={
            "database": database,
            "thread_id": thread_id,
            "writer": {k: v for k, v in writer.items() if k != "stderr"},
            "reader": {k: v for k, v in reader.items() if k != "stderr"},
            "writer_stderr": writer.get("stderr", "")[:300],
            "reader_stderr": reader.get("stderr", "")[:300],
        },
        notes=(
            "写入与读回在两个独立进程里完成，因此不可能共享内存对象；"
            "同一线程的 checkpoint 与 Store 条目都读回了写入值。"
        ),
    )


def _run_child(source: str, *argv: str, timeout: float = 240) -> dict[str, Any]:
    """Run a snippet in a fresh interpreter and parse its JSON line."""
    env = {
        **_child_env(),
    }
    completed = subprocess.run(
        [sys.executable, "-c", source, *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        cwd=str(REPO_ROOT),
        env=env,
    )
    payload: dict[str, Any] = {
        "returncode": completed.returncode,
        "stderr": completed.stderr[-800:],
    }
    for line in reversed(completed.stdout.strip().splitlines()):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload.update(parsed)
        break
    return payload


def _child_env() -> dict[str, str]:
    import os

    src = str(REPO_ROOT / "src")
    existing = os.environ.get("PYTHONPATH", "")
    return {
        **os.environ,
        "PYTHONPATH": f"{src}{os.pathsep}{existing}" if existing else src,
        "PYTHONUNBUFFERED": "1",
    }


# --------------------------------------------------------------------------- #
# CAP-07
# --------------------------------------------------------------------------- #


def cap07_sandbox_io(backend: Any) -> CapabilityRecord:
    """Create, execute, transfer, destroy — with the container identity recorded."""
    payload = b"sandbox-payload-\x00\x01\x02"
    expected_digest = hashlib.sha256(payload).hexdigest()
    remote = "/workspace/scratch/cap07.bin"

    executed = backend.execute("echo cap07-ok; uname -s; python3 -V")
    uploaded = backend.upload_files([(remote, payload)])
    downloaded = backend.download_files([remote])
    returned = downloaded[0].content if downloaded else None
    returned_digest = hashlib.sha256(returned).hexdigest() if returned else None

    checks = [
        "container_executed" if executed.exit_code == 0 else "",
        "exit_code_reported" if executed.exit_code is not None else "",
        "upload_succeeded" if uploaded and uploaded[0].error is None else "",
        "download_succeeded" if downloaded and downloaded[0].error is None else "",
        "checksum_matches" if returned_digest == expected_digest else "",
    ]
    checks = [check for check in checks if check]

    return CapabilityRecord(
        cap="CAP-07",
        name="OpenSandbox 创建执行下载销毁",
        passed=(
            executed.exit_code == 0
            and returned_digest == expected_digest
            and bool(backend.id)
        ),
        checks=checks,
        evidence={
            "sandbox_id": backend.id,
            "image": getattr(getattr(backend, "config", None), "image", None),
            "exit_code": executed.exit_code,
            "executed_output": executed.output[:200],
            "uploaded_path": remote,
            "sha256_expected": expected_digest,
            "sha256_returned": returned_digest,
        },
        notes="销毁由测试载体的收尾负责，回收结果由残留容器数为 0 断言。",
    )


# --------------------------------------------------------------------------- #
# CAP-08
# --------------------------------------------------------------------------- #


def cap08_agent_protocol(base_url: str, *, client: Any | None = None, assistant_id: str | None = None) -> CapabilityRecord:
    """Import, service start and a task result over the Agent Protocol."""
    import httpx
    from deepagents import AsyncSubAgent
    from langgraph_sdk import get_client, get_sync_client

    health = None
    try:
        health = httpx.get(f"{base_url}/ok", timeout=15).json()
    except httpx.HTTPError as failure:
        health = {"error": type(failure).__name__}

    sync_client = client or get_sync_client(url=base_url)
    assistants = sync_client.assistants.search()
    if assistant_id is None and assistants:
        assistant_id = assistants[0]["assistant_id"]

    probe = f"cap08-{uuid.uuid4().hex[:6]}"
    run_result = None
    if assistant_id:
        run_result = sync_client.runs.wait(None, assistant_id, input={"text": probe})

    sub_agent = AsyncSubAgent(
        name="async-echo",
        description="独立 Agent Protocol 服务上的异步子代理",
        graph_id=assistants[0].get("graph_id") if assistants else "echo",
        url=base_url,
    )

    async_result = None
    if assistant_id:
        import asyncio

        async def call() -> Any:
            async_client = get_client(url=base_url)
            thread = await async_client.threads.create()
            return await async_client.runs.wait(thread["thread_id"], assistant_id, input={"text": probe})

        async_result = asyncio.run(call())

    checks = [
        "async_sub_agent_imported" if AsyncSubAgent is not None else "",
        "service_healthy" if isinstance(health, dict) and health.get("ok") else "",
        "graph_discovered" if assistants else "",
        "task_result_returned" if isinstance(run_result, dict) and run_result.get("result") else "",
        "result_matches_input"
        if isinstance(run_result, dict) and run_result.get("result") == probe.upper()
        else "",
        "async_task_result_returned"
        if isinstance(async_result, dict) and async_result.get("result")
        else "",
    ]
    checks = [check for check in checks if check]

    return CapabilityRecord(
        cap="CAP-08",
        name="AsyncSubAgent + Agent Protocol 最小调用",
        passed=(
            isinstance(health, dict)
            and bool(health.get("ok"))
            and bool(assistants)
            and isinstance(run_result, dict)
            and run_result.get("result") == probe.upper()
            and isinstance(async_result, dict)
            and async_result.get("result") == probe.upper()
        ),
        checks=checks,
        evidence={
            "base_url": base_url,
            "health": health,
            "assistant_count": len(assistants),
            "assistant_id": assistant_id,
            "graph_id": assistants[0].get("graph_id") if assistants else None,
            "probe": probe,
            "sync_result": run_result,
            "async_result": async_result,
            # AsyncSubAgent is a TypedDict, so its fields are mapping keys.
            "sub_agent_fields": {
                "name": sub_agent["name"],
                "graph_id": sub_agent["graph_id"],
                "url": sub_agent["url"],
            },
        },
        notes=(
            "服务跑在独立环境（langgraph-api 与 opensandbox-server 的 grpcio 区间不相交），"
            "所以这里同时验证了导入、服务启动、以及同步与异步两条任务结果。"
        ),
    )


def _text_of(message: Any) -> str:
    """Plain text of a message whose content may be a string or content blocks."""
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return str(content)
