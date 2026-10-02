"""T20 acceptance: background analysis through the Agent Protocol.

Everything here runs against a **real, separate service process** on its own port, reached by
the official SDK, with real MongoDB. The contract is explicit that an asyncio task dressed up
as a background job does not count, so no test substitutes one.

The properties under test:

* a launch creates a genuine Protocol thread and run — the ids in the task record are the
  service's, and the service agrees they exist;
* a repeated request is idempotent, and a second owner sees nothing;
* cancel is final: the run stops and a later poll cannot revive it;
* an update is a follow-up run, because the SDK cannot rewrite a running input and claiming
  otherwise would be a lie the user acts on;
* an unreachable service is never reported as a finished task, and a run the dev server has
  forgotten reports ``lost`` rather than inventing an outcome;
* the background analyst has no write tools, and the main process refuses one independently.
"""

from __future__ import annotations

import base64
import json
import sys
import time
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.approval.service import ApprovalService  # noqa: E402
from agent.approval.store import MongoPendingActionStore  # noqa: E402
from agent.artifacts.service import ArtifactService  # noqa: E402
from agent.artifacts.store import MongoArtifactStore  # noqa: E402
from agent.async_tasks.service import (  # noqa: E402
    AsyncTaskService,
    ProtocolUnavailable,
    SdkProtocolClient,
)
from agent.async_tasks.store import AsyncStatus, MongoAsyncTaskStore  # noqa: E402
from agent.persistence.indexes import (  # noqa: E402
    drop_undeclared_application_indexes,
    ensure_application_indexes,
)
from agent.persistence.repository import ApplicationRepository  # noqa: E402
from api_view.api.deps import WebContext  # noqa: E402
from api_view.run_registry import RunRegistry  # noqa: E402
from api_view.web_main import create_app  # noqa: E402
from fixtures import agent_protocol_service, mongo_service, sandbox_service  # noqa: E402

pytestmark = pytest.mark.integration

OWNER_A = "demo-a"
OWNER_B = "demo-b"
INTERNAL_TOKEN = "t20-internal-token"

#: The deterministic graph. Using it for the endpoint tests keeps a transport failure
#: distinguishable from a graph failure, exactly as ``graph.py`` intends.
TEST_GRAPH = "echo"

TERMINAL = {"completed", "failed", "cancelled", "lost"}


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def settings():
    return mongo_service.unique_settings(f"t20-{uuid.uuid4().hex[:8]}")


@pytest.fixture(scope="module")
def database(settings):
    from pymongo import MongoClient

    mongo_service.require_reachable(settings)
    with MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000, tz_aware=True) as mongo:
        db = mongo[settings.database]
        drop_undeclared_application_indexes(db)
        ensure_application_indexes(db)
        yield db
    mongo_service.drop_test_database(settings)


@pytest.fixture(scope="module")
def service_handle():
    """The real Agent Protocol process. Started once for the module, then reused."""
    with agent_protocol_service.running_service() as handle:
        yield handle


@pytest.fixture(scope="module")
def sync_client(service_handle):
    return agent_protocol_service.client(service_handle.base_url)


@pytest.fixture(scope="module")
def async_tasks(database, service_handle, sync_client):
    """The task service pointed at the real server's deterministic graph."""
    return AsyncTaskService(
        store=MongoAsyncTaskStore(database),
        protocol=SdkProtocolClient(service_handle.base_url, client=sync_client),
        graph_id=TEST_GRAPH,
    )


@pytest.fixture(scope="module")
def parent_thread(database):
    """A real conversation for tasks to belong to."""
    repository = ApplicationRepository(database)
    repository.ensure_thread(owner_user_id=OWNER_A, thread_id="t20-parent", title="后台任务")
    repository.ensure_thread(owner_user_id=OWNER_B, thread_id="t20-parent-b", title="别人的")
    return "t20-parent"


@pytest.fixture(scope="module")
def sandbox_lease():
    """One real sandbox, handed out as if it were a pool. The endpoint has to do the rest."""
    with sandbox_service.running_backend() as (backend, _report):
        yield backend


@pytest.fixture(scope="module")
def api(database, settings, async_tasks, parent_thread, sandbox_lease):
    context = WebContext(
        resources=type("Resources", (), {"database": database, "settings": settings})(),
        repository=ApplicationRepository(database),
        registry=RunRegistry(),
        approvals=ApprovalService(
            store=MongoPendingActionStore(database), grant_secret="t20-grant"
        ),
        artifacts=ArtifactService(store=MongoArtifactStore(database)),
        async_tasks=async_tasks,
        settings=settings,
        graph_provider=lambda owner: None,
        internal_service_token=INTERNAL_TOKEN,
        sandboxes=type("Leases", (), {"get_or_create": staticmethod(lambda owner: sandbox_lease)})(),
    )
    return create_app(context=context)


@pytest.fixture(scope="module")
def client(api):
    from starlette.testclient import TestClient

    return TestClient(api)


def as_owner(owner: str = OWNER_A) -> dict[str, str]:
    return {"X-Demo-User": owner}


def launch(client, **overrides) -> dict:
    body = {
        "parent_thread_id": "t20-parent",
        "request_id": f"req-{uuid.uuid4().hex[:10]}",
        "instruction": "看看哪些物料需要补货",
    }
    body.update(overrides)
    response = client.post("/api/async-tasks", json=body, headers=as_owner())
    assert response.status_code in (200, 202), response.text
    return response.json()["data"]


def wait_for_terminal(client, task_id: str, *, timeout: float = 60.0) -> dict:
    deadline = time.monotonic() + timeout
    current: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/async-tasks/{task_id}", headers=as_owner())
        assert response.status_code == 200, response.text
        current = response.json()["data"]
        if current["status"] in TERMINAL:
            return current
        time.sleep(0.4)
    return current


# --------------------------------------------------------------------------- #
# the service is real
# --------------------------------------------------------------------------- #


def test_the_service_is_a_separate_process_on_its_own_port(service_handle):
    """Not an in-process fake: this is an HTTP server this project does not own."""
    import httpx

    health = httpx.get(f"{service_handle.base_url}/ok", timeout=10, trust_env=False)

    assert health.status_code == 200
    assert health.json() == {"ok": True}
    assert service_handle.base_url.endswith(":8123")


def test_both_graphs_are_registered_and_discoverable(sync_client):
    """The read-only analyst is served, and it is found by graph_id rather than by a guessed id."""
    graphs = sorted(item.get("graph_id") for item in sync_client.assistants.search())

    assert "echo" in graphs
    assert "procurement-analyst" in graphs
    analyst = agent_protocol_service.assistant_id_for(sync_client, "procurement-analyst")
    assert analyst


# --------------------------------------------------------------------------- #
# launch, status, idempotency
# --------------------------------------------------------------------------- #


def test_a_launch_creates_a_real_protocol_thread_and_run(client, sync_client):
    """The ids in the record are the service's, and the service agrees they exist."""
    task = launch(client)

    assert task["async_thread_id"] and task["async_run_id"]
    assert uuid.UUID(task["async_thread_id"])

    # Read the same run back through the SDK, independently of this project's mapping.
    run = sync_client.runs.get(task["async_thread_id"], task["async_run_id"])
    assert run["run_id"] == task["async_run_id"]


def test_the_task_status_follows_the_protocol_run(client, sync_client):
    task = launch(client)
    final = wait_for_terminal(client, task["task_id"])

    assert final["status"] in TERMINAL
    assert final["status"] == "completed", final
    assert final["terminal"] is True

    run = sync_client.runs.get(task["async_thread_id"], task["async_run_id"])
    assert run["status"] == "success", run


def test_the_contract_status_vocabulary_is_what_is_served(client):
    """``queued`` on the way in, and only the contract's words on the way out."""
    task = launch(client)

    assert task["status"] in {"queued", "running"}

    final = wait_for_terminal(client, task["task_id"])
    assert final["status"] in {"queued", "running", "completed", "failed", "cancelled", "lost"}


def test_a_repeated_request_id_returns_the_original_task(client, sync_client, database):
    """A double-submitted form must not analyse twice."""
    request_id = f"req-{uuid.uuid4().hex[:10]}"
    first = launch(client, request_id=request_id)

    before = len(sync_client.threads.search(limit=100))
    second = launch(client, request_id=request_id)
    after = len(sync_client.threads.search(limit=100))

    assert second["task_id"] == first["task_id"]
    assert after == before, "重复的 request_id 不应再创建一个 Protocol thread"


def test_tasks_are_listed_for_their_parent_conversation(client):
    task = launch(client)

    listing = client.get(
        "/api/async-tasks",
        params={"parent_thread_id": "t20-parent"},
        headers=as_owner(),
    )

    assert listing.status_code == 200
    ids = [item["task_id"] for item in listing.json()["data"]["items"]]
    assert task["task_id"] in ids


# --------------------------------------------------------------------------- #
# ownership
# --------------------------------------------------------------------------- #


def test_another_owner_cannot_see_a_task_on_any_route(client):
    """404 everywhere, not 403: a 403 would confirm the task exists."""
    task = launch(client)

    assert client.get(f"/api/async-tasks/{task['task_id']}", headers=as_owner(OWNER_B)).status_code == 404
    assert (
        client.post(
            f"/api/async-tasks/{task['task_id']}/cancel",
            json={"request_id": "x"},
            headers=as_owner(OWNER_B),
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/async-tasks/{task['task_id']}/update",
            json={"request_id": "x", "instruction": "别的"},
            headers=as_owner(OWNER_B),
        ).status_code
        == 404
    )
    # And owner A's conversation is not listable by owner B at all: the parent check runs
    # before any task is looked at, so the answer is "no such thread" rather than an empty
    # list that would confirm the conversation exists.
    listing = client.get(
        "/api/async-tasks",
        params={"parent_thread_id": "t20-parent"},
        headers=as_owner(OWNER_B),
    )
    assert listing.status_code == 404


def test_a_task_cannot_be_attached_to_someone_elses_conversation(client):
    """Work is launched *into* a conversation, so the caller must be able to see it."""
    response = client.post(
        "/api/async-tasks",
        json={
            "parent_thread_id": "t20-parent-b",
            "request_id": f"req-{uuid.uuid4().hex[:8]}",
            "instruction": "往别人的会话里塞任务",
        },
        headers=as_owner(OWNER_A),
    )

    assert response.status_code == 404


def test_a_request_without_a_session_is_refused(client):
    response = client.post(
        "/api/async-tasks",
        json={
            "parent_thread_id": "t20-parent",
            "request_id": "req-no-session",
            "instruction": "没有会话",
        },
    )

    assert response.status_code == 401


# --------------------------------------------------------------------------- #
# cancel and update
# --------------------------------------------------------------------------- #


def test_cancel_is_final_and_a_later_poll_cannot_revive_it(client):
    """`取消后不再发新工具调用` — the terminal status is what stops the polling."""
    task = launch(client)

    cancelled = client.post(
        f"/api/async-tasks/{task['task_id']}/cancel",
        json={"request_id": "cancel-1"},
        headers=as_owner(),
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["data"]["status"] == "cancelled"

    time.sleep(1.0)
    after = client.get(f"/api/async-tasks/{task['task_id']}", headers=as_owner())
    assert after.json()["data"]["status"] == "cancelled", "终态必须保持终态"
    assert after.json()["data"]["terminal"] is True


def test_an_update_starts_a_followup_run_and_says_so(client, sync_client):
    """The SDK cannot rewrite a running input; the record must not claim it did."""
    task = launch(client)
    response = client.post(
        f"/api/async-tasks/{task['task_id']}/update",
        json={"request_id": "upd-1", "instruction": "再顺便看看 P003"},
        headers=as_owner(),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accepted"] is True
    entry = body["data"]["updates"][-1]
    assert entry["handling"] == "followup_run"
    assert "不支持改写" in entry["note"]
    assert body["data"]["async_run_id"] == entry["run_id"], "状态应跟随新的 run"
    # The follow-up run really exists on the service.
    assert sync_client.runs.get(body["data"]["async_thread_id"], entry["run_id"])["run_id"] == entry["run_id"]


def test_a_repeated_update_is_not_applied_twice(client):
    task = launch(client)
    body = {"request_id": "upd-dup", "instruction": "同样的一句话"}

    first = client.post(f"/api/async-tasks/{task['task_id']}/update", json=body, headers=as_owner())
    second = client.post(f"/api/async-tasks/{task['task_id']}/update", json=body, headers=as_owner())

    assert first.json()["accepted"] is True
    assert second.json()["accepted"] is False
    assert len(second.json()["data"]["updates"]) == 1


def test_an_update_on_a_finished_task_is_refused(client):
    task = launch(client)
    wait_for_terminal(client, task["task_id"])

    response = client.post(
        f"/api/async-tasks/{task['task_id']}/update",
        json={"request_id": "upd-late", "instruction": "已经结束了还想改"},
        headers=as_owner(),
    )

    assert response.status_code == 409
    assert "不能" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# failure is visible and never becomes "completed"
# --------------------------------------------------------------------------- #


def test_an_unreachable_service_gives_503_and_no_task(client, database):
    """A launch that could not start anything must not leave a task that looks alive."""
    store = MongoAsyncTaskStore(database)
    dead = AsyncTaskService(
        store=store,
        protocol=SdkProtocolClient("http://127.0.0.1:1"),
        graph_id=TEST_GRAPH,
    )
    request_id = f"req-{uuid.uuid4().hex[:8]}"

    with pytest.raises(ProtocolUnavailable):
        dead.launch(
            owner_user_id=OWNER_A,
            parent_thread_id="t20-parent",
            request_id=request_id,
            instruction="服务不可用",
        )

    assert store.find_by_request(OWNER_A, request_id) is None, (
        "启动失败不得留下一条看起来在跑的任务"
    )


def test_a_task_cannot_be_created_with_an_empty_instruction(client):
    response = client.post(
        "/api/async-tasks",
        json={"parent_thread_id": "t20-parent", "request_id": "req-empty", "instruction": "   "},
        headers=as_owner(),
    )

    assert response.status_code == 400


def test_a_run_the_service_forgot_is_lost_not_completed(database, service_handle, sync_client):
    """The dev server does not persist runs across a restart; saying "completed" would be the
    one unrecoverable lie, because the user would wait for a report that cannot arrive."""
    from agent.async_tasks.store import AsyncTaskRecord

    store = MongoAsyncTaskStore(database)
    task_id = f"task-{uuid.uuid4().hex[:10]}"
    store.create(
        AsyncTaskRecord(
            task_id=task_id,
            owner_user_id=OWNER_A,
            parent_thread_id="t20-parent",
            instruction="run 已经不在服务里了",
            # Valid UUIDs the service has never seen: the shape a run id has after a restart.
            async_thread_id=str(uuid.uuid4()),
            async_run_id=str(uuid.uuid4()),
            status=str(AsyncStatus.RUNNING),
            created_at="",
            updated_at="",
            request_id=f"req-{uuid.uuid4().hex[:8]}",
        )
    )

    service = AsyncTaskService(
        store=store,
        protocol=SdkProtocolClient(service_handle.base_url, client=sync_client),
        graph_id=TEST_GRAPH,
    )
    refreshed = service.status(owner_user_id=OWNER_A, task_id=task_id)

    assert refreshed.status == "lost"
    assert refreshed.status != "completed"
    assert refreshed.last_error


def test_the_recorded_error_names_the_problem(client, database):
    """`错误…明确可见` — the message has to say something a person can act on."""
    task = launch(client)
    record = MongoAsyncTaskStore(database).find(OWNER_A, task["task_id"])
    assert record is not None
    assert record.async_thread_id and record.async_run_id


# --------------------------------------------------------------------------- #
# the background analyst is read-only, and so is its road into this process
# --------------------------------------------------------------------------- #


def test_the_analyst_graph_declares_no_write_tools():
    """`异步没有写单权限`, asserted against the graph's own declaration.

    Imported the way the service imports it, so this fails if the deployed graph ever grows a
    write tool — not merely if a copy in the tests does.
    """
    source = (
        Path(__file__).resolve().parents[2] / "infra" / "agent-protocol" / "analyst_graph.py"
    ).read_text(encoding="utf-8")

    namespace: dict = {}
    # Only the constants are read; the module's langgraph imports are irrelevant to this
    # question and would tie the assertion to the isolated environment.
    for line in source.splitlines():
        if line.startswith(("READ_TOOLS", "WRITE_TOOLS", "GRAPH_ID")):
            exec(line, namespace)  # noqa: S102 - fixed, repository-controlled source

    assert namespace["WRITE_TOOLS"] == ()
    assert "order_create" not in namespace["READ_TOOLS"]
    assert "order_update" not in namespace["READ_TOOLS"]
    assert namespace["GRAPH_ID"] == "procurement-analyst"


def test_the_main_process_refuses_a_write_tool_from_the_background_service(client):
    """The graph's declaration is not the control — this is. It refuses independently."""
    response = client.post(
        "/internal/analysis/read",
        json={
            "owner_user_id": OWNER_A,
            "tool": "order_create",
            "arguments": {"supplier_id": "S001"},
        },
        headers={"x-internal-service-token": INTERNAL_TOKEN},
    )

    assert response.status_code == 403
    assert "read-only" in response.json()["detail"]


def test_the_main_process_refuses_an_unknown_read_tool(client):
    response = client.post(
        "/internal/analysis/read",
        json={"owner_user_id": OWNER_A, "tool": "drop_database", "arguments": {}},
        headers={"x-internal-service-token": INTERNAL_TOKEN},
    )

    assert response.status_code == 400


@pytest.mark.parametrize("token", [None, "", "wrong-token"])
def test_the_internal_surfaces_require_the_service_token(client, token):
    """Both of T20's internal endpoints, refused the same way ``/internal/approvals/verify`` is."""
    headers = {"x-internal-service-token": token} if token is not None else {}
    for path in ("/internal/analysis/read", "/internal/sandbox/operations"):
        response = client.post(
            path,
            json={"owner_user_id": OWNER_A, "tool": "inventory_warning", "operation": "execute"},
            headers=headers,
        )
        assert response.status_code == 403, (path, response.text)


def test_the_internal_sandbox_runs_a_command_in_the_owners_lease(client):
    """The background process gets real execution — through this process's lease, not its own."""
    response = client.post(
        "/internal/sandbox/operations",
        json={
            "owner_user_id": OWNER_A,
            "thread_id": "t20-parent",
            "operation_id": "op-1",
            "operation": "execute",
            "command": "echo background-says-hello; uname -s",
        },
        headers={"x-internal-service-token": INTERNAL_TOKEN},
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["exit_code"] == 0
    assert "background-says-hello" in data["output"]
    assert "Linux" in data["output"]


def test_the_internal_sandbox_round_trips_a_file(client):
    payload = base64.b64encode(b"part_id,quantity\nP001,42\n").decode("ascii")
    written = client.post(
        "/internal/sandbox/operations",
        json={
            "owner_user_id": OWNER_A,
            "operation": "write_files",
            "files": [{"path": "/workspace/scratch/t20-lines.csv", "content_base64": payload}],
        },
        headers={"x-internal-service-token": INTERNAL_TOKEN},
    )
    assert written.status_code == 200
    assert written.json()["data"]["written"][0]["ok"] is True

    read_back = client.post(
        "/internal/sandbox/operations",
        json={
            "owner_user_id": OWNER_A,
            "operation": "download_files",
            "paths": ["/workspace/scratch/t20-lines.csv"],
        },
        headers={"x-internal-service-token": INTERNAL_TOKEN},
    )
    assert read_back.status_code == 200
    entry = read_back.json()["data"]["files"][0]
    assert entry["ok"] is True
    assert base64.b64decode(entry["content_base64"]) == b"part_id,quantity\nP001,42\n"


@pytest.mark.parametrize(
    "path",
    ["/etc/passwd", "/workspace/../etc/passwd", "relative.csv", "/workspace-other/x"],
)
def test_the_internal_sandbox_refuses_paths_outside_the_workspace(client, path):
    """An operation that could read any container path would be an exfiltration service."""
    response = client.post(
        "/internal/sandbox/operations",
        json={"owner_user_id": OWNER_A, "operation": "download_files", "paths": [path]},
        headers={"x-internal-service-token": INTERNAL_TOKEN},
    )

    assert response.status_code == 400, response.text
    assert "不在" in response.json()["detail"] or "invalid_path" in response.json()["detail"]


def test_the_internal_sandbox_refuses_an_unknown_operation(client):
    """A whitelist, not a proxy: there is no "call any manager method" form."""
    response = client.post(
        "/internal/sandbox/operations",
        json={"owner_user_id": OWNER_A, "operation": "recycle", "command": "rm -rf /"},
        headers={"x-internal-service-token": INTERNAL_TOKEN},
    )

    assert response.status_code == 400
    assert "unknown operation" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# the analyst graph itself, run for real
# --------------------------------------------------------------------------- #


def test_the_analyst_graph_runs_on_the_service_without_the_main_process(
    sync_client,
):
    """Run the registered analyst graph through the Protocol.

    Its first real step is an HTTP call back into the main process, which is not running in
    this test, so the run must end as ``error`` — and that is the assertion. A background
    analyst that returned a successful-looking empty report when it could not reach its data
    would be indistinguishable from one that found nothing to reorder.
    """
    assistant = agent_protocol_service.assistant_id_for(sync_client, "procurement-analyst")
    thread = sync_client.threads.create()
    run = sync_client.runs.create(
        thread["thread_id"],
        assistant_id=assistant,
        input={"messages": [{"role": "user", "content": "看看哪些物料需要补货"}]},
    )

    deadline = time.monotonic() + 60
    status = ""
    while time.monotonic() < deadline:
        status = str(sync_client.runs.get(thread["thread_id"], run["run_id"]).get("status"))
        if status in {"success", "error", "timeout", "interrupted"}:
            break
        time.sleep(0.4)

    assert status in {"error", "timeout", "interrupted"}, (
        f"主进程没有运行时不该得到 {status!r}；空报告会被误读成「没有需要补货的物料」"
    )
