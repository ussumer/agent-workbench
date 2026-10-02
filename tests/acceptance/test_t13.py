"""T13 acceptance: SSE, history and recovery.

Two kinds of evidence, deliberately kept apart.

The **adapter** is tested against the real chunk shapes T06 captured
(``tests/fixtures/stream/t06-v2-stream.json``): tool arguments that arrive one character at
a time, structured content blocks, a namespace that must be resolved to a sub-agent *name*.
These are the cases where a plausible implementation quietly produces wrong output rather
than an exception.

The **HTTP surface** is tested against a real MongoDB (display messages, runs, pending
actions, checkpoints all really persist) and, for one case, a real ``create_deep_agent``
graph so the adapter is exercised against the framework's own stream rather than a
description of it. The scripted pieces are the model doubles the task allows; no assertion
is made against a stub standing in for a service.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
for _path in (str(SRC_DIR), str(REPO_ROOT / "tests")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from fixtures import loader, mongo_service  # noqa: E402

from agent.approval.models import PendingStatus  # noqa: E402
from agent.approval.service import ApprovalService  # noqa: E402
from agent.approval.store import MongoPendingActionStore  # noqa: E402
from agent.artifacts.service import ArtifactService  # noqa: E402
from agent.artifacts.store import MongoArtifactStore  # noqa: E402
from agent.async_tasks.service import build_async_task_service  # noqa: E402
from agent.persistence.repository import ApplicationRepository  # noqa: E402
from api_view.api.deps import SESSION_COOKIE, WebContext  # noqa: E402
from api_view.run_registry import RunRegistry, ThreadBusy  # noqa: E402
from api_view.stream_adapter import StreamAdapter, text_of  # noqa: E402
from api_view.web_main import create_app  # noqa: E402

pytestmark = pytest.mark.integration

# The T06 capture lives with the test fixtures, not with the shared business fixtures.
STREAM_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "stream" / "t06-v2-stream.json"


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def settings():
    candidate = mongo_service.unique_settings(f"t13-{uuid.uuid4().hex[:8]}")
    mongo_service.require_reachable(candidate)
    mongo_service.drop_test_database(candidate)
    yield candidate
    mongo_service.drop_test_database(candidate)


@pytest.fixture(scope="module")
def resources(settings):
    started = mongo_service.start_resources(settings)
    try:
        yield started
    finally:
        started.close()


@pytest.fixture()
def approval_service(resources) -> ApprovalService:
    return ApprovalService(
        store=MongoPendingActionStore(resources.database), grant_secret="t13-grant-secret"
    )


# --------------------------------------------------------------------------- #
# a scripted agent
# --------------------------------------------------------------------------- #


def _chunk(call_id: str | None, name: str | None, args: str):
    from langchain_core.messages import AIMessageChunk

    return AIMessageChunk(
        content="",
        tool_call_chunks=[
            {
                "name": name,
                "args": args,
                "id": call_id,
                "index": 0,
                "type": "tool_call_chunk",
            }
        ],
    )


def _text(text: str, message_id: str = "m1"):
    from langchain_core.messages import AIMessage

    return AIMessage(content=text, id=message_id)


def _tool_result(call_id: str, body: str, name: str = "part_query"):
    from langchain_core.messages import ToolMessage

    return ToolMessage(content=body, tool_call_id=call_id, name=name)


class ScriptedAgent:
    """A graph double emitting the framework's v2 part shapes.

    Declared as a model double: it decides the *control flow* of a turn (which parts arrive
    in which order) while every service behind the HTTP layer stays real. ``interrupt`` makes
    it park like the framework does, so resume and state take the real code path.
    """

    def __init__(self, *, interrupt: bool = False, fail: bool = False) -> None:
        self.interrupt = interrupt
        self.fail = fail
        self.state: dict[str, Any] = {"messages": [], "todos": []}
        self.payloads: list[Any] = []
        self.configs: list[dict] = []
        self.calls = 0

    async def astream(self, payload, config, **kwargs) -> AsyncIterator[dict]:
        assert kwargs.get("version") == "v2"
        assert kwargs.get("subgraph") is True
        assert kwargs.get("stream_mode") == ["messages", "values"]
        self.payloads.append(payload)
        self.configs.append(config)
        self.calls += 1

        yield {"type": "messages", "ns": [], "data": (_text("正在"), {"message_id": "m1"})}
        yield {"type": "messages", "ns": [], "data": (_text("查询"), {"message_id": "m1"})}
        yield {
            "type": "messages",
            "ns": [],
            "data": (_chunk("call-1", "part_query", '{"part_id": "'), {"message_id": "m2"}),
        }
        yield {
            "type": "messages",
            "ns": [],
            "data": (_chunk("call-1", None, 'P001"}'), {"message_id": "m2"}),
        }
        yield {
            "type": "values",
            "ns": [],
            "data": {"todos": [{"content": "查 P001", "status": "in_progress"}]},
        }
        yield {
            "type": "values",
            "ns": [],
            "data": {
                "messages": [
                    _text("查询完成。", "m3"),
                    _tool_result("call-1", json.dumps({"ok": True, "data": {"on_hand": 8}})),
                ],
                "todos": [{"content": "查 P001", "status": "completed"}],
            },
        }

        if self.fail:
            raise RuntimeError("模型服务返回了无法解析的响应")

        if self.interrupt:
            from langgraph.types import Interrupt

            interrupt = Interrupt(
                value={
                    "action_requests": [
                        {
                            "name": "order_create",
                            "args": {
                                "supplier_id": "S001",
                                "currency": "CNY",
                                "lines": [
                                    {"part_id": "P001", "quantity": 50, "unit_price": "25.50"}
                                ],
                            },
                            "description": "requires approval",
                        }
                    ],
                    "review_configs": [
                        {"action_name": "order_create", "allowed_decisions": ["approve", "reject"]}
                    ],
                },
                id="int-1",
            )
            self.state = {
                "messages": [*self.state.get("messages", []), _text("等待确认。", "m4")],
                "todos": self.state.get("todos", []),
                "__interrupt__": (interrupt,),
            }
            yield {"type": "values", "ns": [], "data": self.state, "interrupts": (interrupt,)}
            return

        self.state = {
            "messages": [
                _text("正在查询完成。", "m1"),
                _tool_result("call-1", json.dumps({"ok": True, "data": {"on_hand": 8}})),
            ],
            "todos": [{"status": "completed", "content": "查 P001"}],
        }

    def get_state(self, config):
        tasks = ()
        interrupts = self.state.get("__interrupt__")
        if interrupts:
            tasks = (type("T", (), {"interrupts": interrupts})(),)
        return type("S", (), {"values": self.state, "tasks": tasks})()


@pytest.fixture()
def scripted() -> ScriptedAgent:
    return ScriptedAgent()


def build_client(resources, approvals, graph, *, token: str = "t13-internal"):
    context = WebContext(
        resources=resources,
        repository=ApplicationRepository(resources.database),
        registry=RunRegistry(),
        approvals=approvals,
        artifacts=ArtifactService(store=MongoArtifactStore(resources.database)),
        # Never called here: these tests do not launch background work, and the service is
        # only built so the context is complete.
        async_tasks=build_async_task_service(resources.database),
        settings=resources.settings,
        graph_provider=lambda owner: graph,
        internal_service_token=token,
    )
    app = create_app(context=context)
    from starlette.testclient import TestClient

    return TestClient(app)


@pytest.fixture()
def client(resources, approval_service, scripted):
    with build_client(resources, approval_service, scripted) as test_client:
        test_client.post("/api/demo/session", json={"user_id": "demo-a"})
        yield test_client


def sse_events(client, **body) -> tuple[int, list[tuple[str, dict]]]:
    """POST a stream request and return ``(status, [(event, envelope)])``."""
    events: list[tuple[str, dict]] = []
    with client.stream("POST", "/api/chat/stream", json=body) as response:
        status = response.status_code
        if status != 200:
            return status, []
        name = None
        for line in response.iter_lines():
            if line.startswith("event:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and name:
                events.append((name, json.loads(line.split(":", 1)[1].strip())))
    return status, events


def turn(client, *, message: str = "查一下 P001 库存", request_id: str | None = None):
    return sse_events(
        client, request_id=request_id or f"req-{uuid.uuid4().hex[:10]}", message=message
    )


def only_thread(client) -> str:
    items = client.get("/api/history").json()["data"]["items"]
    assert items, "expected at least one thread"
    return items[0]["thread_id"]


# --------------------------------------------------------------------------- #
# adapter: the shapes the framework really produces
# --------------------------------------------------------------------------- #


def test_tool_arguments_are_reassembled_from_single_character_fragments():
    """The T06 capture: ``{"a": 17, "b": 25}`` arrives as 13 fragments."""
    captured = json.loads(STREAM_FIXTURE.read_text(encoding="utf-8"))
    fragments = captured["tool_arg_fragments"]
    assert len(fragments) > 5, "fixture should carry the fragmented form"

    adapter = StreamAdapter(thread_id="t", run_id="r")
    events = []
    for fragment in fragments:
        events.extend(
            adapter.consume(
                {"type": "messages", "ns": [], "data": (_chunk("c1", "add", fragment), {})}
            )
        )

    deltas = "".join(e.payload["delta"] for e in events if e.event == "tool_args")
    assert deltas == captured["concatenated_tool_args"]
    assert json.loads(deltas) == {"a": 17, "b": 25}
    assert [e.event for e in events].count("tool_start") == 1


def test_a_partial_fragment_does_not_becomes_a_stream_error():
    """Half a JSON object is the normal state mid-stream, not a failure."""
    adapter = StreamAdapter(thread_id="t", run_id="r")

    events = list(
        adapter.consume(
            {"type": "messages", "ns": [], "data": (_chunk("c1", "part_query", '{"part_id": "P'), {})}
        )
    )

    assert [event.event for event in events] == ["tool_start", "tool_args"]
    assert adapter.unknown_types == {}
    assert adapter.terminal_status is None


def test_structured_content_is_read_as_blocks_not_stringified():
    """``str(content)`` on a block list prints Python repr — a bug that looks like success."""
    blocks = [
        {"type": "text", "text": "P001 库存 8"},
        {"type": "reasoning", "text": "内部推理不应展示"},
    ]

    assert text_of(blocks) == "P001 库存 8"
    assert "reasoning" not in text_of(blocks)
    assert text_of({"type": "text", "text": "plain"}) == "plain"
    assert text_of("already a string") == "already a string"
    assert text_of(None) == ""


def test_a_sub_agents_output_is_attributed_by_name_not_by_its_text():
    """``tools:<task-id>`` is an id; the name comes from the ``task`` call's own arguments."""
    adapter = StreamAdapter(thread_id="t", run_id="r")

    # The parent delegates: the task call's arguments name the sub-agent. The fragments are
    # deliberately split so the name is only known once the JSON is complete.
    for fragment in ('{"description": "查库存", ', '"subagent_type": "procurement-analyst"}'):
        list(
            adapter.consume(
                {"type": "messages", "ns": [], "data": (_chunk("task-1", "task", fragment), {})}
            )
        )

    events = list(
        adapter.consume(
            {
                "type": "messages",
                "ns": ["tools:task-1"],
                "data": (_text("P001 库存不足", "sub-1"), {}),
            }
        )
    )

    assert events and events[0].event == "token"
    assert events[0].source == "procurement-analyst"


def test_an_unresolved_task_namespace_does_not_pretend_to_be_a_name():
    adapter = StreamAdapter(thread_id="t", run_id="r")

    events = list(
        adapter.consume(
            {"type": "messages", "ns": ["tools:never-seen"], "data": (_text("hi", "x"), {})}
        )
    )

    assert events[0].source == "sub-agent"


def test_an_unknown_part_type_is_recorded_rather_than_swallowed():
    """A framework upgrade should show up as evidence, not as events that stopped arriving."""
    adapter = StreamAdapter(thread_id="t", run_id="r")

    assert list(adapter.consume({"type": "quantum", "ns": [], "data": {}})) == []
    assert adapter.unknown_types == {"quantum": 1}


def test_exactly_one_done_is_emitted_per_run():
    adapter = StreamAdapter(thread_id="t", run_id="r")
    adapter.run_started()

    first = adapter.finish(status="completed")
    second = adapter.finish(status="completed")

    assert first is not None and second is None
    assert adapter.done_emitted is True


def test_a_run_that_errored_is_never_reported_as_completed():
    adapter = StreamAdapter(thread_id="t", run_id="r")
    adapter.error(code="MODEL_ERROR", message="boom")

    done = adapter.finish(status="completed")

    assert done is not None
    assert done.payload["status"] == "failed"
    assert done.payload["interrupted"] is False


def test_a_run_that_stopped_without_a_signal_is_reported_as_failed():
    """The absence of further tokens is not evidence of success."""
    adapter = StreamAdapter(thread_id="t", run_id="r")

    done = adapter.finish()

    assert done is not None
    assert done.payload["status"] == "failed"


def test_an_interrupted_run_is_reported_as_interrupted_not_completed():
    adapter = StreamAdapter(thread_id="t", run_id="r")
    interrupt = _interrupt("int-9")

    events = list(
        adapter.consume({"type": "values", "ns": [], "data": {}, "interrupts": (interrupt,)})
    )
    done = adapter.finish()

    assert [e.event for e in events] == ["interrupt"]
    assert events[0].payload["interrupt_type"] == "hitl_approval"
    assert done is not None
    assert done.payload["status"] == "interrupted"
    assert done.payload["interrupted"] is True


def test_the_two_interrupt_layers_are_told_apart_by_their_payload():
    from api_view.stream_adapter import describe_interrupt

    kind, _prompt, candidates = describe_interrupt(
        {"missing_fields": ["unit_price"], "question": "请补充单价。"}, "i1"
    )
    assert kind == "order_info_supplement"
    assert candidates[0]["missing_fields"] == ["unit_price"]

    kind, _prompt, candidates = describe_interrupt(
        {"action_requests": [{"name": "order_create", "args": {}, "description": "d"}]}, "i2"
    )
    assert kind == "hitl_approval"
    assert candidates[0]["tool_name"] == "order_create"


def test_a_tool_call_is_started_and_ended_once():
    adapter = StreamAdapter(thread_id="t", run_id="r")

    events = []
    events += list(
        adapter.consume(
            {"type": "messages", "ns": [], "data": (_chunk("c1", "part_query", '{"a":1}'), {})}
        )
    )
    body = json.dumps({"ok": True, "data": {"on_hand": 8}})
    events += list(
        adapter.consume(
            {"type": "values", "ns": [], "data": {"messages": [_tool_result("c1", body)]}}
        )
    )
    # The same result delivered twice must not produce a second tool_end.
    events += list(
        adapter.consume(
            {"type": "values", "ns": [], "data": {"messages": [_tool_result("c1", body)]}}
        )
    )

    names = [e.event for e in events]
    assert names.count("tool_start") == 1
    assert names.count("tool_end") == 1
    assert names.count("tool_result") == 1


def test_an_error_envelope_from_a_tool_becomes_a_failed_tool_result():
    adapter = StreamAdapter(thread_id="t", run_id="r")
    list(
        adapter.consume(
            {"type": "messages", "ns": [], "data": (_chunk("c1", "order_create", "{}"), {})}
        )
    )

    events = list(
        adapter.consume(
            {
                "type": "values",
                "ns": [],
                "data": {
                    "messages": [
                        _tool_result(
                            "c1",
                            json.dumps(
                                {
                                    "ok": False,
                                    "data": None,
                                    "error": {
                                        "code": "APPROVAL_REQUIRED",
                                        "message": "needs approval",
                                        "retryable": False,
                                    },
                                }
                            ),
                        )
                    ]
                },
            }
        )
    )
    result = [e for e in events if e.event == "tool_result"][0]

    assert result.payload["ok"] is False
    assert result.payload["error"]["code"] == "APPROVAL_REQUIRED"


def test_sse_frames_carry_an_id_an_event_name_and_a_json_envelope():
    adapter = StreamAdapter(thread_id="thread-1", run_id="run-1", request_id="req-1")
    frame = adapter.run_started().to_sse()

    assert frame.startswith("id: run-1:1\nevent: run_started\ndata: ")
    assert frame.endswith("\n\n")
    envelope = json.loads(frame.split("data: ", 1)[1].strip())
    assert envelope["v"] == 1
    assert envelope["thread_id"] == "thread-1"
    assert envelope["run_id"] == "run-1"
    assert envelope["seq"] == 1
    assert envelope["source"] == "main"


def _interrupt(interrupt_id: str):
    from langgraph.types import Interrupt

    return Interrupt(
        value={
            "action_requests": [
                {"name": "order_create", "args": {"supplier_id": "S001"}, "description": "d"}
            ],
            "review_configs": [{"action_name": "order_create", "allowed_decisions": ["approve"]}],
        },
        id=interrupt_id,
    )


# --------------------------------------------------------------------------- #
# HTTP surface
# --------------------------------------------------------------------------- #


def test_session_accepts_only_the_two_demo_users(client):
    response = client.post("/api/demo/session", json={"user_id": "demo-b"})
    assert response.status_code == 200
    cookie_header = response.headers.get("set-cookie", "")
    assert SESSION_COOKIE in cookie_header and "HttpOnly" in cookie_header

    assert client.post("/api/demo/session", json={"user_id": "demo-c"}).status_code == 400


def test_a_request_without_a_session_is_refused(resources, approval_service, scripted):
    context = WebContext(
        resources=resources,
        repository=ApplicationRepository(resources.database),
        registry=RunRegistry(),
        approvals=approval_service,
        artifacts=ArtifactService(store=MongoArtifactStore(resources.database)),
        # Never called here: these tests do not launch background work, and the service is
        # only built so the context is complete.
        async_tasks=build_async_task_service(resources.database),
        settings=resources.settings,
        graph_provider=lambda owner: scripted,
        internal_service_token="t13-internal",
    )
    from starlette.testclient import TestClient

    with TestClient(create_app(context=context)) as anonymous:
        response = anonymous.post(
            "/api/chat/stream", json={"request_id": "r1", "message": "你好"}
        )

    assert response.status_code == 401


def test_a_blank_or_overlong_message_is_refused(client):
    assert (
        client.post(
            "/api/chat/stream", json={"request_id": "r-blank", "message": "   "}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/chat/stream", json={"request_id": "r-long", "message": "字" * 20001}
        ).status_code
        == 400
    )


def test_stream_emits_the_contract_events_with_one_done(client):
    status, events = turn(client)
    names = [name for name, _ in events]

    assert status == 200
    assert names[0] == "run_started"
    assert names[-1] == "done"
    assert names.count("done") == 1
    assert {"token", "tool_start", "tool_args", "tool_result", "tool_end", "todos"} <= set(names)

    sources = {envelope["source"] for _, envelope in events}
    assert sources == {"main"}
    seqs = [envelope["seq"] for _, envelope in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)

    done = [envelope for name, envelope in events if name == "done"][0]
    assert done["payload"]["status"] == "completed"
    assert done["payload"]["interrupted"] is False


def test_state_reports_todos_and_the_terminal_status(client):
    turn(client)
    thread = only_thread(client)

    state = client.get(f"/api/chat/{thread}/state").json()["data"]

    assert state["status"] == "completed"
    assert state["todos"] == [{"content": "查 P001", "status": "completed"}]
    assert state["pending_interrupts"] == []
    assert state["run_id"]


def test_the_same_request_id_does_not_start_a_second_run(client, scripted):
    request_id = f"req-{uuid.uuid4().hex[:8]}"
    turn(client, request_id=request_id)
    runs_before = scripted.calls

    status, events = sse_events(
        client, request_id=request_id, thread_id=only_thread(client), message="查一下 P001 库存"
    )

    assert status == 200
    assert events == [], "重发同 request_id 不应再产生事件流"
    assert scripted.calls == runs_before, "重发同 request_id 不应启动新 run"


def test_the_same_request_id_with_a_different_body_conflicts(client):
    request_id = f"req-{uuid.uuid4().hex[:8]}"
    turn(client, request_id=request_id)

    response = client.post(
        "/api/chat/stream",
        json={"thread_id": only_thread(client), "request_id": request_id, "message": "换了内容"},
    )

    assert response.status_code == 409


def test_another_users_thread_is_invisible_for_every_route(client):
    turn(client)
    thread = only_thread(client)

    client.post("/api/demo/session", json={"user_id": "demo-b"})

    assert client.get(f"/api/chat/{thread}/state").status_code == 404
    assert client.get(f"/api/history/{thread}").status_code == 404
    assert client.delete(f"/api/history/{thread}").status_code == 404
    assert client.post(f"/api/chat/{thread}/cancel").status_code == 404
    assert client.get("/api/history").json()["data"]["total"] == 0


def test_delete_removes_the_conversation_and_keeps_everything_else(client):
    turn(client)
    thread = only_thread(client)

    response = client.delete(f"/api/history/{thread}")
    payload = response.json()["data"]

    assert response.status_code == 200
    assert payload["deleted"]["threads"] == 1
    assert payload["deleted"]["display_messages"] == 2
    assert payload["deleted"]["runs"] == 1
    assert payload["deleted"]["checkpoints"] == 1
    # Named explicitly so the claim is checkable rather than implied.
    assert "用户技能" in payload["kept"]
    assert "业务订单与操作记录" in payload["kept"]

    # This thread is gone; other threads in the shared test database are not this test's
    # business, so the assertion is about absence rather than about a global count.
    remaining = {
        item["thread_id"] for item in client.get("/api/history").json()["data"]["items"]
    }
    assert thread not in remaining
    assert client.get(f"/api/history/{thread}").status_code == 404


def test_a_thread_with_an_active_run_cannot_be_deleted(resources, approval_service):
    """The registry owns the run, so this is a refusal rather than a race."""
    agent = ScriptedAgent()
    with build_client(resources, approval_service, agent) as client:
        client.post("/api/demo/session", json={"user_id": "demo-a"})

        context = client.app.state.context
        handle = asyncio.run(
            context.registry.open(
                thread_id="busy-thread", owner_user_id="demo-a", request_id="r"
            )
        )
        context.repository.ensure_thread(owner_user_id="demo-a", thread_id="busy-thread")

        response = client.delete("/api/history/busy-thread")

        assert response.status_code == 409
        asyncio.run(context.registry.release(handle))


def test_a_second_request_on_a_busy_thread_is_refused(client):
    context = client.app.state.context
    handle = asyncio.run(
        context.registry.open(thread_id="busy-2", owner_user_id="demo-a", request_id="r")
    )
    context.repository.ensure_thread(owner_user_id="demo-a", thread_id="busy-2")

    response = client.post(
        "/api/chat/stream",
        json={"thread_id": "busy-2", "request_id": "r-2", "message": "再来一次"},
    )

    assert response.status_code == 409
    asyncio.run(context.registry.release(handle))


def test_cancel_reports_a_request_and_never_claims_a_rollback(client):
    turn(client)
    thread = only_thread(client)

    payload = client.post(f"/api/chat/{thread}/cancel").json()["data"]

    # The run already finished, so nothing is cancelled — and the response still refuses to
    # imply that a committed write could be undone.
    assert payload["cancel_requested"] is False
    assert "回滚" in payload["note"]
    assert "不会" in payload["note"]


def test_display_history_is_stored_separately_from_graph_state(client):
    """History must not shrink when the graph summarises: they are different records.

    One thread, four turns — the thread id is passed explicitly, because a turn without one
    starts a new thread and would make this a test about four threads of one turn each.
    """
    thread = f"t13-multi-{uuid.uuid4().hex[:8]}"
    for index in range(4):
        sse_events(
            client,
            thread_id=thread,
            message=f"第 {index} 轮查询",
            request_id=f"req-multi-{index}-{uuid.uuid4().hex[:6]}",
        )

    detail = client.get(f"/api/history/{thread}").json()["data"]

    assert len(detail["messages"]) == 8, [(m["role"], m["content"]) for m in detail["messages"]]
    assert [m["role"] for m in detail["messages"][:2]] == ["user", "assistant"]
    assert len(detail["runs"]) == 4
    # Display history is its own collection, so it survives independent of graph state.
    assert len({m["message_id"] for m in detail["messages"]}) == 8


def test_resume_must_carry_exactly_one_of_supplement_or_decisions(client):
    turn(client)
    thread = only_thread(client)

    both = client.post(
        f"/api/chat/{thread}/resume",
        json={
            "request_id": "r-both",
            "interrupt_id": "i1",
            "resume": {"supplement": "P001", "decisions": [{"type": "approve"}]},
        },
    )
    neither = client.post(
        f"/api/chat/{thread}/resume",
        json={"request_id": "r-none", "interrupt_id": "i1", "resume": {}},
    )

    assert both.status_code == 400
    assert neither.status_code == 400


def test_a_real_approval_interrupt_is_recorded_and_refuses_a_second_decision(
    resources, approval_service
):
    """The interrupt is persisted before ``done``, so a refresh cannot show a ghost."""
    agent = ScriptedAgent(interrupt=True)
    with build_client(resources, approval_service, agent) as client:
        client.post("/api/demo/session", json={"user_id": "demo-a"})
        status, events = turn(client)
        assert status == 200

        names = [name for name, _ in events]
        assert "interrupt" in names
        assert names.count("done") == 1
        done = [envelope for name, envelope in events if name == "done"][0]
        assert done["payload"]["status"] == "interrupted"
        assert done["payload"]["interrupted"] is True

        thread = only_thread(client)
        state = client.get(f"/api/chat/{thread}/state").json()["data"]

        assert state["status"] == "interrupted"
        assert len(state["pending_interrupts"]) == 1
        interrupt = state["pending_interrupts"][0]
        assert interrupt["interrupt_type"] == "hitl_approval"
        assert interrupt["candidates"][0]["tool_name"] == "order_create"

        # The pending action exists, with its payload frozen, before anyone approved it.
        action = approval_service.store.find("demo-a", thread, interrupt["interrupt_id"])
        assert action is not None
        assert action.status is PendingStatus.PENDING
        assert action.payload["lines"][0]["unit_price"] == "25.50"

        approved = client.post(
            f"/api/chat/{thread}/resume",
            json={
                "request_id": "resume-1",
                "interrupt_id": interrupt["interrupt_id"],
                "resume": {"decisions": [{"type": "approve"}]},
            },
        )
        assert approved.status_code == 200

        stored = approval_service.store.find("demo-a", thread, interrupt["interrupt_id"])
        assert stored.status is PendingStatus.APPROVED

        # A second decision is refused, and it does not start another run either.
        again = client.post(
            f"/api/chat/{thread}/resume",
            json={
                "request_id": "resume-2",
                "interrupt_id": interrupt["interrupt_id"],
                "resume": {"decisions": [{"type": "approve"}]},
            },
        )
        assert again.status_code == 409
        assert "ALREADY_DECIDED" in again.json()["detail"]


def test_a_run_that_raises_reports_error_then_failed_and_never_completed(
    resources, approval_service
):
    agent = ScriptedAgent(fail=True)
    with build_client(resources, approval_service, agent) as client:
        client.post("/api/demo/session", json={"user_id": "demo-a"})
        _status, events = turn(client)

        names = [name for name, _ in events]
        assert "error" in names
        done = [envelope for name, envelope in events if name == "done"][0]

        assert done["payload"]["status"] == "failed"
        assert names.count("done") == 1
        assert names.index("error") < names.index("done")

        thread = only_thread(client)
        assert client.get(f"/api/chat/{thread}/state").json()["data"]["status"] == "failed"


def test_the_run_registry_refuses_a_second_run_on_one_thread():
    async def scenario() -> None:
        registry = RunRegistry()
        first = await registry.open(thread_id="t", owner_user_id="demo-a", request_id="r1")
        with pytest.raises(ThreadBusy):
            await registry.open(thread_id="t", owner_user_id="demo-a", request_id="r2")
        await registry.release(first)
        # Releasing frees the thread for the next run.
        second = await registry.open(thread_id="t", owner_user_id="demo-a", request_id="r2")
        assert second.run_id

    asyncio.run(scenario())


def test_a_real_graph_streams_through_the_api_and_reports_a_real_interrupt(resources):
    """The adapter against the framework itself, not against a description of it.

    Everything here is real: the checkpointer is MongoDB, the graph is ``create_deep_agent``,
    the interrupt is the framework's own ``interrupt_on``. Only the model is scripted.
    """
    from langchain_core.callbacks import CallbackManagerForLLMRun
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage, BaseMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_core.tools import StructuredTool
    from deepagents import create_deep_agent

    class ScriptedChatModel(BaseChatModel):
        script: list[AIMessage]

        @property
        def _llm_type(self) -> str:
            return "scripted"

        def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
            return self

        def _generate(
            self,
            messages: list[BaseMessage],
            stop: list[str] | None = None,
            run_manager: CallbackManagerForLLMRun | None = None,
            **kwargs: Any,
        ) -> ChatResult:
            if not self.script:
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content="完成"))])
            return ChatResult(generations=[ChatGeneration(message=self.script.pop(0))])

        @property
        def _identifying_params(self) -> dict[str, Any]:
            return {}

    def order_create(supplier_id: str, currency: str, lines: list, note: str = "") -> str:
        """Create an order."""
        return json.dumps({"ok": False, "data": None, "error": {"code": "SHOULD_NOT_RUN", "message": "x", "retryable": False}})

    tool = StructuredTool.from_function(
        func=order_create, name="order_create", description="Create an order."
    )
    graph = create_deep_agent(
        model=ScriptedChatModel(
            script=[
                AIMessage(
                    content="我先准备下单。",
                    tool_calls=[
                        {
                            "name": "order_create",
                            "args": {
                                "supplier_id": "S001",
                                "currency": "CNY",
                                "lines": [{"part_id": "P001", "quantity": 50, "unit_price": "25.50"}],
                            },
                            "id": "real-call-1",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        ),
        tools=[tool],
        system_prompt="你是采购助手。",
        interrupt_on={"order_create": {"allowed_decisions": ["approve", "reject"]}},
        checkpointer=resources.checkpointer,
    )

    approvals = ApprovalService(
        store=MongoPendingActionStore(resources.database), grant_secret="t13-grant-secret"
    )
    with build_client(resources, approvals, graph) as client:
        client.post("/api/demo/session", json={"user_id": "demo-a"})
        status, events = turn(client, message="帮我下单 P001 50 件")

        names = [name for name, _ in events]
        assert status == 200
        assert "token" in names, "真实图应当产生文本块"
        assert "interrupt" in names, "真实 interrupt_on 应当产生中断事件"

        done = [envelope for name, envelope in events if name == "done"][0]
        assert done["payload"]["status"] == "interrupted"
        assert names.count("done") == 1

        thread = only_thread(client)
        state = client.get(f"/api/chat/{thread}/state").json()["data"]
        assert state["pending_interrupts"], "刷新后应当能读到待处理中断"
        assert state["pending_interrupts"][0]["interrupt_type"] == "hitl_approval"
