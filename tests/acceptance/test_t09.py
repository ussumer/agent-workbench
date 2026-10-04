"""T09 acceptance: user sandbox pool, stable proxy and recovery.

Real containers, a real Mongo registry and real concurrency. The suite proves the
properties the contract depends on:

* one proxy per user, kept across threads, whose Python identity survives a container
  being replaced;
* different users get different containers and cannot read each other's files;
* a claim from the warm pool is exclusive, and a failed initialisation is never
  handed to a run;
* recovery re-uploads the workspace, bumps the generation, and happens exactly once
  even when several threads notice the failure together;
* a call that raced a replacement fails explicitly instead of being replayed.

No host-shell fallback exists anywhere in this file: execution happens in containers
or the test fails.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures import mongo_service, sandbox_service  # noqa: E402

from agent.backends.custom_opensandbox import OpenSandboxBackend  # noqa: E402
from agent.backends.sandbox_manager import (  # noqa: E402
    ALLOWED_TRANSITIONS,
    IllegalTransition,
    MongoSandboxRegistry,
    OpenSandboxFactory,
    SandboxManager,
    SandboxStatus,
    assert_transition,
)
from agent.backends.sandbox_proxy import (  # noqa: E402
    SandboxBackendProxy,
    SandboxReplacedError,
)
from agent.backends.sandbox_setup import (  # noqa: E402
    RULE_FILES,
    SCRATCH_ROOT,
    WORKSPACE_ROOT,
    SandboxRuntimeConfig,
)
from agent.middlewares.sandbox_health import (  # noqa: E402
    STATE_SANDBOX_GENERATION,
    STATE_SANDBOX_RECOVERED,
    SandboxHealthMiddleware,
)

pytestmark = pytest.mark.integration

RULE_README = f"{WORKSPACE_ROOT}/rules/README.md"

#: Every protocol method the proxy must delegate explicitly.
PROXY_METHODS: tuple[str, ...] = (
    "ls",
    "read",
    "write",
    "edit",
    "glob",
    "grep",
    "delete",
    "upload_files",
    "download_files",
    "execute",
    "als",
    "aread",
    "awrite",
    "aedit",
    "aglob",
    "agrep",
    "adelete",
    "aupload_files",
    "adownload_files",
    "aexecute",
)


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def settings():
    return mongo_service.unique_settings("t09")


@pytest.fixture
def pool(settings):
    """A fresh manager per test, always torn down."""
    with sandbox_service.running_pool(settings, warm_pool_size=1) as (manager, factory):
        yield manager, factory


def kill_sandbox(sandbox_id: str) -> str:
    """Destroy a container out from under the manager, simulating a crash."""
    container = sandbox_service.container_id_for(sandbox_id)
    assert container, f"no container for sandbox {sandbox_id}"
    subprocess.run(
        ["docker", "rm", "-f", container],
        capture_output=True,
        timeout=120,
        check=False,
    )
    return container


# --------------------------------------------------------------------------- #
# warm pool and claiming
# --------------------------------------------------------------------------- #


def test_the_warm_pool_is_ready_before_the_first_request(pool):
    """A first message must not pay for container creation."""
    manager, factory = pool

    assert manager.warm_count() == 1
    assert len(factory.created_ids) == 1
    warm_id = manager.warm_sandbox_ids()[0]
    assert sandbox_service.container_id_for(warm_id) is not None


def test_a_new_user_claims_the_warm_sandbox(pool):
    manager, factory = pool
    warm_ids = manager.warm_sandbox_ids()

    proxy = manager.get_or_create("user-claimant")

    assert proxy.id in warm_ids, "首个用户应认领预热沙箱，而不是新建"
    assert manager.warm_count() <= 1


def test_the_warm_pool_is_replenished_after_a_claim(pool):
    manager, factory = pool
    before = len(factory.created_ids)

    manager.get_or_create("user-replenish")

    deadline = time.monotonic() + 120
    while manager.warm_count() < 1 and time.monotonic() < deadline:
        time.sleep(0.5)
    assert manager.warm_count() == 1, "认领后必须补充预热"
    assert len(factory.created_ids) > before


def test_concurrent_claims_for_different_users_do_not_share_a_container(settings):
    """Two users claiming at the same time must never end up in one container."""
    with sandbox_service.running_pool(settings, warm_pool_size=1) as (manager, _):
        results: dict[str, str] = {}
        errors: list[BaseException] = []
        barrier = threading.Barrier(2)

        def claim(user_id: str) -> None:
            try:
                barrier.wait(timeout=60)
                results[user_id] = manager.get_or_create(user_id).id
            except BaseException as failure:  # noqa: BLE001 - surfaced after the join
                errors.append(failure)

        threads = [
            threading.Thread(target=claim, args=(f"user-conc-{index}",)) for index in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=600)

        assert not errors, errors
        assert len(set(results.values())) == 2, f"并发认领拿到了同一个沙箱: {results}"


def test_the_same_user_always_gets_the_same_proxy(pool):
    manager, _ = pool

    first = manager.get_or_create("user-stable")
    second = manager.get_or_create("user-stable")

    assert first is second, "同一用户必须复用同一个 proxy 对象"
    assert first.id == second.id


# --------------------------------------------------------------------------- #
# isolation
# --------------------------------------------------------------------------- #


def test_two_users_get_different_containers(pool):
    manager, _ = pool

    alice = manager.get_or_create("user-iso-alice")
    bob = manager.get_or_create("user-iso-bob")

    assert alice.id != bob.id
    assert alice.owner_user_id == "user-iso-alice"
    assert bob.owner_user_id == "user-iso-bob"
    assert sandbox_service.container_id_for(alice.id) != sandbox_service.container_id_for(bob.id)


def test_one_user_cannot_read_another_users_files(pool):
    manager, _ = pool
    alice = manager.get_or_create("user-read-alice")
    bob = manager.get_or_create("user-read-bob")
    path = sandbox_service.seed_user_file(alice, "user-read-alice")

    assert alice.download_files([path])[0].content == b"private-data-for-user-read-alice"

    stolen = bob.download_files([path])[0]
    assert stolen.content is None
    assert stolen.error == "file_not_found"
    assert "private-data" not in str(stolen.content)

    listing = bob.execute(f"ls -R {SCRATCH_ROOT} 2>&1; echo done")
    assert "private-data" not in listing.output


def test_two_users_are_registered_independently(settings):
    """The registry keeps one row per user, each naming its own sandbox."""
    with sandbox_service.running_pool(settings, warm_pool_size=1) as (manager, _):
        alice = manager.get_or_create("user-reg-alice")
        bob = manager.get_or_create("user-reg-bob")

        registry = MongoSandboxRegistry(_database(settings))
        alice_row = registry.get("user-reg-alice")
        bob_row = registry.get("user-reg-bob")

        assert alice_row is not None and bob_row is not None
        assert alice_row.sandbox_id == alice.id
        assert bob_row.sandbox_id == bob.id
        assert alice_row.sandbox_id != bob_row.sandbox_id
        assert alice_row.status == SandboxStatus.CLAIMED.value
        assert alice_row.image_digest.startswith("opensandbox/code-interpreter")


def _database(settings):
    from pymongo import MongoClient

    return MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000, tz_aware=True)[
        settings.database
    ]


# --------------------------------------------------------------------------- #
# proxy behaviour
# --------------------------------------------------------------------------- #


def test_the_proxy_delegates_the_whole_protocol_explicitly(pool):
    """No ``__getattr__``: every method is declared, so gaps fail loudly."""
    manager, _ = pool
    proxy = manager.get_or_create("user-proxy")

    assert "__getattr__" not in SandboxBackendProxy.__dict__
    missing = [name for name in PROXY_METHODS if not callable(getattr(proxy, name, None))]
    assert not missing, f"proxy 缺少协议方法: {missing}"

    target = f"{SCRATCH_ROOT}/proxy-probe.txt"
    assert proxy.write(target, "alpha\nbeta\n").error is None
    assert proxy.read(target).error is None
    assert proxy.ls(SCRATCH_ROOT).error is None
    assert proxy.glob("proxy-probe.txt", SCRATCH_ROOT).error is None
    assert proxy.grep("beta", SCRATCH_ROOT).error is None
    assert proxy.edit(target, "beta", "BETA").error is None
    assert proxy.upload_files([(f"{SCRATCH_ROOT}/probe.bin", b"\x00\x01")])[0].error is None
    assert proxy.download_files([f"{SCRATCH_ROOT}/probe.bin"])[0].content == b"\x00\x01"
    assert proxy.delete(f"{SCRATCH_ROOT}/probe.bin").error is None
    assert proxy.execute("echo delegated").exit_code == 0


def test_the_proxy_delegates_the_async_protocol_too(pool):
    import asyncio

    manager, _ = pool
    proxy = manager.get_or_create("user-proxy-async")
    target = f"{SCRATCH_ROOT}/proxy-async.txt"

    async def exercise() -> dict:
        return {
            "aexecute": await proxy.aexecute("echo async-delegated"),
            "awrite": await proxy.awrite(target, "one\ntwo\n"),
            "aread": await proxy.aread(target),
            "aedit": await proxy.aedit(target, "two", "TWO"),
            "als": await proxy.als(SCRATCH_ROOT),
            "aglob": await proxy.aglob("proxy-async.txt", SCRATCH_ROOT),
            "agrep": await proxy.agrep("TWO", SCRATCH_ROOT),
            "aupload": await proxy.aupload_files([(f"{SCRATCH_ROOT}/p.bin", b"x")]),
            "adownload": await proxy.adownload_files([f"{SCRATCH_ROOT}/p.bin"]),
            "adelete": await proxy.adelete(f"{SCRATCH_ROOT}/p.bin"),
        }

    results = asyncio.run(exercise())

    assert results["aexecute"].exit_code == 0
    assert "async-delegated" in results["aexecute"].output
    assert results["awrite"].error is None
    assert results["aread"].error is None
    assert results["aedit"].occurrences == 1
    assert results["als"].error is None
    assert results["aglob"].error is None
    assert results["agrep"].error is None
    assert results["aupload"][0].error is None
    assert results["adownload"][0].content == b"x"
    assert results["adelete"].error is None


def test_replacing_the_backend_preserves_proxy_identity(pool):
    """The handle every caller holds must survive a container replacement."""
    manager, _ = pool
    proxy = manager.get_or_create("user-identity")
    original_backend = proxy.backend

    kill_sandbox(proxy.id)
    recovered = manager.recover("user-identity")

    assert recovered is proxy, "恢复不得替换 proxy 对象"
    assert proxy.backend is not original_backend
    assert isinstance(proxy.backend, OpenSandboxBackend)
    assert proxy.replacement_count == 1


def test_generation_changes_are_observable(pool):
    manager, _ = pool
    proxy = manager.get_or_create("user-generation")
    start = proxy.generation

    kill_sandbox(proxy.id)
    manager.recover("user-generation")

    assert proxy.generation == start + 1
    assert manager.stats().proxies["user-generation"] == start + 1


def test_calls_racing_a_replacement_fail_instead_of_replaying(pool):
    """A command that may have had side effects must never be re-run automatically."""
    manager, _ = pool
    proxy = manager.get_or_create("user-race")
    victim = f"{SCRATCH_ROOT}/must-not-exist.txt"

    proxy.begin_replacement()
    with pytest.raises(SandboxReplacedError) as caught:
        proxy.execute(f"touch {victim}")

    assert "side effects" in str(caught.value) or "being replaced" in str(caught.value)

    # Put the same backend back and confirm the command really did not run.
    proxy.replace_backend(proxy.backend)
    absence = proxy.execute(f"test -e {victim} && echo present || echo absent")
    assert "absent" in absence.output, "被拒绝的调用不应留下任何副作用"


# --------------------------------------------------------------------------- #
# serialisation
# --------------------------------------------------------------------------- #


def test_shell_commands_for_one_user_are_serialised(pool):
    """Two concurrent commands must not interleave inside one container.

    The command claims a directory for the duration of its work; if two ran at once,
    the loser would fail with the reserved exit code.
    """
    manager, _ = pool
    proxy = manager.get_or_create("user-serial")
    proxy.execute("rm -rf /tmp/serialise.lock; echo ready").exit_code

    script = "mkdir /tmp/serialise.lock 2>/dev/null || exit 9; sleep 1; rmdir /tmp/serialise.lock"
    codes: list[int | None] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def run() -> None:
        barrier.wait(timeout=60)
        response = proxy.execute(script, timeout=60)
        with lock:
            codes.append(response.exit_code)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=300)

    assert codes == [0, 0], f"同一用户的 shell 命令发生了交错: {codes}"


def test_per_run_workspace_paths_follow_the_contract(pool):
    manager, _ = pool
    path = manager.run_workspace("thread-42", "run-7")

    assert path == "/workspace/thread-42/run-7"

    proxy = manager.get_or_create("user-runs")
    created = proxy.execute(f"mkdir -p {path} && test -d {path} && echo ok")
    assert created.exit_code == 0
    assert "ok" in created.output


# --------------------------------------------------------------------------- #
# health and recovery
# --------------------------------------------------------------------------- #


def test_the_health_check_notices_a_killed_container(pool):
    manager, _ = pool
    proxy = manager.get_or_create("user-health")

    assert manager.check_health("user-health") is True

    kill_sandbox(proxy.id)

    assert manager.check_health("user-health") is False


def test_recovery_restores_the_workspace_rules(pool):
    manager, _ = pool
    proxy = manager.get_or_create("user-recover")
    original = proxy.id

    proxy.write(RULE_README, "TAMPERED")
    assert "TAMPERED" in proxy.read(RULE_README).file_data["content"]

    kill_sandbox(proxy.id)
    manager.recover("user-recover")

    assert proxy.id != original
    restored = proxy.read(RULE_README).file_data["content"]
    assert restored.strip() == RULE_FILES[RULE_README].strip(), "恢复后必须重新上传规则文件"
    scratch = proxy.execute(f"test -d {SCRATCH_ROOT} && echo present")
    assert "present" in scratch.output


def test_concurrent_health_failures_rebuild_only_once(pool):
    """Several threads noticing the same failure must not each build a container."""
    manager, factory = pool
    proxy = manager.get_or_create("user-single-rebuild")
    kill_sandbox(proxy.id)
    created_before = len(factory.created_ids)

    errors: list[BaseException] = []
    barrier = threading.Barrier(3)

    def recover() -> None:
        try:
            barrier.wait(timeout=60)
            manager.ensure_healthy("user-single-rebuild")
        except BaseException as failure:  # noqa: BLE001
            errors.append(failure)

    threads = [threading.Thread(target=recover) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=600)

    assert not errors, errors
    new_containers = len(factory.created_ids) - created_before
    assert new_containers == 1, f"并发健康失败只应重建一次，实际新建 {new_containers} 个容器"
    assert manager.stats().recoveries == 1
    assert manager.check_health("user-single-rebuild") is True


def test_a_failed_initialisation_is_never_published_to_the_run(settings):
    """When the replacement cannot be built, the broken sandbox stays broken.

    The proxy must keep pointing at the old generation rather than exposing a
    half-initialised container. The failure is provoked with real infrastructure: an
    image that does not exist, so creation itself fails.
    """
    from pymongo import MongoClient

    with sandbox_service.running_pool(settings, warm_pool_size=0) as (manager, _):
        proxy = manager.get_or_create("user-broken")
        good_id = proxy.id
        kill_sandbox(good_id)

        config = sandbox_service.control_settings()
        impossible = SandboxRuntimeConfig(
            domain=config.domain,
            protocol=config.protocol,
            image="opensandbox/does-not-exist:v0",
            timeout_seconds=config.timeout_seconds,
            ready_timeout_seconds=60,
        )
        manager._factory = OpenSandboxFactory(impossible)  # noqa: SLF001 - deliberate fault injection

        with pytest.raises(Exception):  # noqa: BLE001 - any failure mode is acceptable here
            manager.recover("user-broken")

        assert proxy.id == good_id, "不得把半初始化的容器发布给运行"
        with MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000, tz_aware=True) as client:
            row = MongoSandboxRegistry(client[settings.database]).get("user-broken")
        assert row is not None
        assert row.status == SandboxStatus.FAILED.value


def test_the_health_middleware_recovers_before_the_run_starts(pool):
    """`before_agent` probes, recovers, and reports the generation it settled on."""
    manager, _ = pool
    proxy = manager.get_or_create("user-middleware")
    kill_sandbox(proxy.id)

    middleware = SandboxHealthMiddleware(manager, owner_resolver=lambda runtime: "user-middleware")
    result = middleware.before_agent({}, object())

    assert result is not None
    assert result[STATE_SANDBOX_RECOVERED] is True
    assert result[STATE_SANDBOX_GENERATION] == proxy.generation
    assert manager.check_health("user-middleware") is True


def test_the_health_middleware_skips_runs_without_an_owner(pool):
    """No owner means no sandbox is touched, not a guess at one."""
    manager, factory = pool
    middleware = SandboxHealthMiddleware(manager, owner_resolver=lambda runtime: None)
    before = len(factory.created_ids)

    assert middleware.before_agent({}, object()) is None
    assert len(factory.created_ids) == before


# --------------------------------------------------------------------------- #
# restart, reconnection and cleanup
# --------------------------------------------------------------------------- #


def test_a_restart_reconnects_through_the_registry_instead_of_leaking(settings):
    """A new manager adopts the registered sandbox rather than creating another.

    A restart also leaves the previous process's warm container running, because warm
    sandboxes have no registry row by design. The restarted manager reclaims it by
    project marker, which is the only discovery mechanism left once memory is gone.
    """
    with sandbox_service.running_pool(settings, warm_pool_size=1) as (first, first_factory):
        proxy = first.get_or_create("user-restart")
        sandbox_id = proxy.id
        warm_orphans = first.warm_sandbox_ids()
        assert warm_orphans, "认领后应补充了一个预热沙箱"
        first.detach()  # process forgets its state; containers keep running

        with sandbox_service.running_pool(settings, warm_pool_size=0) as (second, second_factory):
            adopted = second.get_or_create("user-restart")

            assert adopted.id == sandbox_id, "重启后应重连登记中的沙箱"
            assert sandbox_id in second_factory.reconnect_attempts
            assert sandbox_id not in second_factory.created_ids
            assert adopted.owner_user_id == "user-restart"

            reclaimed = second.cleanup_orphans(keep_warm=False)
            assert set(warm_orphans) <= set(reclaimed), "重启后应回收上个进程遗留的预热容器"
            for orphan in warm_orphans:
                assert sandbox_service.container_id_for(orphan) is None

    assert sandbox_service.container_id_for(sandbox_id) is None


def test_a_registered_sandbox_that_vanished_is_rebuilt(settings):
    """A stale registration must lead to a fresh container, not an error loop."""
    from pymongo import MongoClient

    with sandbox_service.running_pool(settings, warm_pool_size=0) as (first, _):
        proxy = first.get_or_create("user-vanished")
        stale_id = proxy.id
        kill_sandbox(stale_id)
        first.detach()

        with MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000, tz_aware=True) as c:
            registry = MongoSandboxRegistry(c[settings.database])
            assert registry.get("user-vanished") is not None

            manager = SandboxManager(
                factory=OpenSandboxFactory(
                    sandbox_service.control_settings(), metadata={"purpose": "t09-vanished"}
                ),
                registry=registry,
                warm_pool_size=0,
            )
            try:
                rebuilt = manager.get_or_create("user-vanished")
                assert rebuilt.id != stale_id
                assert registry.get("user-vanished").sandbox_id == rebuilt.id
            finally:
                manager.shutdown()


def test_the_registry_records_the_pinned_image_digest(settings):
    """A tag can be re-pointed; the recorded digest pins what actually ran."""
    from pymongo import MongoClient

    with sandbox_service.running_pool(settings, warm_pool_size=0) as (manager, _):
        manager.get_or_create("user-digest")

        with MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000, tz_aware=True) as c:
            row = MongoSandboxRegistry(c[settings.database]).get("user-digest")

    assert row is not None
    assert row.image_digest.startswith("opensandbox/code-interpreter")
    assert "sha256:" in row.image_digest


def test_recycling_destroys_only_that_users_sandbox(pool):
    manager, _ = pool
    alice = manager.get_or_create("user-recycle-alice")
    bob = manager.get_or_create("user-recycle-bob")
    alice_id, bob_id = alice.id, bob.id

    assert manager.recycle("user-recycle-alice") is True

    assert sandbox_service.container_id_for(alice_id) is None
    assert sandbox_service.container_id_for(bob_id) is not None
    assert manager.get("user-recycle-alice") is None
    assert manager.get_or_create("user-recycle-bob") is bob


def test_shutdown_destroys_every_container_it_created(settings):
    with sandbox_service.running_pool(settings, warm_pool_size=1) as (manager, factory):
        alice = manager.get_or_create("user-shutdown-alice")
        bob = manager.get_or_create("user-shutdown-bob")
        created = list(factory.created_ids)
        assert len(created) >= 3

    assert factory.leaked_containers() == [], "关停后不应残留任何本项目容器"
    for sandbox_id in (alice.id, bob.id):
        assert sandbox_service.container_id_for(sandbox_id) is None


def test_cleanup_leaves_containers_it_did_not_create_alone(settings):
    """Cleanup is scoped to this project; other containers on the host survive."""
    outsider = "rush-harness-t09-outsider"
    subprocess.run(
        ["docker", "rm", "-f", outsider],
        capture_output=True,
        timeout=120,
        check=False,
    )
    started = subprocess.run(
        ["docker", "run", "-d", "--name", outsider, "alpine:3.20", "sleep", "300"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    if started.returncode != 0:
        pytest.fail(f"could not start the unrelated container: {started.stderr.strip()}")

    try:
        with sandbox_service.running_pool(settings, warm_pool_size=1) as (manager, factory):
            # A warm sandbox is the real orphan case: it has no owner, so the registry
            # has no row for it, and after a restart only the in-memory pool knows it
            # ever existed.
            warm_ids = manager.warm_sandbox_ids()
            assert warm_ids
            manager.detach()

            removed = manager.cleanup_orphans(keep_warm=False)

            assert set(removed) == set(warm_ids), "应清理自己创建但已无人持有的容器"
            for sandbox_id in warm_ids:
                assert sandbox_service.container_id_for(sandbox_id) is None
            assert factory.leaked_containers() == []

            survivor = subprocess.run(
                ["docker", "inspect", outsider, "--format", "{{.State.Running}}"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )
            assert survivor.returncode == 0, "清理不得触碰非本项目容器"
            assert survivor.stdout.strip() == "true"
    finally:
        subprocess.run(
            ["docker", "rm", "-f", outsider],
            capture_output=True,
            timeout=120,
            check=False,
        )


# --------------------------------------------------------------------------- #
# state machine
# --------------------------------------------------------------------------- #


def test_the_state_machine_rejects_illegal_transitions():
    assert_transition(SandboxStatus.CLAIMED, SandboxStatus.UNHEALTHY)
    assert_transition(SandboxStatus.RECOVERING, SandboxStatus.CLAIMED)
    assert_transition(SandboxStatus.WARM, SandboxStatus.CLAIMED)

    with pytest.raises(IllegalTransition):
        assert_transition(SandboxStatus.DESTROYED, SandboxStatus.CLAIMED)

    with pytest.raises(IllegalTransition):
        assert_transition(SandboxStatus.WARM, SandboxStatus.RECOVERING)

    # Recovery must be reachable from every broken state, and terminal states must be
    # terminal.
    assert SandboxStatus.RECOVERING in ALLOWED_TRANSITIONS[SandboxStatus.UNHEALTHY]
    assert ALLOWED_TRANSITIONS[SandboxStatus.DESTROYED] == frozenset()
