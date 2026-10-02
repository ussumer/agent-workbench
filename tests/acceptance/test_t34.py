"""Initialization failures must settle real Mongo runs and the HTTP event stream."""

import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from deepagents import create_deep_agent

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from acceptance import test_t13 as chat_case  # noqa: E402
from acceptance.test_t11 import ScriptedChatModel  # noqa: E402
from agent.middlewares.tools_summarization import BudgetConfig  # noqa: E402

settings = chat_case.settings
resources = chat_case.resources
approval_service = chat_case.approval_service
pytestmark = pytest.mark.integration


@pytest.fixture()
def runtime(resources, approval_service):
    model = ScriptedChatModel(script=[])
    graph = create_deep_agent(model=model, checkpointer=resources.checkpointer,
                              store=resources.store)
    with chat_case.build_client(resources, approval_service, graph) as client:
        client.headers["X-Demo-User"] = "demo-a"
        yield SimpleNamespace(client=client, graph=graph, model=model,
                              context=client.app.state.context, database=resources.database)


def body():
    return {"thread_id": uuid.uuid4().hex, "request_id": uuid.uuid4().hex, "message": "你好"}


def terminal(events, expected):
    done = [event for name, event in events if name == "done"]
    assert len(done) == 1, events
    assert done[0]["payload"]["status"] == expected
    assert sum(name == "run_started" for name, _ in events) == 1
    return done[0]


def broken_provider(owner):
    raise RuntimeError("injected graph initialization failure")


def test_provider_failure_has_one_error_and_failed_done(runtime):
    runtime.context.graph_provider = broken_provider
    status, events = chat_case.sse_events(runtime.client, **body())
    assert status == 200
    terminal(events, "failed")
    assert sum(name == "error" for name, _ in events) == 1
    assert runtime.model.contexts == []


def test_failed_initialization_persists_status_and_releases_thread(runtime):
    runtime.context.graph_provider = broken_provider
    request = body()
    chat_case.sse_events(runtime.client, **request)
    run = runtime.database["runs"].find_one({"thread_id": request["thread_id"]})
    assert run["status"] == "failed"
    assert run["budget_usage"]["counts"] == {"model": 0, "tool": 0}
    assert runtime.context.registry.snapshot()["runs"] == []


def test_failed_request_replay_does_not_reinitialize_graph(runtime):
    calls = []

    def provider(owner):
        calls.append(owner)
        return broken_provider(owner)

    runtime.context.graph_provider = provider
    request = body()
    chat_case.sse_events(runtime.client, **request)
    response = runtime.client.post("/api/chat/stream", json=request)
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "failed"
    assert response.json()["data"]["replayed"] is True
    assert calls == ["demo-a"]


def test_new_request_can_run_after_initialization_failure(runtime):
    runtime.context.graph_provider = broken_provider
    request = body()
    chat_case.sse_events(runtime.client, **request)
    runtime.context.graph_provider = lambda owner: runtime.graph
    request["request_id"] = uuid.uuid4().hex
    status, events = chat_case.sse_events(runtime.client, **request)
    assert status == 200
    terminal(events, "completed")
    records = list(runtime.database["runs"].find({"thread_id": request["thread_id"]}))
    assert sorted(record["status"] for record in records) == ["completed", "failed"]


def test_slow_initialization_is_inside_the_run_deadline(runtime):
    finished = threading.Event()

    def provider(owner):
        time.sleep(0.8)
        finished.set()
        return runtime.graph

    runtime.context.graph_provider = provider
    runtime.context.budget_config = BudgetConfig(run_seconds=0.1)
    started = time.monotonic()
    _, events = chat_case.sse_events(runtime.client, **body())
    elapsed = time.monotonic() - started
    terminal(events, "failed")
    assert elapsed < 0.6
    assert any(event["payload"]["code"] == "RUN_TIME_BUDGET_EXCEEDED"
               for name, event in events if name == "error")
    assert finished.wait(2)
    assert runtime.model.contexts == []
    assert runtime.context.registry.snapshot()["runs"] == []


def test_resume_initialization_failure_still_emits_failed_done(runtime):
    request = body()
    runtime.context.repository.ensure_thread(owner_user_id="demo-a",
        thread_id=request["thread_id"], title="resume failure")
    runtime.context.graph_provider = broken_provider
    response = runtime.client.post(f"/api/chat/{request['thread_id']}/resume", json={
        "request_id": request["request_id"], "interrupt_id": "missing-checkpoint",
        "resume": {"supplement": "补充"}})
    # Use the same SSE decoder as ordinary requests, without a scripted service.
    import json
    events = []
    name = None
    for line in response.text.splitlines():
        if line.startswith("event:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            events.append((name, json.loads(line.split(":", 1)[1])))
    assert response.status_code == 200
    terminal(events, "failed")


def test_cancel_can_reach_a_run_waiting_for_initialization(runtime):
    entered = threading.Event()
    release = threading.Event()

    def provider(owner):
        entered.set()
        assert release.wait(2)
        return runtime.graph

    runtime.context.graph_provider = provider
    request = body()
    # The timer also guarantees that an old blocking implementation cannot hang this test.
    timer = threading.Timer(0.8, release.set)
    timer.start()
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(chat_case.sse_events, runtime.client, **request)
            assert entered.wait(2)
            started = time.monotonic()
            response = runtime.client.post(f"/api/chat/{request['thread_id']}/cancel")
            elapsed = time.monotonic() - started
            _, events = pending.result(timeout=3)
            assert not release.is_set(), "cancel must settle before the initializer returns"
        assert response.json()["data"]["cancel_requested"] is True
        assert elapsed < 0.5
        terminal(events, "cancelled")
        assert runtime.model.contexts == []
        record = runtime.database["runs"].find_one({"thread_id": request["thread_id"]})
        assert record["status"] == "cancelled"
        assert runtime.context.registry.snapshot()["runs"] == []
    finally:
        release.set()
        timer.cancel()
