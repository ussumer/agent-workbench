"""Sandbox calls, rather than direct breaker updates, control owner circuit state."""

import asyncio
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures import mongo_service, sandbox_service  # noqa: E402


@pytest.fixture(scope="module")
def manager():
    settings = mongo_service.unique_settings("t33-" + uuid.uuid4().hex[:8])
    try:
        with sandbox_service.running_pool(settings, warm_pool_size=0) as (manager, _):
            yield manager
    finally:
        mongo_service.drop_test_database(settings)


@pytest.fixture()
def circuit(manager):
    ticks = [0.0]
    manager.breakers.clock = lambda: ticks[0]
    owner = "t33-" + uuid.uuid4().hex[:10]
    proxy = manager.get_or_create(owner)
    assert proxy.breaker is manager.breakers.for_user(owner)
    yield proxy, ticks
    manager.recycle(owner)


def fail_three(proxy):
    for _ in range(3):
        assert proxy.execute("python3 -c 'import time; time.sleep(3)'", timeout=1).exit_code == 124
    assert proxy.breaker.snapshot().state == "open"


def test_real_timeouts_open_the_circuit_and_block_side_effects(circuit):
    proxy, _ = circuit
    fail_three(proxy)
    with pytest.raises(RuntimeError, match="SANDBOX_CIRCUIT_OPEN"):
        proxy.execute("touch /workspace/scratch/should-not-exist")
    result = proxy.backend.download_files(["/workspace/scratch/should-not-exist"])[0]
    assert result.error == "file_not_found"
    assert proxy.breaker.snapshot().sandbox_failures == 3


def test_script_failures_and_missing_files_are_not_infrastructure_failures(circuit):
    proxy, _ = circuit
    for _ in range(5):
        assert proxy.execute("exit 42").exit_code == 42
        assert proxy.download_files(["/workspace/scratch/missing"])[0].error == "file_not_found"
    assert proxy.breaker.snapshot().sandbox_failures == 0
    assert proxy.breaker.state == "closed"


def test_async_and_sync_calls_share_the_same_failure_counter(circuit):
    proxy, _ = circuit
    assert proxy.execute("sleep 3", timeout=1).exit_code == 124
    assert asyncio.run(proxy.aexecute("sleep 3", timeout=1)).exit_code == 124
    assert proxy.execute("sleep 3", timeout=1).exit_code == 124
    with pytest.raises(RuntimeError, match="SANDBOX_CIRCUIT_OPEN"):
        asyncio.run(proxy.aread("/workspace/scratch/missing"))
    assert proxy.breaker.snapshot().sandbox_failures == 3


def test_another_owners_container_remains_usable(manager, circuit):
    proxy, _ = circuit
    fail_three(proxy)
    owner = "t33-other-" + uuid.uuid4().hex[:8]
    other = manager.get_or_create(owner)
    try:
        assert other.execute("true").exit_code == 0
        assert other.breaker is not proxy.breaker
        assert other.breaker.snapshot().sandbox_failures == 0
    finally:
        manager.recycle(owner)


def test_cooldown_refusal_does_not_rebuild_and_health_probe_recovers(manager, circuit):
    proxy, ticks = circuit
    original_id, generation = proxy.id, proxy.generation
    fail_three(proxy)
    with pytest.raises(RuntimeError, match="SANDBOX_CIRCUIT_OPEN"):
        manager.ensure_healthy(proxy.owner_user_id)
    assert (proxy.id, proxy.generation) == (original_id, generation)
    ticks[0] = 31.0
    assert manager.check_health(proxy.owner_user_id)
    assert proxy.breaker.state == "closed"
    assert (proxy.id, proxy.generation) == (original_id, generation)


def test_only_one_parallel_half_open_probe_reaches_the_real_backend(circuit, monkeypatch):
    proxy, ticks = circuit
    path = "/workspace/scratch/probe.txt"
    assert proxy.upload_files([(path, b"probe")])[0].error is None
    fail_three(proxy)
    ticks[0] = 31.0
    entered, release = threading.Event(), threading.Event()
    original = proxy.backend.download_files

    def held_probe(paths):
        entered.set()
        assert release.wait(10)
        return original(paths)

    monkeypatch.setattr(proxy.backend, "download_files", held_probe)
    with ThreadPoolExecutor(max_workers=8) as executor:
        first = executor.submit(proxy.download_files, [path])
        assert entered.wait(10)
        try:
            others = [executor.submit(proxy.download_files, [path]) for _ in range(7)]
            for future in others:
                with pytest.raises(RuntimeError, match="SANDBOX_CIRCUIT_OPEN"):
                    future.result(timeout=5)
        finally:
            release.set()
        assert first.result(timeout=10)[0].content == b"probe"
    assert proxy.breaker.state == "closed"


def test_file_transport_failures_are_not_hidden_by_protocol_error_mapping(circuit, monkeypatch):
    proxy, _ = circuit

    def unavailable(*args, **kwargs):
        raise httpx.ConnectError("controlled file transport outage")

    monkeypatch.setattr(proxy.backend._sandbox.files, "write_files", unavailable)
    for _ in range(3):
        assert proxy.upload_files([("/workspace/scratch/out.txt", b"data")])[0].error is not None
    assert proxy.breaker.snapshot().state == "open"
    with pytest.raises(RuntimeError, match="SANDBOX_CIRCUIT_OPEN"):
        proxy.download_files(["/workspace/scratch/out.txt"])


def test_success_from_before_the_outage_cannot_close_a_newly_opened_circuit(circuit, monkeypatch):
    proxy, _ = circuit
    path = "/workspace/scratch/old-call.txt"
    assert proxy.upload_files([(path, b"old call")])[0].error is None
    entered, release = threading.Event(), threading.Event()
    original = proxy.backend.download_files

    def delayed_download(paths):
        entered.set()
        assert release.wait(30)
        return original(paths)

    monkeypatch.setattr(proxy.backend, "download_files", delayed_download)
    with ThreadPoolExecutor(max_workers=1) as executor:
        old_call = executor.submit(proxy.download_files, [path])
        assert entered.wait(10)
        try:
            fail_three(proxy)
        finally:
            release.set()
        assert old_call.result(timeout=10)[0].content == b"old call"
    assert proxy.breaker.state == "open"


def test_cleanup_cannot_delete_another_databases_live_warm_pool(manager):
    foreign_settings = mongo_service.unique_settings("t33-foreign-" + uuid.uuid4().hex[:8])
    try:
        with sandbox_service.running_pool(foreign_settings, warm_pool_size=1) as (foreign, factory):
            warm_ids = set(foreign.warm_sandbox_ids())
            assert warm_ids
            removed = set(manager.cleanup_orphans(keep_warm=False))
            assert not warm_ids & removed
            for sandbox_id in warm_ids:
                handle = factory.reconnect(sandbox_id)
                assert handle is not None
                assert handle.backend.execute("true").exit_code == 0
    finally:
        mongo_service.drop_test_database(foreign_settings)
