"""T38 real Mongo + official OpenSandbox Python kernel; no live model calls."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent.planning.kernel import KernelService
from agent.planning.kernel_protocol import MAX_PREVIEW_BYTES, KernelError
from agent.tools.planning_kernel import build_kernel_tools

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fixtures import mongo_service, sandbox_service  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def stack():
    settings = mongo_service.unique_settings("t38_" + uuid.uuid4().hex[:8])
    resources = mongo_service.start_resources(settings)
    try:
        with sandbox_service.running_pool(settings, warm_pool_size=0) as (manager, _factory):
            yield resources.database, manager, settings
    finally:
        resources.close()
        mongo_service.drop_test_database(settings)


@pytest.fixture()
def kernel(stack):
    database, manager, settings = stack
    thread = "kernel-" + uuid.uuid4().hex[:10]
    for owner, tid in (("kernel-a", thread), ("kernel-b", thread + "-b")):
        database.threads.insert_one({"owner_user_id": owner, "thread_id": tid})
    service = KernelService(database, manager.get_or_create)
    yield service, "kernel-a", thread, manager, settings
    for row in list(service.sessions.find({"thread_id": {"$in": [thread, thread + "-b"]}})):
        service.close(row["owner_user_id"], row["thread_id"], session=row["session"])


def run(kernel, code, **kwargs):
    service, owner, thread, *_ = kernel
    result = service.execute(owner, thread, code, **kwargs)
    assert result["status"] == "completed", result
    return result


def wait_operation(service, thread, *, initialized=True):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        row = service.executions.find_one({"thread_id": thread, "status": "running"})
        if row and (not initialized or row.get("execution_id")):
            return row
        time.sleep(0.05)
    raise AssertionError("kernel execution did not start")


def test_kernel_live_objects_and_quotes_survive_multiple_executions(kernel):
    first = run(kernel, "quotes = {'A': 2500}; constraints = {'budget': 10000}; plans = []; checks = []\nprint(id(quotes))")
    second = run(kernel, "plans.append({'quantity': constraints['budget'] // quotes['A']})\nchecks.append(plans[-1]['quantity'] == 4)\nprint(id(quotes))\nprint(checks)")
    assert first["context_id"] == second["context_id"]
    assert first["stdout"].strip() in second["stdout"]
    assert "True" in second["stdout"]


def test_budget_change_uses_existing_objects_and_only_recomputes_allocation(kernel):
    run(kernel, "quotes = {'A': 2550, 'B': 6800, 'C': 1750}; quote_reads = 1\nconstraints = {'budget': 250000}\nmandatory = 42*quotes['A'] + 15*quotes['B']\nplans = {'optional': (constraints['budget']-mandatory)//quotes['C']}\nchecks = {'required_cost': mandatory}\nprint(id(quotes))")
    service, owner, thread, *_ = kernel
    snapshot = service.checkpoint(owner, thread, ("quotes", "quote_reads", "constraints", "plans", "checks", "mandatory"))
    assert snapshot["status"] == "completed", snapshot
    result = run(kernel, "constraints['budget'] = 220000\nplans['optional'] = (constraints['budget']-mandatory)//quotes['C']\nprint(quote_reads, checks['required_cost'], plans['optional'])")
    assert result["stdout"].strip() == "1 209100 6"
    saved = json.loads(service.sessions.find_one({"thread_id": thread})["checkpoint"])
    assert saved["values"]["plans"]["optional"] == 6
    assert saved["values"]["quote_reads"] == 1


def test_named_sessions_and_owners_have_distinct_contexts(kernel):
    service, owner, thread, *_ = kernel
    a = run(kernel, "secret = 'owner-a-only'", session="first")
    b = service.execute("kernel-b", thread + "-b", "print('secret' in globals())", session="first")
    c = run(kernel, "print('secret' in globals())", session="second")
    assert b["status"] == "completed" and b["stdout"].strip() == "False", b
    assert c["stdout"].strip() == "False"
    assert len({a["context_id"], b["context_id"], c["context_id"]}) == 3
    assert service.status(owner, thread, session="first")["sandbox_id"] != service.status("kernel-b", thread + "-b", session="first")["sandbox_id"]


def test_thread_context_isolation_inside_same_owner_sandbox(kernel):
    service, owner, thread, manager, _ = kernel
    other = thread + "-other"
    service.database.threads.insert_one({"thread_id": other, "owner_user_id": owner})
    run(kernel, "value = 711")
    try:
        result = service.execute(owner, other, "print('value' in globals())")
        assert result["status"] == "completed" and result["stdout"].strip() == "False", result
        assert service.status(owner, thread)["sandbox_id"] == manager.get_or_create(owner).id
    finally:
        service.close(owner, other)


def test_new_service_process_reconnects_existing_context_without_replaying_code(kernel):
    service, owner, thread, _manager, settings = kernel
    first = run(kernel, "values = [31]; values.append(32)")
    code = '''
import json, sys, faulthandler
faulthandler.enable()
faulthandler.dump_traceback_later(20, repeat=True)
sys.path[:0] = ['src', 'tests']
from pymongo import MongoClient
from agent.backends.sandbox_manager import SandboxManager, MongoSandboxRegistry, OpenSandboxFactory
from agent.planning.kernel import KernelService
from fixtures.sandbox_service import control_settings
data = json.loads(sys.argv[1])
with MongoClient(data['mongo_uri']) as client:
    db = client[data['database']]
    manager = SandboxManager(factory=OpenSandboxFactory(control_settings()), registry=MongoSandboxRegistry(db), warm_pool_size=0)
    service = KernelService(db, manager.get_or_create)
    result = service.execute(data['owner'], data['thread'], 'print(values)')
    print(json.dumps(result))
'''
    inputs = {"mongo_uri": settings.mongo_uri, "database": settings.database, "owner": owner, "thread": thread}
    try:
        completed = subprocess.run([sys.executable, "-c", code, json.dumps(inputs)],
                                   capture_output=True, text=True, encoding="utf-8", timeout=90,
                                   env=mongo_service.python_env())
    except subprocess.TimeoutExpired as failure:
        trace = failure.stderr or b""
        if isinstance(trace, bytes):
            trace = trace.decode("utf-8", "replace")
        raise AssertionError("kernel reconnection subprocess timed out:\n" + trace) from failure
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["status"] == "completed" and result["stdout"].strip() == "[31, 32]", result
    assert result["context_id"] == first["context_id"] and result["recovery"] == "reconnected"


def test_missing_context_restores_json_and_exposes_nonserializable_rebuild(kernel):
    service, owner, thread, manager, _ = kernel
    first = run(kernel, "quotes = {'A': {'price': '25.50', 'source_revision': 2}}\nplanner = lambda x: x + 1")
    snap = service.checkpoint(owner, thread, ("quotes", "planner"))
    assert snap["status"] == "completed" and snap["rebuild_required"] == ["planner"], snap
    manager.get_or_create(owner).kernel_delete_context(first["context_id"])
    restored = run(kernel, "print(quotes['A']['price'], quotes['A']['source_revision'], 'planner' in globals())")
    assert restored["recovery"] == "restored_json" and restored["rebuild_required"] == ["planner"]
    assert restored["stdout"].strip() == "25.50 2 False"
    assert restored["context_id"] != first["context_id"]


def test_cancel_interrupts_real_code_and_discards_partial_mutation(kernel):
    service, owner, thread, *_ = kernel
    run(kernel, "values = [1]")
    assert service.checkpoint(owner, thread, ("values",))["status"] == "completed"
    with ThreadPoolExecutor() as executor:
        future = executor.submit(service.execute, owner, thread,
                                 "import time\nvalues.append(99)\nwhile True: time.sleep(0.05)", timeout=60)
        op = wait_operation(service, thread)
        assert service.cancel(owner, thread, op["operation_id"])
        result = future.result(timeout=20)
    assert result["status"] == "cancelled", result
    assert service.executions.find_one({"operation_id": op["operation_id"]})["status"] == "cancelled"
    after = run(kernel, "print(values)")
    assert after["stdout"].strip() == "[1]" and after["recovery"] == "restored_json"
    assert not service.cancel(owner, thread, after["operation_id"])


def test_timeout_is_not_success_and_kernel_is_usable_after_real_interruption(kernel):
    service, owner, thread, *_ = kernel
    run(kernel, "marker = 17")
    result = service.execute(owner, thread, "import time\ntime.sleep(20)\nprint('late success')", timeout=1)
    assert result["status"] == "cancelled", result
    assert service.executions.find_one({"operation_id": result["operation_id"]})["cancel_reason"] == "timeout"
    after = run(kernel, "print(21)")
    assert after["stdout"].strip() == "21"


def test_kernel_and_shell_share_owner_execution_queue(kernel):
    service, owner, thread, manager, _ = kernel
    proxy = manager.get_or_create(owner)
    with ThreadPoolExecutor() as executor:
        future = executor.submit(service.execute, owner, thread,
                                 "import time\ntime.sleep(1.5)\nprint('kernel complete')")
        wait_operation(service, thread)
        started = time.monotonic()
        result = proxy.execute("echo shell-complete")
        elapsed = time.monotonic() - started
        kernel_result = future.result(timeout=20)
    assert result.exit_code == 0 and "shell-complete" in result.output
    assert elapsed > 1 and kernel_result["status"] == "completed"


def test_bounded_preview_preserves_full_output_and_owner_access(kernel):
    service, owner, thread, *_ = kernel
    result = run(kernel, "print('Z' * 50000)")
    assert result["truncated"] and len(result["stdout"].encode()) <= MAX_PREVIEW_BYTES
    query = {"operation_id": result["operation_id"], "kind": "stdout"}
    full = b"".join(bytes(c["content"]) for c in service.outputs.find(query).sort("seq", 1))
    assert full == b"Z" * 50000 + b"\n"
    page = service.read_output(owner, thread, result["operation_id"], offset=MAX_PREVIEW_BYTES)
    assert page["content"] == "Z" * (50000 - MAX_PREVIEW_BYTES) + "\n"
    with pytest.raises(KernelError, match="not in this thread"):
        service.read_output("kernel-b", thread + "-b", result["operation_id"])


def test_foreign_thread_cannot_start_or_cancel_kernel_operation(kernel):
    service, owner, thread, *_ = kernel
    with pytest.raises(KernelError, match="not owned"):
        service.execute(owner, "nonexistent-thread", "pass")
    assert service.sessions.find_one({"thread_id": "nonexistent-thread"}) is None


def test_snippet_failure_does_not_commit_checkpoint_or_claim_success(kernel):
    service, owner, thread, *_ = kernel
    run(kernel, "values = [1]")
    assert service.checkpoint(owner, thread, ("values",))["status"] == "completed"
    bad = service.execute(owner, thread, "values.append(5)\nraise ValueError('business calculation failed')")
    assert bad["status"] == "failed" and bad["error_code"] == "KERNEL_CODE_ERROR", bad
    assert "business calculation failed" in service.read_output(owner, thread, bad["operation_id"], kind="error")["content"]
    assert run(kernel, "print(values)")["stdout"].strip() == "[1]"


async def test_async_tool_cancellation_stops_the_actual_kernel_operation(kernel):
    service, owner, thread, *_ = kernel
    tools = build_kernel_tools(service)
    tool = next(t for t in tools if t.name == "kernel_execute")
    pending = asyncio.create_task(tool.ainvoke({"code": "import time\nwhile True: time.sleep(0.05)"}, config={
        "configurable": {"owner_user_id": owner, "thread_id": thread}
    }))
    op = await asyncio.to_thread(wait_operation, service, thread)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        row = service.executions.find_one({"operation_id": op["operation_id"]})
        if row["status"] != "running":
            break
        await asyncio.sleep(0.05)
    assert row["status"] == "cancelled", row
    result = await asyncio.to_thread(service.execute, owner, thread, "print(41)")
    assert result["status"] == "completed" and result["stdout"].strip() == "41", result


def test_container_loss_restores_json_in_the_recovered_generation(kernel):
    service, owner, thread, manager, _ = kernel
    first = run(kernel, "quotes = {'A': 2550}; constraints = {'revision': 2}")
    assert service.checkpoint(owner, thread, ("quotes", "constraints"))["status"] == "completed"
    proxy = manager.get_or_create(owner)
    old_id, generation = proxy.id, proxy.generation
    proxy.backend.sandbox.kill()
    manager.recover(owner)
    assert proxy.id != old_id and proxy.generation == generation + 1
    after = run(kernel, "print(quotes['A'], constraints['revision'])")
    assert after["context_id"] != first["context_id"]
    assert after["stdout"].strip() == "2550 2" and after["recovery"] == "restored_json"


def test_late_result_during_generation_replacement_cannot_commit_success(kernel):
    service, owner, thread, manager, _ = kernel
    run(kernel, "values = [1]")
    assert service.checkpoint(owner, thread, ("values",))["status"] == "completed"
    proxy = manager.get_or_create(owner)
    with ThreadPoolExecutor() as executor:
        future = executor.submit(service.execute, owner, thread,
                                 "import time\ntime.sleep(1.5)\nvalues.append(99)\nprint('late')")
        op = wait_operation(service, thread)
        proxy.begin_replacement()
        manager.recover(owner)
        result = future.result(timeout=20)
    assert result["status"] == "failed", result
    assert service.executions.find_one({"operation_id": op["operation_id"]})["status"] == "failed"
    assert run(kernel, "print(values)")["stdout"].strip() == "[1]"


async def test_async_cancel_while_queued_never_starts_actor_code(kernel):
    service, owner, thread, *_ = kernel
    tool = next(t for t in build_kernel_tools(service) if t.name == "kernel_execute")
    first = asyncio.create_task(tool.ainvoke({"code": "import time\ntime.sleep(2)"}, config={
        "configurable": {"owner_user_id": owner, "thread_id": thread}
    }))
    await asyncio.to_thread(wait_operation, service, thread)
    second = asyncio.create_task(tool.ainvoke({"session": "queued", "code": "raise AssertionError('must not start')"}, config={
        "configurable": {"owner_user_id": owner, "thread_id": thread}
    }))
    deadline = time.monotonic() + 10
    row = None
    while time.monotonic() < deadline:
        row = service.executions.find_one({"thread_id": thread, "session": "queued", "status": "running"})
        if row is not None:
            break
        await asyncio.sleep(0.02)
    assert row is not None
    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second
    assert json.loads(await first)["ok"]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        after = service.executions.find_one({"operation_id": row["operation_id"]})
        if after["status"] != "running":
            break
        await asyncio.sleep(0.02)
    assert after["status"] == "cancelled" and not after.get("execution_id"), after


def test_registration_failure_releases_lease_without_losing_live_state(kernel, monkeypatch):
    service, owner, thread, *_ = kernel
    original = run(kernel, "values = [17]")
    def unavailable(*args, **kwargs):
        raise RuntimeError("injected Mongo registration failure")
    with monkeypatch.context() as patch:
        patch.setattr(service.executions, "insert_one", unavailable)
        with pytest.raises(RuntimeError, match="registration failure"):
            service.execute(owner, thread, "values.append(99)")
    after = run(kernel, "print(values)")
    assert after["context_id"] == original["context_id"]
    assert after["stdout"].strip() == "[17]"


def test_checkpoint_commit_failure_never_publishes_completed_and_restores_prior_data(kernel, monkeypatch):
    service, owner, thread, *_ = kernel
    run(kernel, "values = [1]", checkpoint_names=("values",))
    update = service.sessions.update_one
    observed = []
    def fail_commit(selector, changes, **kwargs):
        if "checkpoint" in changes.get("$set", {}):
            operation = service.executions.find_one({"thread_id": thread, "status": "committing"})
            assert operation is not None
            observed.append(operation["operation_id"])
            raise RuntimeError("injected Mongo checkpoint failure")
        return update(selector, changes, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(service.sessions, "update_one", fail_commit)
        result = service.execute(owner, thread, "values.append(99)")
    assert result["status"] == "failed" and observed == [result["operation_id"]]
    assert service.executions.find_one({"operation_id": result["operation_id"]})["status"] == "failed"
    assert run(kernel, "print(values)")["stdout"].strip() == "[1]"


def test_busy_session_rejects_second_operation_and_expired_lease_recovers_checkpoint(kernel):
    from datetime import UTC, datetime, timedelta
    service, owner, thread, *_ = kernel
    run(kernel, "values = [1]", checkpoint_names=("values",))
    scope = service._scope(owner, thread, "analysis")
    old = service._lease(scope, 60)
    with pytest.raises(KernelError, match="active operation"):
        service.execute(owner, thread, "values.append(99)")
    service.sessions.update_one({"_id": old["_id"]}, {"$set": {
        "lease_until": datetime.now(UTC) - timedelta(seconds=1),
    }})
    recovered = run(kernel, "print(values)")
    assert recovered["recovery"] == "restored_json" and recovered["stdout"].strip() == "[1]"
    stale = service.sessions.update_one({"_id": old["_id"], "lease_token": old["lease_token"]},
                                       {"$set": {"checkpoint": "corrupt"}})
    assert stale.matched_count == 0
