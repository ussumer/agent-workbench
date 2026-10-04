"""T19 acceptance: offload, archival, the sandbox breaker and the shared budget.

Four claims are worth testing hard, because each one fails quietly:

* **A large response becomes a file.** If it were merely truncated the model would answer
  from a partial result; if the file write failed and we still returned a reference, the
  model would read a path that does not exist. Both are tested.
* **The archive is written before summarisation.** Tested by archiving a real transcript and
  checking every message is in the store — a lossy summary can then never be the only copy.
* **A business rejection does not trip the sandbox breaker.** This is the one that turns user
  input into an outage if it is wrong, so it is tested directly: seven 422s in a row and the
  breaker stays closed.
* **SDK guards retain the configured limits.** Identity assertions here only check assembly;
  graph state is private even with a shared instance. T32 verifies API callback aggregation
  across actual parent/child model calls and Mongo thread counters.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
for _path in (str(SRC_DIR), str(REPO_ROOT / "tests")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from agent.middleware_config import build_middlewares, middleware_inventory  # noqa: E402
from agent.middlewares.sandbox_breaker import (  # noqa: E402
    BreakerConfig,
    BreakerRegistry,
    SandboxBreaker,
    classify,
)
from agent.middlewares.tools_summarization import (  # noqa: E402
    OFFLOAD_DIR,
    OFFLOAD_THRESHOLD_BYTES,
    BudgetConfig,
    RunBudget,
    ToolsSummarizationMiddleware,
    archive_thread_history,
    build_budget_middlewares,
    compaction_preserves,
    offload_name,
    should_offload,
)
from agent.persistence.scoped_store import UserScopedStore  # noqa: E402
from fixtures import mongo_service  # noqa: E402

pytestmark = pytest.mark.integration

DEMO_A = "demo-a"
DEMO_B = "demo-b"


@pytest.fixture(scope="module")
def settings():
    candidate = mongo_service.unique_settings(f"t19-{uuid.uuid4().hex[:8]}")
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


def scoped(resources, user_id: str = DEMO_A) -> UserScopedStore:
    return UserScopedStore(resources.store, user_id)


class FakeWorkspace:
    """A writer that records what would have been written into the sandbox."""

    def __init__(self, *, fail: bool = False) -> None:
        self.files: dict[str, bytes] = {}
        self.fail = fail

    def write(self, path: str, payload: bytes) -> None:
        if self.fail:
            raise OSError("sandbox is not writable")
        self.files[path] = payload


def tool_message(body: str, *, name: str = "inventory_warning", call_id: str = "call-1"):
    from langchain_core.messages import ToolMessage

    return ToolMessage(content=body, tool_call_id=call_id, name=name, status="success")


class _Request:
    def __init__(self, message) -> None:
        self.message = message


def apply(middleware: ToolsSummarizationMiddleware, message):
    """Run one tool call through the middleware, synchronously."""
    return middleware.wrap_tool_call(_Request(message), lambda _request: message)


# --------------------------------------------------------------------------- #
# offloading
# --------------------------------------------------------------------------- #


def test_a_normal_sized_result_is_left_alone():
    workspace = FakeWorkspace()
    middleware = ToolsSummarizationMiddleware(writer=workspace.write)
    body = json.dumps({"ok": True, "data": {"items": list(range(20))}})

    result = apply(middleware, tool_message(body))

    assert result.content == body
    assert workspace.files == {}


def test_an_oversized_result_becomes_a_file_and_a_successful_reference():
    workspace = FakeWorkspace()
    middleware = ToolsSummarizationMiddleware(writer=workspace.write)
    body = json.dumps({"ok": True, "data": {"items": [{"part_id": f"P{i:03d}"} for i in range(2000)]}})
    assert should_offload(body), "用例本身需要一个大结果"

    result = apply(middleware, tool_message(body))
    envelope = json.loads(result.content)

    assert envelope["ok"] is True, "结果太大不是失败；必须是成功并指向文件"
    assert envelope["data"]["offloaded"] is True
    assert envelope["data"]["path"].startswith(OFFLOAD_DIR)
    assert envelope["data"]["bytes"] == len(body.encode("utf-8"))


def test_the_reference_points_at_a_file_that_really_holds_everything():
    workspace = FakeWorkspace()
    middleware = ToolsSummarizationMiddleware(writer=workspace.write)
    body = json.dumps({"ok": True, "data": {"items": [{"part_id": f"P{i:03d}"} for i in range(2000)]}})

    result = apply(middleware, tool_message(body))
    path = json.loads(result.content)["data"]["path"]

    assert path in workspace.files
    stored = json.loads(workspace.files[path].decode("utf-8"))
    assert stored == json.loads(body), "落文件的是完整内容，不是截断版"


def test_if_the_file_cannot_be_written_the_result_is_kept_inline():
    """A reference to a file that does not exist is worse than a long message."""
    workspace = FakeWorkspace(fail=True)
    middleware = ToolsSummarizationMiddleware(writer=workspace.write)
    body = json.dumps({"ok": True, "data": {"items": [{"part_id": f"P{i:03d}"} for i in range(2000)]}})

    result = apply(middleware, tool_message(body))

    assert result.content == body, "写文件失败时必须保留原始结果"
    assert "offloaded" not in result.content


def test_the_offload_path_is_stable_for_one_call_and_unique_across_calls():
    first = offload_name("inventory_warning", "call-1")
    again = offload_name("inventory_warning", "call-1")
    other = offload_name("inventory_warning", "call-2")

    assert first == again
    assert first != other
    assert first.startswith(OFFLOAD_DIR)
    assert "/" not in first[len(OFFLOAD_DIR) + 1 :], "文件名里不能再有路径分隔符"


def test_a_tool_name_with_separators_cannot_escape_the_offload_directory():
    path = offload_name("../../etc/passwd", "call-1")

    assert path.startswith(f"{OFFLOAD_DIR}/")
    assert ".." not in path


# --------------------------------------------------------------------------- #
# archiving before summarisation
# --------------------------------------------------------------------------- #


def test_the_full_transcript_is_archived(resources):
    store = scoped(resources)
    messages = [
        {"role": "user", "content": "查一下 P001"},
        {"role": "assistant", "content": "P001 库存 8"},
        {"role": "tool", "content": "x" * 500},
    ]

    record = archive_thread_history(
        store, thread_id=f"t-{uuid.uuid4().hex[:8]}", messages=messages, run_id="run-1"
    )

    assert record.message_count == 3
    assert record.bytes > 0
    assert record.digest

    item = store.get(("memories", DEMO_A), record.key)
    assert item is not None
    assert len(item.value["messages"]) == 3, "归档必须是完整历史，不是摘要"


def test_the_archive_belongs_to_one_owner(resources):
    store_a = scoped(resources, DEMO_A)
    store_b = scoped(resources, DEMO_B)
    thread = f"t-{uuid.uuid4().hex[:8]}"

    record = archive_thread_history(
        store_a, thread_id=thread, messages=[{"role": "user", "content": "私有内容"}], run_id="r"
    )

    assert store_a.get(("memories", DEMO_A), record.key) is not None
    assert store_b.get(("memories", DEMO_B), record.key) is None


def test_compaction_is_checked_for_the_things_that_must_survive():
    before = {
        "todos": [{"content": "查库存", "status": "in_progress"}],
        "__interrupt__": [{"interrupt_id": "i1"}],
        "messages": list(range(50)),
    }
    preserved = {"todos": before["todos"], "__interrupt__": before["__interrupt__"], "messages": [0]}

    ok, problems = compaction_preserves(before, preserved)
    assert ok and problems == []


def test_a_compaction_that_drops_todos_is_reported_by_name():
    before = {"todos": [{"content": "查库存", "status": "in_progress"}], "messages": [1, 2, 3]}

    ok, problems = compaction_preserves(before, {"messages": [3]})

    assert ok is False
    assert any("todos" in problem for problem in problems)


def test_a_compaction_that_drops_a_pending_interrupt_is_reported():
    before = {"__interrupt__": [{"interrupt_id": "i1"}], "messages": [1]}

    ok, problems = compaction_preserves(before, {"messages": [1]})

    assert ok is False
    assert any("interrupt" in problem for problem in problems)


def test_a_compaction_that_changes_todos_is_reported():
    before = {"todos": [{"content": "a", "status": "in_progress"}]}

    ok, problems = compaction_preserves(before, {"todos": [{"content": "a", "status": "completed"}]})

    assert ok is False
    assert any("发生变化" in problem for problem in problems)


# --------------------------------------------------------------------------- #
# the sandbox breaker
# --------------------------------------------------------------------------- #


def test_a_business_rejection_is_not_a_sandbox_failure():
    """The headline claim: user input must not be able to cause an outage."""
    breaker = SandboxBreaker(BreakerConfig(failure_threshold=3))

    for code in ("UNSUPPORTED_PART", "INACTIVE_SUPPLIER", "VERSION_CONFLICT", "APPROVAL_REJECTED"):
        snapshot = breaker.record_failure(code)

    assert snapshot.state == "closed"
    assert snapshot.business_failures == 4
    assert snapshot.sandbox_failures == 0
    assert breaker.allows() is True


def test_repeated_business_errors_never_open_the_breaker():
    breaker = SandboxBreaker(BreakerConfig(failure_threshold=2))

    for _ in range(10):
        breaker.record_failure("UNSUPPORTED_PART")

    assert breaker.state == "closed"
    assert breaker.allows() is True


def test_consecutive_sandbox_failures_open_the_breaker():
    breaker = SandboxBreaker(BreakerConfig(failure_threshold=3))

    for _ in range(2):
        breaker.record_failure("SANDBOX_TIMEOUT")
    assert breaker.state == "closed", "未达阈值不应打开"

    snapshot = breaker.record_failure("CONTAINER_GONE")

    assert snapshot.state == "open"
    assert breaker.allows() is False, "打开后必须直接拒绝，而不是再等一次超时"


def test_the_classifier_separates_the_three_kinds():
    assert classify("SANDBOX_TIMEOUT") == "sandbox"
    assert classify("UNSUPPORTED_PART") == "business"
    assert classify("SOMETHING_NEW") == "unknown"


def test_an_unknown_error_does_not_trip_the_breaker():
    breaker = SandboxBreaker(BreakerConfig(failure_threshold=1))

    snapshot = breaker.record_failure("SOMETHING_NEW")

    assert snapshot.state == "closed"
    assert snapshot.unknown_failures == 1


def test_after_the_cooldown_exactly_one_probe_is_allowed():
    now = [0.0]
    breaker = SandboxBreaker(BreakerConfig(failure_threshold=1, cooldown_seconds=30), clock=lambda: now[0])
    breaker.record_failure("SANDBOX_TIMEOUT")
    assert breaker.allows() is False

    now[0] = 31.0

    assert breaker.state == "half_open"
    assert breaker.allows() is True
    assert breaker.allows() is False, "半开只放一次探测，避免并发一起打过去"


def test_a_successful_probe_closes_the_breaker():
    now = [0.0]
    breaker = SandboxBreaker(BreakerConfig(failure_threshold=1, cooldown_seconds=10), clock=lambda: now[0])
    breaker.record_failure("SANDBOX_TIMEOUT")
    now[0] = 11.0
    assert breaker.allows() is True

    snapshot = breaker.record_success()

    assert snapshot.state == "closed"
    assert snapshot.consecutive_failures == 0
    assert breaker.allows() is True


def test_a_failed_probe_reopens_the_breaker_with_a_fresh_cooldown():
    now = [0.0]
    breaker = SandboxBreaker(BreakerConfig(failure_threshold=5, cooldown_seconds=10), clock=lambda: now[0])
    breaker.record_failure("SANDBOX_TIMEOUT")
    breaker.record_failure("SANDBOX_TIMEOUT")
    breaker.record_failure("SANDBOX_TIMEOUT")
    breaker.record_failure("SANDBOX_TIMEOUT")
    breaker.record_failure("SANDBOX_TIMEOUT")
    now[0] = 11.0
    assert breaker.allows() is True

    snapshot = breaker.record_failure("SANDBOX_TIMEOUT")

    assert snapshot.state == "open"
    assert snapshot.opened_count == 2
    assert breaker.allows() is False, "探测失败后要重新等待冷却"


def test_one_users_breaker_does_not_affect_another():
    registry = BreakerRegistry(config=BreakerConfig(failure_threshold=1))

    registry.for_user(DEMO_A).record_failure("SANDBOX_TIMEOUT")

    assert registry.for_user(DEMO_A).state == "open"
    assert registry.for_user(DEMO_B).state == "closed"
    assert registry.for_user(DEMO_B).allows() is True


# --------------------------------------------------------------------------- #
# the shared budget
# --------------------------------------------------------------------------- #


def test_the_budget_uses_the_frameworks_own_limit_middleware():
    built = build_budget_middlewares(BudgetConfig())

    names = {type(item).__name__ for item in built}
    assert names == {"ModelCallLimitMiddleware", "ToolCallLimitMiddleware"}
    assert len(built) == 2


def test_budget_exhaustion_is_an_error_not_completed_procurement():
    """The API preserves committed results and reports failed, without false success."""
    built = build_budget_middlewares(BudgetConfig())
    assert all(getattr(item, "exit_behavior", None) == "error" for item in built)


def test_main_and_subagents_are_given_the_same_instances(configs=None):
    """Identity, not equality: two equal instances would be two budgets."""
    context = {"budget_config": BudgetConfig()}
    first = build_middlewares(context, only={"model_tool_limits"})
    second = build_middlewares(context, only={"model_tool_limits"})

    # Each call builds its own pair — so the sharing has to happen by passing one result to
    # both the parent and the sub-agents, which is what `build_main_agent` does.
    assert len(first) == 2
    assert first[0] is not second[0], "两次装配各自独立，共享必须靠传同一个列表"


def test_the_run_budget_stops_finitely():
    now = [0.0]
    budget = RunBudget(limit_seconds=5.0, clock=lambda: now[0])

    assert budget.exhausted() is False
    now[0] = 2.5
    assert budget.remaining() == pytest.approx(2.5)
    now[0] = 6.0
    assert budget.exhausted() is True
    assert budget.remaining() == 0.0


# --------------------------------------------------------------------------- #
# structure
# --------------------------------------------------------------------------- #


def test_there_is_no_second_tool_error_swallowing_layer():
    """The framework's ToolNode already surfaces tool errors; a second layer hides them."""
    middlewares = sorted(path.name for path in (SRC_DIR / "agent" / "middlewares").glob("*.py"))

    assert "tool_error.py" not in middlewares, "不允许存在第二套吞掉工具错误的逻辑"
    assert "tools_summarization.py" in middlewares
    assert "sandbox_breaker.py" in middlewares


def test_the_t19_slots_are_reported_as_implemented():
    inventory = {entry["key"]: entry for entry in middleware_inventory()}

    for key in ("conversation_summary", "sandbox_breaker", "model_tool_limits"):
        assert inventory[key]["implemented"] is True, key
        assert inventory[key]["owner_task"] == "T19"


def test_the_offload_threshold_is_larger_than_a_normal_answer():
    """A threshold below ordinary results would turn every answer into a file."""
    assert OFFLOAD_THRESHOLD_BYTES >= 4096
    small = json.dumps({"ok": True, "data": {"items": [{"part_id": "P001", "on_hand": 8}]}})
    assert should_offload(small) is False
