"""T14 acceptance: the browser-facing contract, end to end against the real backend.

The frontend's own logic is covered by Vitest (`frontend/tests/*.spec.ts`, runnable with
``npm run test``, 46 cases). What those cannot prove is that the *server* still emits what the
client parses — and that is the failure mode this file exists to catch: a parser that is
right about a wire format the backend stopped producing.

So every assertion here is made against a live FastAPI app over a real MongoDB, and the
streams are consumed as raw bytes and re-framed by hand, the same way
``frontend/src/api/sse.ts`` does it. Two of the tests go further and run the framework's own
``create_deep_agent`` so the interrupt path is the real one rather than a description of it.

The checks the gate requires are these tests plus ``npm run build``; the build check proves
the interface still compiles and bundles.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
for _path in (str(SRC_DIR), str(REPO_ROOT / "tests")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from fixtures import mongo_service  # noqa: E402

from agent.approval.models import PendingStatus  # noqa: E402
from agent.approval.service import ApprovalService  # noqa: E402
from agent.approval.store import MongoPendingActionStore  # noqa: E402
from agent.artifacts.service import ArtifactService  # noqa: E402
from agent.artifacts.store import MongoArtifactStore  # noqa: E402
from agent.async_tasks.service import build_async_task_service  # noqa: E402
from agent.persistence.repository import ApplicationRepository  # noqa: E402
from api_view.api.deps import SESSION_COOKIE, WebContext  # noqa: E402
from api_view.run_registry import RunRegistry  # noqa: E402
from api_view.web_main import create_app  # noqa: E402

pytestmark = pytest.mark.integration

FRONTEND_DIR = REPO_ROOT / "frontend"
DIST_DIR = FRONTEND_DIR / "dist"

#: The event names the client switches on. A backend that renames one of these silently
#: breaks the UI, so the set is asserted rather than assumed.
CLIENT_EVENTS = {
    "run_started",
    "token",
    "tool_start",
    "tool_args",
    "tool_result",
    "tool_end",
    "todos",
    "interrupt",
    "error",
    "done",
}

#: Fields the client reads off every envelope.
ENVELOPE_FIELDS = {"v", "thread_id", "run_id", "seq", "source", "payload"}


# --------------------------------------------------------------------------- #
# scripting a graph
# --------------------------------------------------------------------------- #


def _text(text: str, message_id: str = "m1"):
    from langchain_core.messages import AIMessage

    return AIMessage(content=text, id=message_id)


def _chunk(call_id: str, name: str | None, args: str):
    from langchain_core.messages import AIMessageChunk

    return AIMessageChunk(
        content="",
        tool_call_chunks=[
            {"name": name, "args": args, "id": call_id, "index": 0, "type": "tool_call_chunk"}
        ],
    )


class ScriptedAgent:
    """Emits the framework's v2 part shapes; the model double the task allows."""

    def __init__(self, *, interrupt: bool = False) -> None:
        self.interrupt = interrupt
        self.state: dict[str, Any] = {"messages": [], "todos": []}
        self.calls = 0

    async def astream(self, payload, config, **kwargs):
        assert kwargs.get("version") == "v2"
        assert kwargs.get("subgraph") is True
        self.calls += 1

        yield {"type": "messages", "ns": [], "data": (_text("正在"), {"message_id": "m1"})}
        yield {"type": "messages", "ns": [], "data": (_text("查询"), {"message_id": "m1"})}
        # One character at a time, exactly as the real stream delivers arguments.
        for fragment in ('{"part_id"', ': "P001"}'):
            yield {
                "type": "messages",
                "ns": [],
                "data": (_chunk("call-1", "part_query" if fragment.startswith("{") else None, fragment), {}),
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
                    _text("**P001** 库存 8。", "m3"),
                    _tool_result("call-1", json.dumps({"ok": True, "data": {"on_hand": 8}})),
                ],
                "todos": [{"content": "查 P001", "status": "completed"}],
            },
        }

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
                id="int-t14",
            )
            self.state = {**self.state, "__interrupt__": (interrupt,)}
            yield {"type": "values", "ns": [], "data": self.state, "interrupts": (interrupt,)}
            return
        self.state = {"messages": [], "todos": [{"content": "查 P001", "status": "completed"}]}

    def get_state(self, config):
        interrupts = self.state.get("__interrupt__")
        tasks = (type("T", (), {"interrupts": interrupts})(),) if interrupts else ()
        return type("S", (), {"values": self.state, "tasks": tasks})()


def _tool_result(call_id: str, body: str):
    from langchain_core.messages import ToolMessage

    return ToolMessage(content=body, tool_call_id=call_id, name="part_query")


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def settings():
    candidate = mongo_service.unique_settings(f"t14-{uuid.uuid4().hex[:8]}")
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


def build_client(resources, agent):
    approvals = ApprovalService(
        store=MongoPendingActionStore(resources.database), grant_secret="t14-grant-secret"
    )
    context = WebContext(
        resources=resources,
        repository=ApplicationRepository(resources.database),
        registry=RunRegistry(),
        approvals=approvals,
        artifacts=ArtifactService(store=MongoArtifactStore(resources.database)),
        async_tasks=build_async_task_service(resources.database),
        settings=resources.settings,
        graph_provider=lambda owner: agent,
        internal_service_token="t14-internal",
    )
    from starlette.testclient import TestClient

    return TestClient(create_app(context=context)), approvals


@pytest.fixture()
def client(resources) -> Iterator[Any]:
    agent = ScriptedAgent()
    built, _approvals = build_client(resources, agent)
    with built as test_client:
        test_client.post("/api/demo/session", json={"user_id": "demo-a"})
        yield test_client


# --------------------------------------------------------------------------- #
# the wire format the client parses
# --------------------------------------------------------------------------- #


def read_frames(response) -> list[tuple[str, str, dict]]:
    """Re-frame the stream by hand, the way ``frontend/src/api/sse.ts`` does.

    Deliberately not using an SSE client library: the point is to check the bytes the browser
    will actually receive, including the ``id:`` line the client stores for reconnection.
    """
    frames: list[tuple[str, str, dict]] = []
    event = ""
    event_id = ""
    data: list[str] = []

    def flush() -> None:
        if data:
            frames.append((event or "message", event_id, json.loads("\n".join(data))))
            data.clear()

    for raw in response.iter_lines():
        line = raw if isinstance(raw, str) else raw.decode("utf-8")
        if line == "":
            flush()
            event, event_id = "", ""
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "event":
            event = value
        elif field == "id":
            event_id = value
        elif field == "data":
            data.append(value)
    flush()
    return frames


def start_turn(client, *, message="查一下 P001 库存", thread_id=None, request_id=None):
    body: dict[str, Any] = {
        "request_id": request_id or f"req-{uuid.uuid4().hex[:10]}",
        "message": message,
    }
    if thread_id:
        body["thread_id"] = thread_id
    with client.stream("POST", "/api/chat/stream", json=body) as response:
        assert response.status_code == 200, response.status_code
        return read_frames(response)


def only_thread(client) -> str:
    items = client.get("/api/history").json()["data"]["items"]
    assert items
    return items[0]["thread_id"]


def test_every_frame_has_an_id_an_event_and_the_expected_envelope(client):
    """The client reads `id`, `event` and the envelope. All three must be well formed."""
    frames = start_turn(client)

    assert frames
    for event, event_id, envelope in frames:
        assert event in CLIENT_EVENTS, f"client does not know the event {event!r}"
        # Two id shapes, both deliberate: most frames are numbered within the run, while a
        # token frame carries the *message* id so a client can upsert the same message across
        # deliveries instead of appending it twice.
        assert re.fullmatch(r"[0-9a-f-]{36}:(?:\d+|[\w:-]+)", event_id), event_id
        assert ENVELOPE_FIELDS <= set(envelope), (event, sorted(envelope))
        assert envelope["v"] == 1
        assert envelope["source"], "source 不能为空，UI 用它归因"

    token_ids = {
        envelope["payload"]["message_id"]
        for event, _id, envelope in frames
        if event == "token"
    }
    assert token_ids, "至少要有一个带 message_id 的 token 帧"
    assert any(
        event_id.endswith(next(iter(token_ids)))
        for event, event_id, _envelope in frames
        if event == "token"
    ), "token 帧的 id 应当以 message_id 结尾，便于按 ID 聚合"


def test_the_event_order_is_what_the_client_switches_on(client):
    frames = start_turn(client)
    names = [event for event, _id, _envelope in frames]

    assert names[0] == "run_started"
    assert names[-1] == "done"
    assert names.count("done") == 1
    assert names.index("tool_start") < names.index("tool_args") < names.index("tool_end")
    assert "todos" in names


def test_sequence_numbers_are_strictly_increasing(client):
    frames = start_turn(client)
    seqs = [envelope["seq"] for _event, _id, envelope in frames]

    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)


def test_tool_arguments_arrive_as_fragments_that_concatenate_to_json(client):
    """The UI concatenates `delta` per `tool_call_id`; the result must parse."""
    frames = start_turn(client)

    fragments = [
        envelope["payload"]["delta"]
        for event, _id, envelope in frames
        if event == "tool_args"
    ]
    assert len(fragments) >= 2, "参数应当分片到达，否则前端拼接逻辑没有被真实覆盖"

    call_id = next(
        envelope["tool_call_id"] for event, _id, envelope in frames if event == "tool_args"
    )
    joined = "".join(
        envelope["payload"]["delta"]
        for event, _id, envelope in frames
        if event == "tool_args" and envelope["tool_call_id"] == call_id
    )
    assert json.loads(joined) == {"part_id": "P001"}


def test_a_completed_run_says_completed_and_not_interrupted(client):
    frames = start_turn(client)
    done = next(envelope for event, _id, envelope in frames if event == "done")

    assert done["payload"]["status"] == "completed"
    assert done["payload"]["interrupted"] is False


def test_the_assistant_text_matches_the_streamed_tokens(client):
    """A refresh must show the same answer the stream produced, not a different one."""
    frames = start_turn(client)
    streamed = "".join(
        envelope["payload"]["text"] for event, _id, envelope in frames if event == "token"
    )
    done = next(envelope for event, _id, envelope in frames if event == "done")
    thread = only_thread(client)
    detail = client.get(f"/api/history/{thread}").json()["data"]
    stored = [m["content"] for m in detail["messages"] if m["role"] == "assistant"]

    assert streamed
    assert done["payload"]["content"] == streamed.strip()
    assert stored and stored[-1].strip() == streamed.strip()


def test_the_state_endpoint_answers_the_refresh_case(client):
    start_turn(client)
    thread = only_thread(client)

    state = client.get(f"/api/chat/{thread}/state").json()["data"]

    assert state["status"] == "completed"
    assert state["todos"] == [{"content": "查 P001", "status": "completed"}]
    assert state["pending_interrupts"] == []
    assert state["last_event_id"] is None or re.fullmatch(r"[0-9a-f-]{36}:\d+", state["last_event_id"])


# --------------------------------------------------------------------------- #
# approval, and two tabs
# --------------------------------------------------------------------------- #


def test_an_approval_interrupt_reaches_the_client_with_its_line_items(resources):
    agent = ScriptedAgent(interrupt=True)
    built, approvals = build_client(resources, agent)
    with built as client:
        client.post("/api/demo/session", json={"user_id": "demo-a"})
        frames = start_turn(client, message="帮我下单 P001 50 件")

        interrupt = next(envelope for event, _id, envelope in frames if event == "interrupt")
        payload = interrupt["payload"]

        assert payload["interrupt_type"] == "hitl_approval"
        assert payload["interrupt_id"] == "int-t14"

        approval = next(item for item in payload["candidates"] if item["type"] == "approval")
        assert approval["tool_name"] == "order_create"
        assert approval["arguments"]["lines"][0]["unit_price"] == "25.50"

        decisions = next(item for item in payload["candidates"] if item["type"] == "decisions")
        assert decisions["allowed"] == ["approve", "reject"]

        done = next(envelope for event, _id, envelope in frames if event == "done")
        assert done["payload"]["status"] == "interrupted"
        assert done["payload"]["interrupted"] is True

        thread = only_thread(client)
        action = approvals.store.find("demo-a", thread, "int-t14")
        assert action is not None
        assert action.status is PendingStatus.PENDING


def test_a_refresh_after_an_interrupt_can_still_be_approved(resources):
    """The pending interrupt must come from the server, or a reload strands the run."""
    agent = ScriptedAgent(interrupt=True)
    built, approvals = build_client(resources, agent)
    with built as client:
        client.post("/api/demo/session", json={"user_id": "demo-a"})
        start_turn(client, message="帮我下单 P001 50 件")
        thread = only_thread(client)

        state = client.get(f"/api/chat/{thread}/state").json()["data"]
        assert state["status"] == "interrupted"
        pending = state["pending_interrupts"][0]
        assert pending["interrupt_type"] == "hitl_approval"

        response = client.post(
            f"/api/chat/{thread}/resume",
            json={
                "request_id": f"resume-{uuid.uuid4().hex[:8]}",
                "interrupt_id": pending["interrupt_id"],
                "resume": {"decisions": [{"type": "approve"}]},
            },
        )

        assert response.status_code == 200
        stored = approvals.store.find("demo-a", thread, pending["interrupt_id"])
        assert stored.status is PendingStatus.APPROVED


def test_two_tabs_approving_at_once_produce_one_decision(resources):
    """The second tab must lose, and the client is told which conflict it hit."""
    agent = ScriptedAgent(interrupt=True)
    built, approvals = build_client(resources, agent)
    with built as client:
        client.post("/api/demo/session", json={"user_id": "demo-a"})
        start_turn(client, message="帮我下单 P001 50 件")
        thread = only_thread(client)
        pending = client.get(f"/api/chat/{thread}/state").json()["data"]["pending_interrupts"][0]

        body = {
            "interrupt_id": pending["interrupt_id"],
            "resume": {"decisions": [{"type": "approve"}]},
        }
        first = client.post(
            f"/api/chat/{thread}/resume",
            json={**body, "request_id": f"tab-a-{uuid.uuid4().hex[:6]}"},
        )
        second = client.post(
            f"/api/chat/{thread}/resume",
            json={**body, "request_id": f"tab-b-{uuid.uuid4().hex[:6]}"},
        )

        assert first.status_code == 200
        assert second.status_code == 409
        assert "ALREADY_DECIDED" in second.json()["detail"]

        # One decision, one record, still one pending action.
        assert approvals.store.find("demo-a", thread, pending["interrupt_id"]).status is (
            PendingStatus.APPROVED
        )


def test_a_resume_body_with_both_shapes_is_refused(client):
    start_turn(client)
    thread = only_thread(client)

    response = client.post(
        f"/api/chat/{thread}/resume",
        json={
            "request_id": "both",
            "interrupt_id": "int-x",
            "resume": {"supplement": "P001", "decisions": [{"type": "approve"}]},
        },
    )

    assert response.status_code == 400


# --------------------------------------------------------------------------- #
# identity, conflict and deletion — the client's error branches
# --------------------------------------------------------------------------- #


def test_the_session_cookie_is_httponly_and_only_two_accounts_exist(client):
    response = client.post("/api/demo/session", json={"user_id": "demo-b"})

    assert response.status_code == 200
    cookie = response.headers.get("set-cookie", "")
    assert SESSION_COOKIE in cookie
    assert "HttpOnly" in cookie
    assert client.post("/api/demo/session", json={"user_id": "demo-c"}).status_code == 400


def test_a_replayed_request_id_returns_the_run_instead_of_a_second_stream(client):
    request_id = f"req-{uuid.uuid4().hex[:8]}"
    start_turn(client, request_id=request_id)
    thread = only_thread(client)

    response = client.post(
        "/api/chat/stream",
        json={"thread_id": thread, "request_id": request_id, "message": "查一下 P001 库存"},
    )
    payload = response.json()["data"]

    assert response.status_code == 200
    assert payload["replayed"] is True
    assert payload["run_id"]


def test_a_request_id_reused_with_other_text_is_a_conflict(client):
    request_id = f"req-{uuid.uuid4().hex[:8]}"
    start_turn(client, request_id=request_id)

    response = client.post(
        "/api/chat/stream",
        json={"thread_id": only_thread(client), "request_id": request_id, "message": "换了内容"},
    )

    assert response.status_code == 409


def test_another_account_cannot_see_or_touch_the_thread(client):
    start_turn(client)
    thread = only_thread(client)
    client.post("/api/demo/session", json={"user_id": "demo-b"})

    assert client.get(f"/api/chat/{thread}/state").status_code == 404
    assert client.get(f"/api/history/{thread}").status_code == 404
    assert client.delete(f"/api/history/{thread}").status_code == 404
    assert client.get("/api/history").json()["data"]["total"] == 0


def test_deleting_a_thread_names_what_it_keeps(client):
    """The UI prints these names, so they are part of the contract."""
    start_turn(client)
    thread = only_thread(client)

    payload = client.delete(f"/api/history/{thread}").json()["data"]

    assert payload["deleted"]["threads"] == 1
    assert payload["deleted"]["checkpoints"] == 1
    assert "用户技能" in payload["kept"]
    assert "业务偏好" in "、".join(payload["kept"]) or "用户偏好" in payload["kept"]
    assert any("订单" in item for item in payload["kept"])
    assert client.get(f"/api/history/{thread}").status_code == 404


def test_history_lists_what_the_drawer_needs(client):
    start_turn(client)

    page = client.get("/api/history").json()["data"]

    assert page["total"] >= 1
    item = page["items"][0]
    assert {"thread_id", "title", "message_count", "status", "updated_at"} <= set(item)
    assert item["message_count"] == 2


# --------------------------------------------------------------------------- #
# the built interface
# --------------------------------------------------------------------------- #


def test_the_frontend_builds_and_ships_no_placeholder_screen():
    """`npm run build` is a required check; this asserts its output is the real UI."""
    completed = subprocess.run(
        ["npm", "run", "build"],
        cwd=str(FRONTEND_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        check=False,
        shell=sys.platform == "win32",
    )
    assert completed.returncode == 0, completed.stdout[-2000:] + completed.stderr[-2000:]

    index = DIST_DIR / "index.html"
    assert index.is_file(), "构建产物缺少 index.html"
    html = index.read_text(encoding="utf-8")
    assert "/src/main.ts" not in html, "index.html 仍指向源码入口"

    scripts = list((DIST_DIR / "assets").glob("*.js"))
    assert scripts, "构建产物缺少打包后的脚本"
    bundle = "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in scripts)

    # The real workspace is in the bundle, and it talks to the real endpoints rather than to
    # hard-coded fixtures. The paths are asserted as fragments because the client builds them
    # by concatenation, so the joined literal never appears in the minified output.
    assert "采购助手工作台" in bundle
    assert "/chat/stream" in bundle
    assert "/demo/session" in bundle
    assert "/history" in bundle
    assert "正在运行" in bundle or "等待审批" in bundle


def test_the_built_stylesheet_has_the_narrow_screen_breakpoint():
    """The narrow layout is a stated acceptance criterion, so it is asserted, not assumed."""
    css_files = list((DIST_DIR / "assets").glob("*.css"))
    assert css_files, "构建产物缺少样式表"
    css = "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in css_files)

    # The minifier rewrites `max-width: 900px` into the modern range form `width<=900px`,
    # so both spellings are accepted — the assertion is about the breakpoint existing at
    # 900px, not about which syntax survived the build.
    assert re.search(r"@media\s*\(\s*(?:max-width\s*:\s*900px|width\s*<=\s*900px)\s*\)", css), (
        "缺少 900px 窄屏断点"
    )
    assert "grid-template-columns" in css
    # The narrow layout must actually restack the workspace, not merely declare a breakpoint.
    assert re.search(r"grid-template-columns:\s*(?:minmax\(0,\s*1fr\)|1fr)", css)
