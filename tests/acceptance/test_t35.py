"""Durable archive evidence, independent from a lossy summary model's answer."""

import asyncio
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from acceptance import test_t19 as limits_case  # noqa: E402
from acceptance.test_t11 import ScriptedChatModel, tool_call  # noqa: E402
from agent.main_agent import build_main_agent, build_virtual_backend  # noqa: E402
from agent.middlewares.conversation_archive import (  # noqa: E402
    ArchivedCompactionMiddleware,
    ArchivedSummarizationMiddleware,
    ArchivePersistenceError,
)
from agent.middlewares.tools_summarization import archive_thread_history  # noqa: E402
from agent.persistence.scoped_store import UserScopedStore  # noqa: E402
from fixtures import sandbox_service  # noqa: E402

settings = limits_case.settings
resources = limits_case.resources
pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def pool(settings):
    with sandbox_service.running_pool(settings, warm_pool_size=0) as (manager, _):
        yield manager


class SummaryObserver(ScriptedChatModel):
    verify_archive: Any

    def _generate(self, *args, **kwargs):
        self.verify_archive()
        return super()._generate(*args, **kwargs)


def archive_items(resources, thread, owner="demo-a"):
    return [item for item in limits_case.scoped(resources, owner).search(("memories", owner), limit=1000)
            if item.key.startswith(f"archive/{thread}/")]


def setup_graph(resources, pool, *, active=False, owner="demo-a"):
    thread = uuid.uuid4().hex
    scoped = limits_case.scoped(resources, owner)
    backend = build_virtual_backend(sandbox_backend=pool.get_or_create(owner),
                                    store=scoped, owner_user_id=owner)
    messages = []
    for index in range(4):
        messages.extend([HumanMessage(content=f"original-{index}", id=f"user-{index}"),
                         AIMessage(content=f"reply-{index}", id=f"assistant-{index}")])
    messages.append(HumanMessage(content="final original question", id="last-question"))
    todos = [{"content": "unfinished order", "status": "pending"}]
    expected_contents = [message.content for message in messages]

    def verify_archive():
        items = archive_items(resources, thread, owner)
        assert items, "the summary model must observe the persisted archive before generating"
        archived = items[-1].value
        assert [message["content"] for message in archived["messages"][:len(expected_contents)]] == expected_contents
        assert archived["preserved_state"]["todos"] == todos
        assert archived["run_id"] == "measured-run"

    summary_model = SummaryObserver(script=[AIMessage(content="lossy summary")], verify_archive=verify_archive)
    engine = ArchivedSummarizationMiddleware(summary_model, backend=backend,
        trigger=("messages", 8), keep=("messages", 2), truncate_args_settings=None)
    normal_model = ScriptedChatModel(script=[tool_call("compact_conversation", {}, "compact-1"),
        AIMessage(content="complete")] if active else [AIMessage(content="complete")])
    middleware = [ArchivedCompactionMiddleware(engine)] if active else [engine]
    from langchain.agents.middleware import TodoListMiddleware
    graph = build_main_agent(model=normal_model, backend=backend,
        tools=[], write_tools=set(), owner_user_id=owner, subagents={},
        middleware=[TodoListMiddleware(), *middleware],
        checkpointer=resources.checkpointer, store=scoped).graph
    config = {"configurable": {"thread_id": thread, "owner_user_id": owner, "application_run_id": "measured-run"}}
    # The installed SDK marks todos OmitFromInput. Seed through its real planning tool.
    planned_script = list(normal_model.script)
    normal_model.script = [tool_call("write_todos", {"todos": todos}, "plan-1"),
                           AIMessage(content="ready")]
    graph.invoke({"messages": [HumanMessage(content="prepare todos", id="plan-question")]}, config)
    expected_contents = [message.content for message in graph.get_state(config).values["messages"]] + expected_contents
    normal_model.script = planned_script
    return graph, config, {"messages": messages, "todos": todos}, summary_model, thread


@pytest.mark.parametrize("active", [False, True], ids=["automatic", "compact-tool"])
@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
def test_real_sdk_archives_full_state_before_summary_generation(resources, pool, active, asynchronous):
    graph, config, payload, model, thread = setup_graph(resources, pool, active=active)
    result = asyncio.run(graph.ainvoke(payload, config)) if asynchronous else graph.invoke(payload, config)
    assert model.contexts, "must actually generate a summary, not just archive directly"
    assert result["todos"] == payload["todos"]
    assert graph.get_state(config).values.get("_summarization_event")
    assert archive_items(resources, thread)
    assert archive_items(resources, thread, "demo-b") == []


@pytest.mark.parametrize("active", [False, True], ids=["automatic", "compact-tool"])
def test_archive_failure_refuses_summary_and_compaction(resources, pool, monkeypatch, active):
    graph, config, payload, model, thread = setup_graph(resources, pool, active=active)
    original = UserScopedStore.put

    def fail_archive(self, namespace, key, value, *args, **kwargs):
        if key.startswith(f"archive/{thread}/"):
            raise OSError("injected archive write failure")
        return original(self, namespace, key, value, *args, **kwargs)

    monkeypatch.setattr(UserScopedStore, "put", fail_archive)
    if active:
        result = asyncio.run(graph.ainvoke(payload, config))
        assert any(isinstance(message, ToolMessage) and "Compaction failed" in str(message.content)
                   for message in result["messages"])
    else:
        with pytest.raises(ArchivePersistenceError):
            asyncio.run(graph.ainvoke(payload, config))
    assert model.contexts == []
    snapshot = graph.get_state(config).values
    assert not snapshot.get("_summarization_event")
    assert snapshot["todos"] == payload["todos"]


def test_multiple_archives_of_one_run_cannot_overwrite_each_other(resources):
    scoped = limits_case.scoped(resources)
    thread = uuid.uuid4().hex
    first = archive_thread_history(scoped, thread_id=thread, run_id="same-run",
                                   messages=[HumanMessage(content="first")])
    second = archive_thread_history(scoped, thread_id=thread, run_id="same-run",
                                    messages=[HumanMessage(content="second")])
    assert first.key != second.key
    assert scoped.get(("memories", "demo-a"), first.key).value["messages"][0]["content"] == "first"


def test_archives_preserve_tool_arguments_pairing_and_metadata(resources):
    scoped = limits_case.scoped(resources)
    record = archive_thread_history(scoped, thread_id=uuid.uuid4().hex, messages=[
        AIMessage(content="lookup", tool_calls=[{"name": "supplier_query", "args": {"q": "S001"},
                                                 "id": "lookup-1", "type": "tool_call"}]),
        ToolMessage(content="S001 exists", tool_call_id="lookup-1", name="supplier_query",
                    status="success", artifact={"source": "ERP"})])
    messages = scoped.get(("memories", "demo-a"), record.key).value["messages"]
    assert messages[0]["tool_calls"][0]["args"] == {"q": "S001"}
    assert messages[1]["tool_call_id"] == "lookup-1"
    assert messages[1]["artifact"] == {"source": "ERP"}
