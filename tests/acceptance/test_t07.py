"""T07 acceptance: three kinds of persistence, on real MongoDB.

The suite runs against a private ``rush_harness_test_*`` database and proves:

* graph state written by the official ``MongoDBSaver`` is recovered by a
  *separate process* — not by reusing an in-memory object;
* long-term files round-trip through the official MongoDB-backed ``MongoDBStore``
  with per-user namespace isolation;
* application records (threads, display messages, runs, pending actions) enforce
  ownership, de-duplicate by id, and never stand in for a checkpoint.

An unreachable MongoDB is reported as a failure, never replaced by an in-memory
store.
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import TypedDict

import pytest
from langgraph.graph import END, START, StateGraph

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures import mongo_service  # noqa: E402

SRC_DIR = mongo_service.SRC_DIR
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agent.persistence import (  # noqa: E402
    APPLICATION_COLLECTIONS,
    INDEX_SPECS,
    ApplicationRepository,
    NamespaceViolation,
    OwnershipViolation,
    RequestConflict,
    UserScopedStore,
    describe_indexes,
    memories_namespace,
    user_skill_key,
)
from api_view.web_config import DEFAULT_TEST_DATABASE  # noqa: E402

pytestmark = pytest.mark.integration

OWNER = "demo-a"
OTHER = "demo-b"


class CounterState(TypedDict):
    count: int
    note: str


def _bump(state: CounterState) -> dict:
    return {"count": state["count"] + 1, "note": state.get("note", "") + "!"}


def build_counter_graph(checkpointer):
    """A tiny real LangGraph so the checkpointer is exercised, not simulated."""
    graph = StateGraph(CounterState)
    graph.add_node("bump", _bump)
    graph.add_edge(START, "bump")
    graph.add_edge("bump", END)
    return graph.compile(checkpointer=checkpointer)


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def settings():
    candidate = mongo_service.unique_settings(f"t07-{uuid.uuid4().hex[:8]}")
    mongo_service.require_reachable(candidate)
    yield candidate
    mongo_service.drop_test_database(candidate)


@pytest.fixture(scope="module")
def resources(settings):
    started = mongo_service.start_resources(settings)
    yield started
    started.close()


@pytest.fixture(autouse=True)
def clean_state(resources):
    """Give every test a blank slate inside the private test database."""
    for name in (*APPLICATION_COLLECTIONS, "checkpoints", "checkpoint_writes", "persistent-store"):
        resources.database[name].delete_many({})
    yield


@pytest.fixture()
def repository(resources) -> ApplicationRepository:
    return ApplicationRepository(resources.database)


def seed_thread(repository: ApplicationRepository, thread_id: str, owner: str = OWNER) -> str:
    repository.ensure_thread(owner_user_id=owner, thread_id=thread_id, title="采购对话")
    return thread_id


# --------------------------------------------------------------------------- #
# collections and indexes
# --------------------------------------------------------------------------- #


def test_application_indexes_match_the_declared_manifest(resources):
    actual = describe_indexes(resources.database)

    for spec in INDEX_SPECS:
        assert spec.name in actual[spec.collection], f"{spec.collection}.{spec.name} 缺失"

    unique_names = {spec.name for spec in INDEX_SPECS if spec.unique}
    assert "uk_threads_thread_id" in unique_names
    assert "uk_runs_owner_request" in unique_names
    assert "uk_display_messages_thread_message" in unique_names
    assert "uk_pending_actions_interrupt_call" in unique_names
    assert len(APPLICATION_COLLECTIONS) == 4


def test_acceptance_runs_against_its_own_private_database(resources):
    assert resources.settings.database.startswith(DEFAULT_TEST_DATABASE)
    assert resources.settings.database != "rush_harness_demo"


# --------------------------------------------------------------------------- #
# threads
# --------------------------------------------------------------------------- #


def test_creating_the_same_thread_twice_keeps_one_record(repository):
    seed_thread(repository, "thread-1")

    thread = repository.ensure_thread(owner_user_id=OWNER, thread_id="thread-1", title="改标题")

    assert thread.thread_id == "thread-1"
    assert repository.list_threads(owner_user_id=OWNER)[1] == 1


def test_a_thread_cannot_be_taken_over_by_another_user(repository):
    seed_thread(repository, "thread-owned")

    with pytest.raises(OwnershipViolation):
        repository.ensure_thread(owner_user_id=OTHER, thread_id="thread-owned")

    assert repository.get_thread(owner_user_id=OTHER, thread_id="thread-owned") is None


def test_threads_are_listed_per_owner_newest_first(repository):
    for index in range(3):
        seed_thread(repository, f"thread-{index}")
        repository.ensure_thread(owner_user_id=OWNER, thread_id=f"thread-{index}")

    threads, total = repository.list_threads(owner_user_id=OWNER, page=1, page_size=2)

    assert total == 3
    assert len(threads) == 2
    assert all(thread.owner_user_id == OWNER for thread in threads)
    assert repository.list_threads(owner_user_id=OTHER)[1] == 0


def test_deleting_a_thread_only_removes_that_owners_records(repository):
    seed_thread(repository, "thread-delete")
    repository.upsert_display_message(
        owner_user_id=OWNER, thread_id="thread-delete", message_id="m1", seq=1,
        role="user", content="你好",
    )

    removed = repository.delete_thread(owner_user_id=OWNER, thread_id="thread-delete")

    assert removed["threads"] == 1
    assert removed["display_messages"] == 1
    assert repository.get_thread(owner_user_id=OWNER, thread_id="thread-delete") is None


# --------------------------------------------------------------------------- #
# display messages
# --------------------------------------------------------------------------- #


def test_redelivering_a_display_message_does_not_duplicate_it(repository):
    seed_thread(repository, "thread-msg")

    first = repository.upsert_display_message(
        owner_user_id=OWNER, thread_id="thread-msg", message_id="msg-1", seq=1,
        role="user", content="查询库存预警",
    )
    second = repository.upsert_display_message(
        owner_user_id=OWNER, thread_id="thread-msg", message_id="msg-1", seq=1,
        role="user", content="查询库存预警",
    )

    assert first == "inserted"
    assert second == "unchanged"
    assert repository.count_display_messages(owner_user_id=OWNER, thread_id="thread-msg") == 1


def test_the_same_message_id_with_different_content_conflicts(repository):
    seed_thread(repository, "thread-conflict")
    repository.upsert_display_message(
        owner_user_id=OWNER, thread_id="thread-conflict", message_id="msg-2", seq=1,
        role="user", content="第一条",
    )

    with pytest.raises(RequestConflict):
        repository.upsert_display_message(
            owner_user_id=OWNER, thread_id="thread-conflict", message_id="msg-2", seq=1,
            role="user", content="被篡改的内容",
        )

    assert repository.count_display_messages(owner_user_id=OWNER, thread_id="thread-conflict") == 1


def test_display_history_and_checkpoints_are_separate(resources, repository):
    """Writing history must not create graph state, and vice versa."""
    seed_thread(repository, "thread-separate")
    repository.upsert_display_message(
        owner_user_id=OWNER, thread_id="thread-separate", message_id="msg-3", seq=1,
        role="assistant", content="预警有 P001/P003/P004",
    )

    assert resources.database["checkpoints"].count_documents({}) == 0

    graph = build_counter_graph(resources.checkpointer)
    graph.invoke({"count": 0, "note": ""}, config={"configurable": {"thread_id": "thread-separate"}})

    assert resources.database["checkpoints"].count_documents({}) > 0
    assert repository.count_display_messages(owner_user_id=OWNER, thread_id="thread-separate") == 1


# --------------------------------------------------------------------------- #
# runs
# --------------------------------------------------------------------------- #


def test_the_same_request_id_replays_the_same_run(repository):
    seed_thread(repository, "thread-run")

    first = repository.reserve_run(
        owner_user_id=OWNER, thread_id="thread-run", run_id="run-1",
        request_id="req-1", request_digest="digest-a",
    )
    second = repository.reserve_run(
        owner_user_id=OWNER, thread_id="thread-run", run_id="run-2",
        request_id="req-1", request_digest="digest-a",
    )

    assert first.is_new is True
    assert second.replayed is True
    assert second.run_id == first.run_id == "run-1"
    assert len(repository.list_runs(owner_user_id=OWNER, thread_id="thread-run")) == 1


def test_the_same_request_id_with_a_different_body_conflicts(repository):
    seed_thread(repository, "thread-run-2")
    repository.reserve_run(
        owner_user_id=OWNER, thread_id="thread-run-2", run_id="run-3",
        request_id="req-2", request_digest="digest-a",
    )

    with pytest.raises(RequestConflict):
        repository.reserve_run(
            owner_user_id=OWNER, thread_id="thread-run-2", run_id="run-4",
            request_id="req-2", request_digest="digest-b",
        )


def test_concurrent_reservations_produce_exactly_one_run(repository):
    seed_thread(repository, "thread-race")
    barrier = Barrier(4)

    def reserve(index: int):
        barrier.wait(timeout=30)
        return repository.reserve_run(
            owner_user_id=OWNER, thread_id="thread-race", run_id=f"run-race-{index}",
            request_id="req-race", request_digest="digest-same",
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(reserve, range(4)))

    assert len({outcome.run_id for outcome in outcomes}) == 1
    assert sum(1 for outcome in outcomes if outcome.is_new) == 1
    assert len(repository.list_runs(owner_user_id=OWNER, thread_id="thread-race")) == 1


def test_a_run_status_update_is_owner_scoped(repository):
    seed_thread(repository, "thread-status")
    repository.reserve_run(
        owner_user_id=OWNER, thread_id="thread-status", run_id="run-5",
        request_id="req-3", request_digest="digest-a",
    )

    assert repository.update_run_status(owner_user_id=OWNER, run_id="run-5", status="completed")
    assert repository.get_run(owner_user_id=OWNER, run_id="run-5")["status"] == "completed"
    assert repository.get_run(owner_user_id=OTHER, run_id="run-5") is None
    assert repository.update_run_status(owner_user_id=OTHER, run_id="run-5", status="failed") is False


# --------------------------------------------------------------------------- #
# pending actions
# --------------------------------------------------------------------------- #


def test_a_pending_action_is_recorded_once(repository):
    seed_thread(repository, "thread-approval")

    first = repository.save_pending_action(
        owner_user_id=OWNER, thread_id="thread-approval", interrupt_id="int-1",
        tool_call_id="call-1", tool_name="order_create", action_digest="sha-a",
        payload={"supplier_id": "S001"},
    )
    second = repository.save_pending_action(
        owner_user_id=OWNER, thread_id="thread-approval", interrupt_id="int-1",
        tool_call_id="call-1", tool_name="order_create", action_digest="sha-a",
        payload={"supplier_id": "S001"},
    )

    assert first == "inserted"
    assert second == "unchanged"
    assert len(repository.list_pending_actions(owner_user_id=OWNER, thread_id="thread-approval")) == 1


def test_a_pending_action_digest_cannot_change_under_the_same_interrupt(repository):
    seed_thread(repository, "thread-digest")
    repository.save_pending_action(
        owner_user_id=OWNER, thread_id="thread-digest", interrupt_id="int-2",
        tool_call_id="call-2", tool_name="order_create", action_digest="sha-original",
        payload={"quantity": 50},
    )

    with pytest.raises(RequestConflict):
        repository.save_pending_action(
            owner_user_id=OWNER, thread_id="thread-digest", interrupt_id="int-2",
            tool_call_id="call-2", tool_name="order_create", action_digest="sha-changed",
            payload={"quantity": 60},
        )


def test_a_pending_action_can_only_be_decided_once(repository):
    seed_thread(repository, "thread-decide")
    repository.save_pending_action(
        owner_user_id=OWNER, thread_id="thread-decide", interrupt_id="int-3",
        tool_call_id="call-3", tool_name="order_create", action_digest="sha-a",
        payload={"quantity": 50},
    )

    first = repository.decide_pending_action(
        owner_user_id=OWNER, interrupt_id="int-3", status="approved"
    )
    second = repository.decide_pending_action(
        owner_user_id=OWNER, interrupt_id="int-3", status="rejected"
    )

    assert first is not None and first["status"] == "approved"
    assert second is None, "第二次决定必须失败，条件更新只允许一次状态迁移"
    assert repository.get_pending_action(owner_user_id=OWNER, interrupt_id="int-3")[
        "status"
    ] == "approved"


def test_another_user_cannot_decide_a_pending_action(repository):
    seed_thread(repository, "thread-decide-2")
    repository.save_pending_action(
        owner_user_id=OWNER, thread_id="thread-decide-2", interrupt_id="int-4",
        tool_call_id="call-4", tool_name="order_update", action_digest="sha-a",
        payload={"quantity": 60},
    )

    assert (
        repository.decide_pending_action(
            owner_user_id=OTHER, interrupt_id="int-4", status="approved"
        )
        is None
    )


# --------------------------------------------------------------------------- #
# checkpointer: cross-process recovery
# --------------------------------------------------------------------------- #


def test_the_checkpointer_persists_graph_state(resources):
    """Partial input accumulates on top of the checkpoint; full input overwrites it."""
    graph = build_counter_graph(resources.checkpointer)
    config = {"configurable": {"thread_id": "thread-graph"}}

    graph.invoke({"count": 0, "note": ""}, config=config)
    # Supplying a key replaces that key; omitting it keeps the checkpointed value.
    graph.invoke({"count": 5}, config=config)
    graph.invoke({}, config=config)

    state = graph.get_state(config)
    assert state.values["count"] == 7
    assert state.values["note"] == "!!!"


def test_different_threads_do_not_share_checkpoint_state(resources):
    graph = build_counter_graph(resources.checkpointer)

    graph.invoke({"count": 0, "note": ""}, config={"configurable": {"thread_id": "thread-a"}})
    graph.invoke({"count": 0, "note": ""}, config={"configurable": {"thread_id": "thread-b"}})
    graph.invoke({}, config={"configurable": {"thread_id": "thread-b"}})

    first = graph.get_state({"configurable": {"thread_id": "thread-a"}})
    second = graph.get_state({"configurable": {"thread_id": "thread-b"}})

    assert first.values["count"] == 1
    assert second.values["count"] == 2, "thread-b 的第二次运行应从自己的 checkpoint 继续"


RESTART_PROBE = """
import json, sys
from typing import TypedDict
from langgraph.graph import END, START, StateGraph


class CounterState(TypedDict):
    count: int
    note: str


def bump(state):
    return {"count": state["count"] + 1, "note": state.get("note", "") + "!"}


def main() -> int:
    uri, database, thread_id = sys.argv[1], sys.argv[2], sys.argv[3]
    from api_view.web_config import MongoResources, PersistenceSettings

    resources = MongoResources(
        PersistenceSettings(mongo_uri=uri, database=database)
    ).start()
    try:
        graph = StateGraph(CounterState)
        graph.add_node("bump", bump)
        graph.add_edge(START, "bump")
        graph.add_edge("bump", END)
        compiled = graph.compile(checkpointer=resources.checkpointer)
        snapshot = compiled.get_state({"configurable": {"thread_id": thread_id}})
        print(json.dumps({"values": dict(snapshot.values), "checkpoint_id": snapshot.config["configurable"].get("checkpoint_id")}))
    finally:
        resources.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""


def test_graph_state_is_recovered_by_a_separate_process(resources, tmp_path: Path):
    """The strongest evidence available: a different interpreter reads the state back."""
    thread_id = "thread-restart"
    graph = build_counter_graph(resources.checkpointer)
    graph.invoke({"count": 7, "note": "seed"}, config={"configurable": {"thread_id": thread_id}})

    probe = tmp_path / "restart_probe.py"
    probe.write_text(RESTART_PROBE, encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            str(probe),
            resources.settings.mongo_uri,
            resources.settings.database,
            thread_id,
        ],
        capture_output=True,
        text=True,
        timeout=180,
        env=mongo_service.python_env(),
        check=False,
    )
    assert completed.returncode == 0, completed.stderr

    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["values"]["count"] == 8
    assert payload["values"]["note"] == "seed!"
    assert payload["checkpoint_id"], "跨进程读回必须来自持久 checkpoint，而不是内存对象"


# --------------------------------------------------------------------------- #
# store: preferences and skills
# --------------------------------------------------------------------------- #


def test_the_store_round_trips_user_preferences(resources):
    namespace = memories_namespace(OWNER)
    resources.store.put(namespace, "preferences.md", {"language": "zh-CN", "currency": "CNY"})

    item = resources.store.get(namespace, "preferences.md")

    assert item is not None
    assert item.value["language"] == "zh-CN"
    assert item.namespace == namespace


def test_the_store_isolates_users_by_namespace(resources):
    owner_store = UserScopedStore(resources.store, OWNER)
    other_store = UserScopedStore(resources.store, OTHER)

    owner_store.put(memories_namespace(OWNER), "preferences.md", {"language": "zh-CN"})

    assert owner_store.get(memories_namespace(OWNER), "preferences.md") is not None
    assert other_store.get(memories_namespace(OTHER), "preferences.md") is None


def test_the_scoped_store_refuses_another_users_namespace(resources):
    other_store = UserScopedStore(resources.store, OTHER)

    with pytest.raises(NamespaceViolation):
        other_store.get(memories_namespace(OWNER), "preferences.md")


def test_the_scoped_store_refuses_another_users_skill_prefix(resources):
    owner_store = UserScopedStore(resources.store, OWNER)
    other_store = UserScopedStore(resources.store, OTHER)
    skills = ("skills",)

    owner_key = user_skill_key(
        OWNER, "procurement-analyst", "reorder-cost-summary", "1.0.0", "SKILL.md"
    )
    owner_store.put(skills, owner_key, {"size": 10})

    with pytest.raises(NamespaceViolation):
        other_store.get(skills, owner_key)

    assert owner_key in owner_store.list_keys(skills, limit=100)
    assert owner_key not in other_store.list_keys(skills, limit=100), (
        "共享 ('skills',) namespace 也不能泄露其他用户的 key"
    )
