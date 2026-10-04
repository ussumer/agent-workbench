"""Integration tests for the sandbox lifecycle: registry, reconnection, teardown.

The acceptance suite proves the user-visible guarantees; this file walks the
lifecycle transitions and the bookkeeping that keeps containers from piling up. One
manager is shared so the suite does not pay for a dozen container creations.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures import mongo_service, sandbox_service  # noqa: E402

from agent.backends.sandbox_manager import (  # noqa: E402
    MongoSandboxRegistry,
    OpenSandboxFactory,
    SandboxManager,
    SandboxStatus,
)
from agent.backends.sandbox_setup import SCRATCH_ROOT  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def settings():
    return mongo_service.unique_settings("t09life")


@pytest.fixture(scope="module")
def database(settings):
    from pymongo import MongoClient

    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000, tz_aware=True)
    yield client[settings.database]
    client.close()


@pytest.fixture(scope="module")
def manager(settings):
    with sandbox_service.running_pool(settings, warm_pool_size=1) as (running, _):
        yield running


def test_the_warm_pool_reports_its_target_size(manager):
    """The pool is filled at start and does not overshoot its target."""
    assert manager.warm_count() == 1

    inflated = manager.replenish_warm_pool()

    assert inflated == 0, "池已满时不应再创建容器"
    assert manager.warm_count() == 1


def test_claiming_moves_a_sandbox_from_warm_to_claimed(manager, database):
    warm_ids = manager.warm_sandbox_ids()
    proxy = manager.get_or_create("life-claim")

    assert proxy.id in warm_ids

    row = MongoSandboxRegistry(database).get("life-claim")
    assert row is not None
    assert row.status == SandboxStatus.CLAIMED.value
    assert row.generation == 1


def test_recovery_keeps_exactly_one_registry_row(manager, database):
    proxy = manager.get_or_create("life-recover")
    old_id = proxy.id

    container = sandbox_service.container_id_for(old_id)
    import subprocess

    subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=120, check=False)

    manager.recover("life-recover")

    registry = MongoSandboxRegistry(database)
    assert registry.get("life-recover").sandbox_id == proxy.id
    assert registry.get("life-recover").generation == 2
    assert registry.get("life-recover").sandbox_id != old_id
    assert len([row for row in registry.list_active() if row.user_id == "life-recover"]) == 1


def test_a_registered_sandbox_survives_detach_and_is_reconnected(settings, database):
    """Dropping in-memory state must not orphan containers."""
    registry = MongoSandboxRegistry(database)
    sandbox_id = ""

    with sandbox_service.running_pool(settings, warm_pool_size=0) as (first, first_factory):
        proxy = first.get_or_create("life-restart")
        sandbox_id = proxy.id
        first.detach()

        assert registry.get("life-restart").sandbox_id == sandbox_id

        # A second process would do exactly this: read the registry and reattach.
        second = SandboxManager(
            factory=OpenSandboxFactory(
                sandbox_service.control_settings(), metadata={"purpose": "t09-lifecycle"}
            ),
            registry=registry,
            warm_pool_size=0,
        )
        try:
            adopted = second.get_or_create("life-restart")
            assert adopted.id == sandbox_id, "重启后应重连而不是新建"
        finally:
            second.shutdown()

    assert sandbox_service.container_id_for(sandbox_id) is None, "重连的容器最终也要销毁"
    assert registry.get("life-restart") is None
    assert first_factory.leaked_containers() == []


def test_recycle_removes_the_registration_and_the_container(manager, database):
    proxy = manager.get_or_create("life-recycle")
    sandbox_id = proxy.id

    assert manager.recycle("life-recycle") is True

    assert MongoSandboxRegistry(database).get("life-recycle") is None
    assert sandbox_service.container_id_for(sandbox_id) is None
    assert manager.get("life-recycle") is None


def test_registered_sandboxes_are_not_treated_as_orphans(manager):
    proxy = manager.get_or_create("life-protected")

    removed = manager.cleanup_orphans(keep_warm=True)

    assert proxy.id not in removed
    assert manager.check_health("life-protected") is True


def test_a_user_sandbox_holds_real_workspace_state(manager):
    proxy = manager.get_or_create("life-workspace")

    path = f"{SCRATCH_ROOT}/lifecycle.txt"
    assert proxy.write(path, "persisted-in-container\n").error is None
    assert "persisted-in-container" in proxy.read(path).file_data["content"]

    # A fresh generation starts clean apart from the managed rule files.
    assert proxy.read("/workspace/rules/README.md").error is None


def test_created_and_destroyed_bookkeeping_balances(settings):
    """Every container the pool creates is destroyed by shutdown or by recycle."""
    with sandbox_service.running_pool(settings, warm_pool_size=1) as (running, factory):
        running.get_or_create("life-bookkeeping-a")
        running.get_or_create("life-bookkeeping-b")
        created = len(factory.created_ids)

        stats_before = running.stats()
        assert stats_before.created == created

    assert factory.leaked_containers() == [], f"{created} 个容器创建后必须全部销毁"
