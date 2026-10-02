"""Development smoke test: one real read-only query through the real model.

This is not part of the gate. ``tests/acceptance/test_t11.py`` proves the behaviour with a
declared model double; this script exists to see the *real* model drive the real stack at
least once, because a scripted model cannot tell you whether the prompt actually works.

What it does:

1. starts the packaged Java ERP and the MCP gateway against a throwaway database;
2. builds the main agent with the configured model and the gateway's own tools;
3. asks one read-only question and prints the whole trace — tool calls, todo list, answer;
4. fails if the trace contains no MCP call (a plausible-sounding answer with no tool call
   is exactly the failure mode this smoke test is for).

No sandbox is started: the question is answerable from the ERP alone, so the filesystem
backend is a stub that refuses to do anything. That refusal is deliberate — if the model
tries to write a file here, the trace shows it and the run fails loudly.

Usage:
    python scripts/smoke_agent.py
"""

from __future__ import annotations

import asyncio
import ast
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(REPO_ROOT / "src"), str(REPO_ROOT / "tests")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# The Windows console defaults to a legacy code page, which cannot represent the model's
# output. Reporting is the whole point of this script, so it must not die on a dash.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from langchain_core.callbacks import BaseCallbackHandler  # noqa: E402

from fixtures import erp_service, loader, mcp_service  # noqa: E402

from agent.config import ModelConfig  # noqa: E402
from agent.main_agent import build_main_agent, build_virtual_backend  # noqa: E402
from agent.memory.prompts import load_runtime_rules  # noqa: E402
from agent.middleware_config import (  # noqa: E402
    build_middlewares,
    middleware_inventory,
)
from agent.subagents.loader import load_all  # noqa: E402
from mcp_server.tools.registry import ERP_TOOLS, ERP_WRITE_TOOLS  # noqa: E402

# Deliberately two-step: a single lookup would not show whether the main agent plans,
# delegates and aggregates, which is what this task is about. Still entirely read-only.
QUESTION = (
    "请先看哪些物料需要补货，然后挑出库存最紧张的一项，"
    "说明它的供应商是谁、当前库存和建议补货量。只做只读查询，不要下单。"
)

#: A tool call on one of these proves the answer came from the ERP rather than the model.
READ_TOOLS = tuple(name for name in ERP_TOOLS if name not in set(ERP_WRITE_TOOLS))


class StubSandbox:
    """Refuses every filesystem operation, on purpose."""

    def __getattr__(self, name: str):
        def _refuse(*args: Any, **kwargs: Any):
            raise AssertionError(
                f"the smoke query should not touch the filesystem, but called sandbox.{name}"
            )

        return _refuse


async def main() -> int:
    model_config = ModelConfig.from_env()
    print(f"model: {model_config.model_id} @ {model_config.base_url}")

    configs = load_all(available_tools=ERP_TOOLS, write_tools=ERP_WRITE_TOOLS)
    print(f"sub-agents: {sorted(configs)}")

    with tempfile.TemporaryDirectory(prefix="smoke-agent-") as workdir:
        run_dir = Path(workdir)
        with erp_service.running_erp(run_dir / "erp", seed_path=loader.SEED_PATH) as erp:
            print(f"erp: {erp.base_url}")
            with mcp_service.running_gateway(
                run_dir / "gateway",
                erp_base_url=erp.base_url,
                erp_token=erp.token,
            ) as gateway:
                print(f"gateway: {gateway.mcp_url}")
                return await run_query(model_config, configs, gateway)


def _parse_arguments(raw: Any) -> dict:
    """Recover a tool call's arguments from whatever the framework handed us.

    Tool inputs arrive as JSON, as a Python literal, or already as a dict depending on the
    call path; the report is only useful if the arguments are readable either way.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        for parse in (json.loads, ast.literal_eval):
            try:
                parsed = parse(raw)
            except (ValueError, SyntaxError):
                continue
            if isinstance(parsed, dict):
                return parsed
    return {"raw": str(raw)}


class TraceRecorder(BaseCallbackHandler):
    """Records every tool call, including those made inside a delegated sub-agent.

    Sub-agent tool calls do not appear in the parent graph's message state — the sub-agent
    runs inside the ``task`` tool — so callbacks are the only place the whole trace is
    visible. Without this the smoke test could not tell a real ERP lookup from an invented
    answer.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.delegations: list[tuple[str, dict]] = []

    def on_tool_start(self, serialized: dict, input_str: str, **kwargs: Any) -> None:
        name = (serialized or {}).get("name") or kwargs.get("name") or "?"
        arguments = _parse_arguments(input_str)
        self.calls.append((name, arguments))
        if name == "task":
            self.delegations.append((name, arguments))


async def run_query(model_config: ModelConfig, configs: dict, gateway: Any) -> int:
    from langchain_core.messages import AIMessage
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient(
        {
            "erp": {
                "url": gateway.mcp_url,
                "transport": "streamable_http",
                "headers": {"x-actor-id": "demo-a"},
            }
        }
    )
    tools = await client.get_tools()
    print(f"gateway tools available: {sorted(tool.name for tool in tools)}")

    assembly = build_main_agent(
        model=model_config.create_chat_model(),
        tools=tools,
        write_tools=ERP_WRITE_TOOLS,
        backend=build_virtual_backend(
            sandbox_backend=StubSandbox(), store=None, owner_user_id="demo-a"
        ),
        owner_user_id="demo-a",
        subagents=configs,
        preferences={"currency": "CNY", "language": "zh-CN"},
        middleware=build_middlewares({}, only={"todo_list"}),
        middleware_inventory=middleware_inventory({}),
    )

    print(f"\nmain agent tools: {list(assembly.tool_names) or '(none beyond the built-ins)'}")
    print(f"delegated to sub-agents: {list(assembly.delegated_tools)}")

    recorder = TraceRecorder()
    state = await assembly.graph.ainvoke(
        {"messages": [{"role": "user", "content": QUESTION}]}, config={"callbacks": [recorder]}
    )

    print("\n=== rule file in play ===")
    print(f"  {load_runtime_rules().splitlines()[0]}")

    print("\n=== tool trace (including inside sub-agents) ===")
    for name, arguments in recorder.calls:
        print(f"  -> {name}({json.dumps(arguments, ensure_ascii=False)[:110]})")

    todos = state.get("todos") or []
    print(f"\n=== todos ({len(todos)}) ===")
    for todo in todos:
        print(f"  [{todo['status']}] {todo['content']}")

    print("\n=== answer ===")
    final = [
        m for m in state["messages"] if isinstance(m, AIMessage) and m.content and not m.tool_calls
    ]
    print(f"  {final[-1].content if final else '(no final text)'}")

    used_mcp = [name for name, _ in recorder.calls if name in READ_TOOLS]
    print("\n=== verdict ===")
    print(f"  MCP read calls: {used_mcp}")
    print(f"  delegations:    {[args.get('subagent_type') for _, args in recorder.delegations]}")
    print(f"  todos written:  {len(todos)}")

    problems = []
    if not used_mcp:
        problems.append("trace 里没有 MCP 读调用，回答可能是模型编造的")
    if not recorder.delegations:
        problems.append("主 Agent 没有用 task 委派，说明分工规则没生效")
    if not todos:
        problems.append("主 Agent 没有写 todo 清单")

    if problems:
        for problem in problems:
            print(f"  FAIL: {problem}")
        return 1

    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
