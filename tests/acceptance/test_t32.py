"""Budget assertions follow real HTTP runs, Mongo counters and delegated SDK calls."""

import asyncio
import sys
import time
import uuid
from pathlib import Path

from deepagents import create_deep_agent
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from acceptance import test_t29 as memory_case  # noqa: E402
from acceptance.test_t11 import ScriptedChatModel  # noqa: E402
from acceptance.test_t13 import sse_events  # noqa: E402
from agent.main_agent import build_virtual_backend  # noqa: E402
from agent.memory.preferences import load_preferences  # noqa: E402
from agent.middlewares.tools_summarization import BudgetConfig  # noqa: E402

runtime = memory_case.runtime
services = memory_case.services
query = memory_case.query
send = memory_case.send


def configure(runtime, **kwargs):
    runtime.client.app.state.context.budget_config = BudgetConfig(**kwargs)


def errors(events):
    return [event["payload"] for name, event in events if name == "error"]


def turn(runtime, thread, message):
    status, events = sse_events(runtime.client, request_id=uuid.uuid4().hex,
                                thread_id=thread, message=message)
    assert status == 200
    done = [event["payload"] for name, event in events if name == "done"]
    assert len(done) == 1
    return done[0], events


def test_main_and_child_model_calls_use_one_run_budget(runtime, services):
    configure(runtime, model_calls_per_run=3)
    done, events = query(runtime)
    assert done["status"] == "failed"
    assert any(error["code"] == "MODEL_RUN_BUDGET_EXCEEDED" for error in errors(events))
    assert len(runtime.models["demo-a"].contexts) == 1
    assert len(runtime.analysts["demo-a"].contexts) == 2
    run_id = next(event["run_id"] for name, event in events if name == "done")
    record = services.resources.database["runs"].find_one({"run_id": run_id})
    assert record["budget_usage"]["counts"]["model"] == 3
    assert record["budget_usage"]["failure_code"] == "MODEL_RUN_BUDGET_EXCEEDED"
    assert load_preferences(runtime.scoped("demo-a")).history["recent_queries"] == []


def test_main_task_and_child_erp_tools_use_one_run_budget(runtime):
    configure(runtime, tool_calls_per_run=1)
    done, events = query(runtime)
    assert done["status"] == "failed"
    assert any(error["code"] == "TOOL_RUN_BUDGET_EXCEEDED" for error in errors(events))
    assert len(runtime.models["demo-a"].contexts) == 1
    assert load_preferences(runtime.scoped("demo-a")).history["recent_supplier_ids"] == []


def test_thread_model_budget_is_cumulative_across_api_runs(runtime, services):
    configure(runtime, model_calls_per_run=5, model_calls_per_thread=1)
    runtime.client.headers["X-Demo-User"] = "demo-a"
    thread = uuid.uuid4().hex
    first, _ = turn(runtime, thread, "你好")
    second, events = turn(runtime, thread, "再问一次")
    assert first["status"] == "completed"
    assert second["status"] == "failed"
    assert any(error["code"] == "MODEL_THREAD_BUDGET_EXCEEDED" for error in errors(events))
    record = services.resources.database["threads"].find_one({"thread_id": thread})
    assert record["budget_usage"]["model"] == 1
    assert len(runtime.models["demo-a"].contexts) == 1


def test_run_budget_resets_on_a_new_api_invocation(runtime):
    configure(runtime, model_calls_per_run=1)
    assert send(runtime, "你好")[0]["status"] == "completed"
    assert send(runtime, "你好")[0]["status"] == "completed"
    assert len(runtime.models["demo-a"].contexts) == 2


def test_owner_b_does_not_spend_owner_a_thread_budget(runtime, services):
    configure(runtime, model_calls_per_run=1, model_calls_per_thread=1)
    for owner in ("demo-a", "demo-b"):
        runtime.client.headers["X-Demo-User"] = owner
        thread = uuid.uuid4().hex
        assert turn(runtime, thread, "你好")[0]["status"] == "completed"
        record = services.resources.database["threads"].find_one({"owner_user_id": owner, "thread_id": thread})
        assert record["budget_usage"]["model"] == 1


def test_waiting_model_is_stopped_by_the_total_run_deadline(runtime, services, monkeypatch):
    async def delayed_stream(self, messages, **kwargs):
        await asyncio.sleep(1.0)
        self.contexts.append(list(messages))
        yield ChatGenerationChunk(message=AIMessageChunk(content="完成"))

    monkeypatch.setattr(ScriptedChatModel, "_astream", delayed_stream)
    # Exercise the deadline against an actual SDK graph without synchronously uploading
    # presets first. The other cases exercise the full main/subagent assembly.
    scoped = runtime.scoped("demo-a")
    backend = build_virtual_backend(sandbox_backend=services.manager.get_or_create("demo-a"),
                                    store=scoped, owner_user_id="demo-a")
    graph = create_deep_agent(model=runtime.models["demo-a"], backend=backend,
                              checkpointer=services.resources.checkpointer, store=scoped)
    runtime.client.app.state.context.graph_provider = lambda owner: graph
    configure(runtime, run_seconds=0.1)
    started = time.monotonic()
    done, events = send(runtime, "你好")
    assert time.monotonic() - started < 0.9
    assert done["status"] == "failed"
    assert any(error["code"] == "RUN_TIME_BUDGET_EXCEEDED" for error in errors(events))
    assert runtime.models["demo-a"].contexts == []
