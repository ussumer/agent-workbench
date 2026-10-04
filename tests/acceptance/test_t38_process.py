"""Real Mongo/OpenSandbox independent processes; no paid or scripted model."""

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

from agent.planning.computation import ComputationService
from agent.planning.computation_protocol import MAX_PREVIEW_BYTES, ComputationError
from agent.tools.planning_computation import build_computation_tools

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fixtures import mongo_service, sandbox_service  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def stack():
    settings = mongo_service.unique_settings("t38_process_" + uuid.uuid4().hex[:8])
    resources = mongo_service.start_resources(settings)
    try:
        with sandbox_service.running_pool(settings, warm_pool_size=0) as (manager, _):
            yield resources.database, manager, settings
    finally:
        resources.close()
        mongo_service.drop_test_database(settings)


@pytest.fixture()
def computation(stack):
    db, manager, settings = stack
    owner, thread = "computation-a", "computation-" + uuid.uuid4().hex[:8]
    db.threads.insert_one({"owner_user_id": owner, "thread_id": thread})
    return ComputationService(db, manager.get_or_create), owner, thread, manager, settings


def run(computation, code, **kwargs):
    service, owner, thread, *_ = computation
    result = service.execute(owner, thread, code, **kwargs)
    assert result["status"] == "completed", result
    return result


def values(computation):
    service, owner, thread, *_ = computation
    return json.loads(service.sessions.find_one({"owner_user_id": owner, "thread_id": thread})["values_json"])


def wait_running(computation):
    service, _, thread, *_ = computation
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        row = service.executions.find_one({"thread_id": thread, "status": "running"})
        if row:
            return row
        time.sleep(0.05)
    raise AssertionError("independent computation did not start")


def assert_processes_gone(proxy, result):
    pids = set(result["terminated_pids"]) | {result["worker_pid"]}
    response = proxy.execute("python -c " + __import__("shlex").quote(
        f"import os; print([p for p in {sorted(pids)!r} if os.path.exists('/proc/'+str(p))])"))
    assert response.exit_code == 0 and response.output.strip() == "[]", response


def test_multiple_steps_persist_json_but_not_python_globals(computation):
    first = run(computation, "secret = 711\nsave_state('quotes', {'A': 2550})\nprint('first')")
    second = run(computation, "print('secret' in globals(), load_state('quotes')['A'])", read_names=["quotes"])
    assert second["stdout"].strip() == "False 2550"
    assert first["worker_pid"] != second["worker_pid"]
    assert second["version"] == 2


def test_budget_change_reuses_verified_quotes_and_unchanged_result(computation):
    run(computation, "save_state('quotes', {'A':2550,'B':6800,'C':1750})\n"
        "save_state('constraints', {'budget':250000})\nsave_state('required',209100)\n"
        "save_state('candidates',{'optional':23})\nsave_state('quote_reads',1)")
    service, owner, thread, manager, _ = computation
    before = service.status(owner, thread)
    result = run(computation, "quotes=load_state('quotes')\nconstraints=load_state('constraints')\n"
        "constraints['budget']=220000\nrequired=load_state('required')\n"
        "save_state('constraints',constraints)\nsave_state('candidates',{'optional':(220000-required)//quotes['C']})",
        read_names=["quotes", "constraints", "required"])
    current = values(computation)
    assert current["candidates"] == {"optional": 6} and current["quote_reads"] == 1
    after = service.status(owner, thread)
    assert after["data"]["quotes"] == before["data"]["quotes"]
    assert after["data"]["required"] == before["data"]["required"]
    operation = service.executions.find_one({"operation_id": result["operation_id"]})
    assert manager.get_or_create(owner).execute("test ! -e " + operation["root"] + "/input-quote_reads.json").exit_code == 0


@pytest.mark.parametrize("suffix", ["raise RuntimeError('deliberate failure')", "save_state('bad',float('nan'))",
                                   "save_state('bad',lambda:1)", "import os; os._exit(0)"])
def test_failed_execution_preserves_every_old_value_and_version(computation, suffix):
    run(computation, "save_state('constraints',{'budget':250000})")
    service, owner, thread, *_ = computation
    result = service.execute(owner, thread, "save_state('constraints',{'budget':220000})\n" + suffix)
    assert result["status"] == "failed", result
    assert values(computation) == {"constraints": {"budget": 250000}}
    assert service.status(owner, thread)["version"] == 1
    run(computation, "print(load_state('constraints')['budget'])", read_names=["constraints"])


def test_only_declared_names_are_loaded_and_no_accidental_save(computation):
    run(computation, "save_state('quotes', {'A':2550})\nsave_state('constraints', {'budget':250000})")
    result = run(computation, "x=load_state('quotes');x['A']=0\n"
        "try: load_state('constraints')\nexcept KeyError: print('explicit reads only')", read_names=["quotes"])
    assert "explicit reads only" in result["stdout"]
    assert values(computation)["quotes"]["A"] == 2550


def test_scope_isolation_same_container_and_other_owner(computation):
    service, owner, thread, manager, _ = computation
    run(computation, "save_state('quotes',{'private':711})")
    other_thread = thread + "-other"
    service.database.threads.insert_one({"owner_user_id": owner, "thread_id": other_thread})
    assert service.execute(owner, other_thread, "print('private' in globals())")["stdout"].strip() == "False"
    with pytest.raises(ComputationError):
        service.execute("computation-b", thread, "pass")
    service.database.threads.insert_one({"owner_user_id": "computation-b", "thread_id": thread + "-b"})
    other = service.execute("computation-b", thread + "-b", "save_state('quotes', {'private':712})")
    assert other["status"] == "completed", other
    assert manager.get_or_create(owner).id != manager.get_or_create("computation-b").id
    assert values(computation)["quotes"]["private"] == 711
    run(computation, "print('quotes' in globals())", session="second")


def test_api_service_process_restart_loads_committed_data_without_replay(computation):
    run(computation, "save_state('quotes',[31,32])")
    _, owner, thread, manager, settings = computation
    script = '''
import json,sys
sys.path[:0]=['src','tests']
from pymongo import MongoClient
from agent.backends.sandbox_manager import SandboxManager,MongoSandboxRegistry,OpenSandboxFactory
from agent.planning.computation import ComputationService
from fixtures.sandbox_service import control_settings
d=json.loads(sys.argv[1]); client=MongoClient(d['uri']);db=client[d['database']]
manager=SandboxManager(factory=OpenSandboxFactory(control_settings()),registry=MongoSandboxRegistry(db),warm_pool_size=0)
service=ComputationService(db,manager.get_or_create)
print(json.dumps(service.execute(d['owner'],d['thread'],"print(load_state('quotes'))",read_names=['quotes'])))
manager.detach();client.close()
'''
    payload = json.dumps({"uri": settings.mongo_uri, "database": settings.database, "owner": owner, "thread": thread})
    child = subprocess.run([sys.executable, "-c", script, payload], capture_output=True, text=True, timeout=60)
    assert child.returncode == 0, child.stderr
    result = json.loads(child.stdout.strip())
    assert result["status"] == "completed" and result["stdout"].strip() == "[31, 32]", result
    assert result["version"] == 2
    assert manager.get_or_create(owner).id


def test_container_replacement_rebuilds_copy_from_mongo(computation):
    run(computation, "save_state('quotes',{'A':2550})")
    _, owner, _, manager, _ = computation
    proxy = manager.get_or_create(owner)
    old_id = proxy.id
    proxy.backend.close()
    manager.recover(owner)
    result = run(computation, "print(load_state('quotes')['A'])", read_names=["quotes"])
    assert proxy.id != old_id and result["stdout"].strip() == "2550"


LONG_CODE = '''
import subprocess,sys,time,os
save_state('constraints',{'budget':0})
child=subprocess.Popen([sys.executable,'-c','import os,time; os.setsid(); time.sleep(300)'])
print(child.pid,flush=True)
time.sleep(300)
'''


def test_timeout_stops_worker_and_detached_child_before_reuse(computation):
    run(computation, "save_state('constraints',{'budget':250000})")
    service, owner, thread, manager, _ = computation
    result = service.execute(owner, thread, LONG_CODE, timeout=3)
    assert result["status"] == "timed_out" and result["processes_stopped"] is True, result
    assert_processes_gone(manager.get_or_create(owner), result)
    assert int(result["stdout"].strip()) in result["terminated_pids"]
    assert values(computation)["constraints"]["budget"] == 250000
    assert run(computation, "print(7)")["stdout"].strip() == "7"


def test_cancel_stops_tree_and_cannot_publish_late(computation):
    run(computation, "save_state('constraints',{'budget':250000})")
    service, owner, thread, manager, _ = computation
    with ThreadPoolExecutor() as pool:
        future = pool.submit(service.execute, owner, thread, LONG_CODE)
        op = wait_running(computation)
        time.sleep(0.5)
        assert service.cancel(owner, thread, op["operation_id"])
        result = future.result(timeout=20)
    assert result["status"] == "cancelled" and result["processes_stopped"], result
    assert_processes_gone(manager.get_or_create(owner), result)
    assert values(computation)["constraints"]["budget"] == 250000
    assert service.status(owner, thread)["version"] == 1
    assert not service.cancel(owner, thread, op["operation_id"])


def test_concurrent_service_and_old_version_cannot_overwrite(computation):
    service, owner, thread, *_ = computation
    run(computation, "save_state('quotes',{'A':2550})")
    independent = ComputationService(service.database, service.backend_provider)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(service.execute, owner, thread, "import time;time.sleep(1);save_state('quotes',{'A':2500})")
        wait_running(computation)
        with pytest.raises(ComputationError, match="already running"):
            independent.execute(owner, thread, "save_state('quotes',{'A':0})")
        assert future.result(timeout=15)["status"] == "completed"
    with pytest.raises(ComputationError, match="stale"):
        independent.execute(owner, thread, "save_state('quotes',{'A':0})", expected_version=1)
    assert values(computation)["quotes"]["A"] == 2500


def test_operation_id_cannot_replay_or_cross_scope_output(computation):
    service, owner, thread, *_ = computation
    result = run(computation, "print('private output')")
    with pytest.raises(ComputationError):
        service.execute(owner, thread, "pass", operation_id=result["operation_id"])
    with pytest.raises(ComputationError):
        service.read_output("other", thread, result["operation_id"])
    with pytest.raises(ComputationError):
        service.cancel("other", thread, result["operation_id"])


def test_output_is_durable_and_paged(computation):
    result = run(computation, "print('x'*100000)")
    service, owner, thread, *_ = computation
    assert len(result["stdout"].encode()) == MAX_PREVIEW_BYTES
    current = ComputationService(service.database, service.backend_provider)
    parts, offset = [], 0
    while offset < 100001:
        page = current.read_output(owner, thread, result["operation_id"], offset=offset)
        parts.append(page["text"])
        offset = page["next_offset"]
    assert "".join(parts) == "x" * 100000 + "\n"


def test_async_tool_cancel_waits_for_actual_process_cleanup(computation):
    service, owner, thread, manager, _ = computation
    run(computation, "save_state('quotes',2550)")
    tool = build_computation_tools(service)[0]
    config = {"configurable": {"owner_user_id": owner, "thread_id": thread}}

    async def exercise():
        pending = asyncio.create_task(tool.ainvoke({"code": LONG_CODE}, config=config))
        operation = await asyncio.to_thread(wait_running, computation)
        await asyncio.sleep(0.5)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        row = service.executions.find_one({"operation_id": operation["operation_id"]})
        assert row["status"] == "cancelled", row
        assert_processes_gone(manager.get_or_create(owner), row["result"])

    asyncio.run(exercise())
    assert values(computation) == {"quotes": 2550}


def test_unconfirmed_cleanup_quarantines_whole_environment(computation):
    service, owner, thread, manager, _ = computation
    run(computation, "save_state('quotes',2550)")
    result = service.execute(owner, thread, "import os,signal;os.kill(os.getppid(),signal.SIGKILL)")
    assert result["status"] == "failed" and result["environment_quarantined"], result
    proxy = manager.get_or_create(owner)
    assert proxy.is_replacing()
    from agent.backends.sandbox_manager import (
        MongoSandboxRegistry,
        OpenSandboxFactory,
        SandboxManager,
    )
    for _ in range(2):
        restarted = SandboxManager(factory=OpenSandboxFactory(sandbox_service.control_settings()),
            registry=MongoSandboxRegistry(service.database), warm_pool_size=0)
        restored = restarted.get_or_create(owner)
        assert restored.id == proxy.id and restored.is_replacing()
        restarted.detach()
    assert service.database.sandbox_registry.find_one({"user_id": owner})["computation_quarantined"]
    assert values(computation) == {"quotes": 2550}
    subsequent = service.execute(owner, thread, "save_state('quotes',0)")
    assert subsequent["error"]["code"] == "ENVIRONMENT_QUARANTINED", subsequent
    manager.recover(owner)
    assert run(computation, "print(load_state('quotes'))", read_names=["quotes"])["stdout"].strip() == "2550"


def test_invalid_staged_fence_after_clean_exit_cannot_commit(computation):
    run(computation, "save_state('quotes',2550)")
    service, owner, thread, *_ = computation
    result = service.execute(owner, thread, "save_state.__globals__['_metadata']['base_version']=999\nsave_state('quotes',0)")
    assert result["status"] == "failed" and result["error"]["code"] == "INVALID_STAGED_STATE", result
    assert result["processes_stopped"] and not result.get("environment_quarantined")
    assert values(computation) == {"quotes": 2550}
    assert service.status(owner, thread)["version"] == 1


def test_cancel_between_validation_and_publication_fences_late_result(computation, monkeypatch):
    run(computation, "save_state('quotes',2550)")
    service, owner, thread, *_ = computation
    original = service._download

    def delayed(proxy, path):
        raw = original(proxy, path)
        if path.endswith('/staged.json'):
            opid = path.split('/')[-2]
            assert service.cancel(owner, thread, opid)
        return raw

    monkeypatch.setattr(service, '_download', delayed)
    result = service.execute(owner, thread, "save_state('quotes',0)")
    assert result["status"] == "stale", result
    assert values(computation) == {"quotes": 2550}
    assert service.status(owner, thread)["version"] == 1


def test_generation_change_after_remote_success_rejects_commit(computation, monkeypatch):
    run(computation, "save_state('quotes',2550)")
    service, owner, thread, manager, _ = computation
    original = service._download

    def replaced(proxy, path):
        raw = original(proxy, path)
        if path.endswith('/staged.json'):
            proxy.backend.close()
            manager.recover(owner)
        return raw

    monkeypatch.setattr(service, '_download', replaced)
    result = service.execute(owner, thread, "save_state('quotes',0)")
    assert result["status"] == "failed" and result["processes_stopped"], result
    assert values(computation) == {"quotes": 2550}


def test_timeout_in_owner_queue_never_starts_actor_code(computation):
    service, owner, thread, manager, _ = computation
    proxy = manager.get_or_create(owner)
    with ThreadPoolExecutor() as pool:
        locked = pool.submit(proxy.execute, "sleep 3")
        time.sleep(0.3)
        result = service.execute(owner, thread, "save_state('quotes',0)", timeout=1)
        assert locked.result(timeout=10).exit_code == 0
    assert result["status"] == "timed_out", result
    row = service.executions.find_one({"operation_id": result["operation_id"]})
    assert not row.get("execution_id")
    assert values(computation) == {}
    assert not proxy.is_replacing()


def test_double_fork_orphan_and_new_session_are_reaped(computation):
    service, owner, thread, manager, _ = computation
    code = '''
import os,time
if os.fork()==0:
    if os.fork()==0:
        os.setsid();print(os.getpid(),flush=True);time.sleep(300)
    os._exit(0)
time.sleep(300)
'''
    result = service.execute(owner, thread, code, timeout=2)
    assert result["status"] == "timed_out" and result["processes_stopped"], result
    orphan = int(result["stdout"].strip())
    assert orphan in result["terminated_pids"]
    assert_processes_gone(manager.get_or_create(owner), result)


def test_crashed_api_operation_is_explicitly_recovered_without_replay(computation):
    run(computation, "save_state('quotes',2550)")
    service, owner, thread, manager, settings = computation
    script = '''
import json,sys
sys.path[:0]=['src','tests']
from pymongo import MongoClient
from agent.backends.sandbox_manager import SandboxManager,MongoSandboxRegistry,OpenSandboxFactory
from agent.planning.computation import ComputationService
from fixtures.sandbox_service import control_settings
d=json.loads(sys.argv[1]);client=MongoClient(d['uri']);db=client[d['database']]
manager=SandboxManager(factory=OpenSandboxFactory(control_settings()),registry=MongoSandboxRegistry(db),warm_pool_size=0)
ComputationService(db,manager.get_or_create).execute(d['owner'],d['thread'],d['code'])
'''
    data = {"uri": settings.mongo_uri, "database": settings.database, "owner": owner, "thread": thread, "code": LONG_CODE}
    process = subprocess.Popen([sys.executable, "-c", script, json.dumps(data)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        op = wait_running(computation)
        time.sleep(0.5)
        process.terminate()
        process.communicate(timeout=10)
        independent = ComputationService(service.database, manager.get_or_create)
        result = independent.recover(owner, thread)
        assert result["status"] == "cancelled" and result["processes_stopped"], result
        assert_processes_gone(manager.get_or_create(owner), result)
        assert values(computation) == {"quotes": 2550}
        assert independent.status(owner, thread)["version"] == 1
        assert run(computation, "print(load_state('quotes'))", read_names=["quotes"])["stdout"].strip() == "2550"
        assert service.executions.count_documents({"operation_id": op["operation_id"]}) == 1
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)


def test_lost_mongo_publish_acknowledgement_reconciles_actual_commit(computation, monkeypatch):
    from pymongo.errors import AutoReconnect
    service, owner, thread, *_ = computation
    original = service.sessions.find_one_and_update
    injected = False

    def lost_ack(selector, update, **kwargs):
        nonlocal injected
        result = original(selector, update, **kwargs)
        if "values_json" in update.get("$set", {}) and not injected:
            injected = True
            raise AutoReconnect("injected lost publish acknowledgement after actual Mongo CAS")
        return result

    monkeypatch.setattr(service.sessions, "find_one_and_update", lost_ack)
    result = run(computation, "save_state('quotes',2550)")
    assert injected and result["publication_reconciled"] and result["version"] == 1
    assert values(computation) == {"quotes": 2550}
    assert service.status(owner, thread)["active"] is None
