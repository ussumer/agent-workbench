"""User sandbox pool: warm-up, claiming, registration, reconnection and recovery.

One sandbox per user, one proxy per user, and a small warm pool so a first message
does not pay for container creation. The pieces:

* **State machine** (:class:`SandboxStatus`) with an explicit legal-transition table.
* **Mongo registry** (:class:`MongoSandboxRegistry`) so the API can reconnect after
  a restart instead of leaking containers.
* **Per-user locks** serialising a user's sandbox operations, and a warm-pool lock
  making a claim exclusive.
* **Recovery** that builds a fully initialised replacement, registers it, and only
  then publishes it to the proxy — a half-initialised container is never handed to
  a run.

Nothing here re-executes a shell command. If a command raced with a replacement the
caller gets an error and decides what to do; the manager never replays work that may
have had side effects.
"""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol

from pymongo.database import Database

from agent.backends.custom_opensandbox import OpenSandboxBackend, create_backend
from agent.backends.sandbox_proxy import SandboxBackendProxy, SandboxReplacedError
from agent.backends.sandbox_setup import (
    SCRATCH_ROOT,
    SandboxRuntimeConfig,
    WorkspaceReport,
    resolve_image_digest,
)
from agent.persistence.indexes import COLLECTION_SANDBOX_REGISTRY

LOGGER = logging.getLogger("rush_harness.sandbox.manager")


class SandboxStatus(str, Enum):
    """Lifecycle of one registered sandbox."""

    CREATING = "creating"
    WARM = "warm"
    CLAIMED = "claimed"
    UNHEALTHY = "unhealthy"
    RECOVERING = "recovering"
    DESTROYING = "destroying"
    DESTROYED = "destroyed"
    FAILED = "failed"


#: Legal transitions. Anything else is a bug, not a race, and is rejected loudly.
ALLOWED_TRANSITIONS: dict[SandboxStatus, frozenset[SandboxStatus]] = {
    SandboxStatus.CREATING: frozenset({SandboxStatus.WARM, SandboxStatus.CLAIMED, SandboxStatus.FAILED}),
    SandboxStatus.WARM: frozenset({SandboxStatus.CLAIMED, SandboxStatus.DESTROYING}),
    SandboxStatus.CLAIMED: frozenset(
        {SandboxStatus.UNHEALTHY, SandboxStatus.RECOVERING, SandboxStatus.DESTROYING}
    ),
    SandboxStatus.UNHEALTHY: frozenset({SandboxStatus.RECOVERING, SandboxStatus.DESTROYING}),
    SandboxStatus.RECOVERING: frozenset(
        {SandboxStatus.CLAIMED, SandboxStatus.FAILED, SandboxStatus.DESTROYING}
    ),
    SandboxStatus.DESTROYING: frozenset({SandboxStatus.DESTROYED}),
    SandboxStatus.DESTROYED: frozenset(),
    SandboxStatus.FAILED: frozenset({SandboxStatus.RECOVERING, SandboxStatus.DESTROYING}),
}


class IllegalTransition(RuntimeError):
    """An attempted state change that the lifecycle does not allow."""


def assert_transition(current: SandboxStatus, target: SandboxStatus) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise IllegalTransition(f"{current.value} -> {target.value} is not allowed")


@dataclass(frozen=True)
class SandboxRegistration:
    """One row of the ``sandbox_registry`` collection."""

    user_id: str
    sandbox_id: str
    generation: int
    status: str
    image_digest: str
    created_at: datetime
    last_seen: datetime

    def as_document(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "sandbox_id": self.sandbox_id,
            "generation": self.generation,
            "status": self.status,
            "image_digest": self.image_digest,
            "created_at": self.created_at,
            "last_seen": self.last_seen,
        }


class SandboxRegistry(ABC):
    """Where sandbox ownership is recorded so it survives a restart."""

    @abstractmethod
    def ensure_indexes(self) -> None: ...

    @abstractmethod
    def upsert(self, registration: SandboxRegistration) -> None: ...

    @abstractmethod
    def get(self, user_id: str) -> SandboxRegistration | None: ...

    @abstractmethod
    def list_active(self) -> list[SandboxRegistration]: ...

    @abstractmethod
    def mark(self, user_id: str, status: SandboxStatus) -> None: ...

    @abstractmethod
    def remove(self, user_id: str) -> None: ...


class MongoSandboxRegistry(SandboxRegistry):
    """Registry backed by the project's MongoDB, which is what the contract requires."""

    def __init__(self, database: Database) -> None:
        self._collection = database[COLLECTION_SANDBOX_REGISTRY]

    def ensure_indexes(self) -> None:
        self._collection.create_index([("user_id", 1)], unique=True, name="uk_sandbox_registry_user")
        self._collection.create_index(
            [("sandbox_id", 1)], unique=True, name="uk_sandbox_registry_sandbox"
        )
        self._collection.create_index(
            [("status", 1), ("last_seen", -1)], name="ix_sandbox_registry_status"
        )

    def upsert(self, registration: SandboxRegistration) -> None:
        self._collection.update_one(
            {"user_id": registration.user_id},
            {"$set": registration.as_document()},
            upsert=True,
        )

    def get(self, user_id: str) -> SandboxRegistration | None:
        document = self._collection.find_one({"user_id": user_id}, {"_id": False})
        return _to_registration(document) if document else None

    def list_active(self) -> list[SandboxRegistration]:
        cursor = self._collection.find(
            {"status": {"$nin": [SandboxStatus.DESTROYED.value, SandboxStatus.DESTROYING.value]}},
            {"_id": False},
        )
        return [registration for doc in cursor if (registration := _to_registration(doc))]

    def mark(self, user_id: str, status: SandboxStatus) -> None:
        self._collection.update_one(
            {"user_id": user_id},
            {"$set": {"status": status.value, "last_seen": _now()}},
        )

    def remove(self, user_id: str) -> None:
        self._collection.delete_one({"user_id": user_id})


def _to_registration(document: dict[str, Any]) -> SandboxRegistration | None:
    try:
        return SandboxRegistration(
            user_id=document["user_id"],
            sandbox_id=document["sandbox_id"],
            generation=int(document["generation"]),
            status=str(document["status"]),
            image_digest=str(document.get("image_digest", "")),
            created_at=document["created_at"],
            last_seen=document["last_seen"],
        )
    except KeyError:
        return None


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class SandboxHandle:
    """A live sandbox plus what initialising it produced."""

    backend: OpenSandboxBackend
    report: WorkspaceReport | None
    image_digest: str

    @property
    def sandbox_id(self) -> str:
        return self.backend.id

    def close(self) -> None:
        self.backend.close()


class SandboxFactory(Protocol):
    """Creates and reconnects sandboxes. Injectable so tests can count containers."""

    def create(self) -> SandboxHandle: ...

    def reconnect(self, sandbox_id: str) -> SandboxHandle | None: ...

    def image_digest(self) -> str: ...

    def list_managed(self) -> list[str]: ...


class OpenSandboxFactory:
    """Default factory: real containers through the OpenSandbox control service."""

    #: Metadata key/value that marks a sandbox as belonging to this project.
    DEFAULT_MARKER = "rush-harness"

    def __init__(
        self,
        config: SandboxRuntimeConfig | None = None,
        *,
        metadata: dict[str, str] | None = None,
        marker: str = DEFAULT_MARKER,
    ) -> None:
        self._config = config or SandboxRuntimeConfig.from_env()
        self._marker = marker
        self._metadata = {"managed-by": marker, **(metadata or {})}
        self._digest: str | None = None

    @property
    def config(self) -> SandboxRuntimeConfig:
        return self._config

    def image_digest(self) -> str:
        if self._digest is None:
            self._digest = resolve_image_digest(self._config.image)
        return self._digest

    @property
    def marker(self) -> str:
        return self._marker

    def list_managed(self) -> list[str]:
        """Ids of every sandbox carrying this project's marker.

        Discovery is by metadata rather than by memory, which is what makes cleanup
        work after a restart: a new process has no ``_created_ids`` but the control
        service still knows which sandboxes belong to this project.
        """
        from opensandbox import SandboxManagerSync
        from opensandbox.models.sandboxes import SandboxFilter
        from opensandbox.sync.adapters.factory import AdapterFactorySync

        connection = self._config.connection_config()
        adapter = AdapterFactorySync(connection)
        manager = SandboxManagerSync(
            sandbox_service=adapter.create_sandbox_service(), connection_config=connection
        )
        page = manager.list_sandbox_infos(SandboxFilter(metadata={"managed-by": self._marker}))
        return [info.id for info in page.sandbox_infos]

    def create(self) -> SandboxHandle:
        backend, report = create_backend(
            self._config, metadata=self._metadata, prepare=True
        )
        return SandboxHandle(backend=backend, report=report, image_digest=self.image_digest())

    def reconnect(self, sandbox_id: str) -> SandboxHandle | None:
        """Attach to an existing container; ``None`` when it no longer exists."""
        from opensandbox.sync import SandboxSync

        try:
            sandbox = SandboxSync.connect(
                sandbox_id, connection_config=self._config.connection_config()
            )
        except Exception as failure:  # noqa: BLE001 - absence is a normal outcome
            LOGGER.info(
                "sandbox %s could not be reconnected (%s); it will be rebuilt",
                sandbox_id,
                type(failure).__name__,
            )
            return None
        backend = OpenSandboxBackend(sandbox, config=self._config)
        return SandboxHandle(backend=backend, report=None, image_digest=self.image_digest())


@dataclass
class PoolStats:
    users: int
    warm: int
    created: int
    destroyed: int
    recoveries: int
    proxies: dict[str, int] = field(default_factory=dict)


class SandboxManager:
    """Owns the warm pool, the per-user proxies and the registry."""

    def __init__(
        self,
        *,
        factory: SandboxFactory,
        registry: SandboxRegistry,
        warm_pool_size: int = 1,
        replenish_in_background: bool = True,
    ) -> None:
        self._factory = factory
        self._registry = registry
        self._warm_pool_size = max(0, warm_pool_size)
        self._replenish_in_background = replenish_in_background

        self._pool_lock = threading.Lock()
        self._warm: list[SandboxHandle] = []
        self._proxies: dict[str, SandboxBackendProxy] = {}
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()
        self._recovery_locks: dict[str, threading.Lock] = {}

        self._created_ids: set[str] = set()
        self._created = 0
        self._destroyed = 0
        self._recoveries = 0
        self._workers: list[threading.Thread] = []

    # ------------------------------------------------------------------ helpers

    def _user_lock(self, user_id: str) -> threading.RLock:
        with self._locks_guard:
            lock = self._locks.get(user_id)
            if lock is None:
                lock = threading.RLock()
                self._locks[user_id] = lock
            return lock

    def _recovery_lock(self, user_id: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._recovery_locks.get(user_id)
            if lock is None:
                lock = threading.Lock()
                self._recovery_locks[user_id] = lock
            return lock

    def _track(self, handle: SandboxHandle) -> None:
        self._created_ids.add(handle.sandbox_id)
        self._created += 1

    def _register(
        self, user_id: str, handle: SandboxHandle, *, generation: int, status: SandboxStatus
    ) -> SandboxRegistration:
        registration = SandboxRegistration(
            user_id=user_id,
            sandbox_id=handle.sandbox_id,
            generation=generation,
            status=status.value,
            image_digest=handle.image_digest,
            created_at=_now(),
            last_seen=_now(),
        )
        self._registry.upsert(registration)
        return registration

    # -------------------------------------------------------------- warm pool

    def start(self) -> None:
        """Create the registry indexes and fill the warm pool."""
        self._registry.ensure_indexes()
        self.replenish_warm_pool()

    def replenish_warm_pool(self) -> int:
        """Top the pool up to its target size. Returns how many were created."""
        created = 0
        while True:
            with self._pool_lock:
                if len(self._warm) >= self._warm_pool_size:
                    return created
                # Reserve the slot before the slow creation so concurrent callers do
                # not each decide the pool is short and overshoot the target.
                self._warm.append(None)  # type: ignore[arg-type]

            handle: SandboxHandle | None = None
            try:
                handle = self._factory.create()
                self._track(handle)
                # A warm sandbox has no owner yet, so it is deliberately absent from
                # the registry: `user_id` is unique there and an empty value would
                # collide with the next warm sandbox and confuse restart recovery.
            except Exception:  # noqa: BLE001 - a failed warm-up must not poison the pool
                LOGGER.exception("warm sandbox creation failed")
                handle = None
            finally:
                with self._pool_lock:
                    self._warm.remove(None)  # type: ignore[arg-type]
                    if handle is not None:
                        self._warm.append(handle)
                        created += 1

            if handle is None:
                return created

    def _claim_warm(self) -> SandboxHandle | None:
        """Take a warm sandbox out of the pool. Exclusive by construction."""
        with self._pool_lock:
            while self._warm:
                handle = self._warm.pop()
                if handle is None:  # a slot reserved by an in-flight creation
                    self._warm.append(None)  # type: ignore[arg-type]
                    return None
                return handle
        return None

    def _spawn_replenish(self) -> None:
        if not self._replenish_in_background:
            self.replenish_warm_pool()
            return
        worker = threading.Thread(
            target=self.replenish_warm_pool, name="sandbox-warm-replenish", daemon=True
        )
        self._workers.append(worker)
        worker.start()

    def warm_count(self) -> int:
        with self._pool_lock:
            return sum(1 for handle in self._warm if handle is not None)

    def warm_sandbox_ids(self) -> list[str]:
        """Sandbox ids currently sitting in the warm pool."""
        with self._pool_lock:
            return [handle.sandbox_id for handle in self._warm if handle is not None]

    def detach(self) -> None:
        """Drop in-memory state *without* destroying containers.

        This is what an API restart looks like: the process forgets its proxies and
        its warm pool, and a new manager must rebuild that knowledge from the registry
        rather than leaving orphan containers behind.
        """
        with self._pool_lock:
            self._warm = []
        self._proxies.clear()
        LOGGER.info("sandbox manager detached; containers left running for reconnection")

    # ------------------------------------------------------- get or create

    def _proxy_for(self, user_id: str, handle: SandboxHandle, *, generation: int) -> SandboxBackendProxy:
        # `_track` is deliberately not called here: a claimed warm sandbox was already
        # counted when it was created, and counting it twice made the created/destroyed
        # bookkeeping unusable for leak checks.
        proxy = SandboxBackendProxy(handle.backend, owner_user_id=user_id, generation=generation)
        with self._locks_guard:
            self._proxies[user_id] = proxy
        self._register(user_id, handle, generation=generation, status=SandboxStatus.CLAIMED)
        return proxy

    def get_or_create(self, user_id: str) -> SandboxBackendProxy:
        """Return this user's sandbox, creating, reconnecting or claiming as needed.

        An existing proxy is returned *as is*, even when its container is currently
        broken. Proxy identity is the contract's stable handle: a dead container is
        repaired by :meth:`recover`, which swaps the backend inside this same object.
        Handing out a fresh proxy instead would silently break every reference the
        graph, the middleware and the tools already hold.
        """
        with self._user_lock(user_id):
            existing = self._proxies.get(user_id)
            if existing is not None:
                return existing

            reconnected = self._reconnect_registered(user_id)
            if reconnected is not None:
                return reconnected

            claimed = self._claim_warm()
            if claimed is not None:
                LOGGER.info("user=%s claimed warm sandbox %s", user_id, claimed.sandbox_id)
                proxy = self._proxy_for(user_id, claimed, generation=1)
                # Replenish only after the claim is fully published, so a concurrent
                # claimant cannot race the same warm sandbox.
                self._spawn_replenish()
                return proxy

            handle = self._factory.create()
            LOGGER.info("user=%s created sandbox %s", user_id, handle.sandbox_id)
            return self._proxy_for(user_id, handle, generation=1)

    def _reconnect_registered(self, user_id: str) -> SandboxBackendProxy | None:
        registration = self._registry.get(user_id)
        if registration is None or registration.status in {
            SandboxStatus.DESTROYED.value,
            SandboxStatus.DESTROYING.value,
            SandboxStatus.FAILED.value,
        }:
            return None
        handle = self._factory.reconnect(registration.sandbox_id)
        if handle is None:
            LOGGER.info(
                "registered sandbox %s for user=%s is gone; rebuilding",
                registration.sandbox_id,
                user_id,
            )
            self._registry.remove(user_id)
            return None
        proxy = self._proxy_for(user_id, handle, generation=max(1, registration.generation))
        LOGGER.info("user=%s reconnected to sandbox %s", user_id, handle.sandbox_id)
        return proxy

    def get(self, user_id: str) -> SandboxBackendProxy | None:
        return self._proxies.get(user_id)

    # ------------------------------------------------------------- health

    def _is_alive(self, proxy: SandboxBackendProxy) -> bool:
        try:
            response = proxy.execute("true", timeout=30)
        except SandboxReplacedError:
            return False
        except Exception:  # noqa: BLE001 - an unreachable sandbox is simply not alive
            return False
        return response.exit_code == 0

    def check_health(self, user_id: str) -> bool:
        proxy = self._proxies.get(user_id)
        if proxy is None:
            return False
        return self._is_alive(proxy)

    def ensure_healthy(self, user_id: str) -> SandboxBackendProxy:
        """Return a healthy proxy, recovering the sandbox when necessary."""
        proxy = self.get_or_create(user_id)
        if self._is_alive(proxy):
            return proxy
        self._registry.mark(user_id, SandboxStatus.UNHEALTHY)
        return self.recover(user_id)

    # ------------------------------------------------------------ recovery

    def recover(self, user_id: str) -> SandboxBackendProxy:
        """Build a replacement generation. Only one rebuilder gets through.

        The new container is created, initialised and registered *before* it is
        published. If registration fails the container is reclaimed, so a caller can
        never observe a half-initialised sandbox.
        """
        proxy = self.get_or_create(user_id)
        with self._recovery_lock(user_id):
            # Second check inside the lock: another thread may have finished recovering
            # while this one was waiting, and a second rebuild would leak a container.
            if not proxy.is_replacing() and self._is_alive(proxy):
                LOGGER.info("user=%s sandbox already healthy; recovery skipped", user_id)
                return proxy

            proxy.begin_replacement()
            self._registry.mark(user_id, SandboxStatus.RECOVERING)
            handle: SandboxHandle | None = None
            try:
                # Creating the container is itself a failure point, so it belongs inside
                # the guard: otherwise a failed creation would leave the registry stuck
                # in `recovering` and the proxy permanently marked as being replaced.
                handle = self._factory.create()
                self._track(handle)
                if handle.report is None:
                    from agent.backends.sandbox_setup import prepare_workspace

                    handle.report = prepare_workspace(
                        handle.backend.sandbox, handle.backend.config
                    )
                self._restore_managed_files(user_id, handle)
                next_generation = proxy.generation + 1
                self._register(
                    user_id,
                    handle,
                    generation=next_generation,
                    status=SandboxStatus.CLAIMED,
                )
            except Exception:
                LOGGER.exception("recovery for user=%s failed; reclaiming the new container", user_id)
                if handle is not None:
                    try:
                        handle.close()
                    finally:
                        self._destroyed += 1
                # Release the replacement flag: the proxy keeps pointing at the old
                # generation, so callers get a real transport error rather than a
                # permanent "being replaced" verdict, and the next health probe can
                # try again.
                proxy.abort_replacement()
                self._registry.mark(user_id, SandboxStatus.FAILED)
                raise

            generation = proxy.replace_backend(handle.backend)
            if generation != next_generation:  # pragma: no cover - defensive
                raise IllegalTransition(
                    f"generation drift: registered {next_generation}, published {generation}"
                )
            self._recoveries += 1
            LOGGER.info(
                "user=%s recovered onto sandbox %s generation=%s",
                user_id,
                handle.sandbox_id,
                generation,
            )
            return proxy

    def _restore_managed_files(self, user_id: str, handle: SandboxHandle) -> None:
        """Re-upload anything that must survive a container replacement.

        Rule files come back with ``prepare_workspace``. Persisted skills and
        preferences live in the Store and are re-materialised here by T10; until then
        this only re-creates the per-run scratch space so the workspace contract
        holds after every recovery.
        """
        handle.backend.execute(f"mkdir -p {SCRATCH_ROOT}", timeout=30)

    # ------------------------------------------------------------- teardown

    def recycle(self, user_id: str) -> bool:
        """Destroy this user's sandbox and drop its registration."""
        with self._user_lock(user_id):
            proxy = self._proxies.pop(user_id, None)
            if proxy is None:
                self._registry.remove(user_id)
                return False
            self._registry.mark(user_id, SandboxStatus.DESTROYING)
            try:
                proxy.backend.close()
            finally:
                self._destroyed += 1
                self._registry.remove(user_id)
                self._registry.mark(user_id, SandboxStatus.DESTROYED)
                self._registry.remove(user_id)
            LOGGER.info("user=%s sandbox recycled", user_id)
            return True

    def shutdown(self) -> dict[str, int]:
        """Destroy the warm pool and every proxy this manager created.

        Only sandboxes created by this manager are touched; unrelated containers on
        the host are left alone.
        """
        destroyed = 0
        with self._pool_lock:
            warm, self._warm = self._warm, []
        for handle in warm:
            if handle is None:
                continue
            try:
                handle.close()
            except Exception:  # noqa: BLE001 - teardown must not mask other failures
                LOGGER.exception("failed to destroy warm sandbox %s", handle.sandbox_id)
            destroyed += 1

        for user_id, proxy in list(self._proxies.items()):
            try:
                proxy.backend.close()
            except Exception:  # noqa: BLE001
                LOGGER.exception("failed to destroy sandbox for user=%s", user_id)
            destroyed += 1
            self._registry.remove(user_id)
        self._proxies.clear()

        self._destroyed += destroyed
        return {"destroyed": destroyed, "warm_remaining": len(self._warm)}

    def stats(self) -> PoolStats:
        return PoolStats(
            users=len(self._proxies),
            warm=self.warm_count(),
            created=self._created,
            destroyed=self._destroyed,
            recoveries=self._recoveries,
            proxies={user: proxy.generation for user, proxy in self._proxies.items()},
        )

    # ------------------------------------------------------------- cleanup

    def cleanup_orphans(self, *, keep_warm: bool = True) -> list[str]:
        """Destroy this project's sandboxes that have no live owner.

        Candidates come from two sources: what this process remembers creating, and
        what the control service reports as carrying this project's marker. The
        second source is what makes cleanup work after a restart, when memory is gone
        but the orphaned containers are still running.

        Ownership scope is strict: only sandboxes marked for this project are
        considered, so the user's other containers are never touched. ``keep_warm``
        preserves warm containers that this process still knows about.

        Deliberately not called from :meth:`start`. A warm container has no registry
        row, so it is indistinguishable from another *live* process's warm container;
        running this automatically would let one instance destroy another's pool. It
        is an explicit operation: at boot of a single instance, or from an operator
        command, with ``keep_warm=False``.
        """
        registered = {registration.sandbox_id for registration in self._registry.list_active()}
        with self._pool_lock:
            warm_ids = {handle.sandbox_id for handle in self._warm if handle is not None}
        protected = registered | (warm_ids if keep_warm else set())

        candidates = set(self._created_ids)
        lister = getattr(self._factory, "list_managed", None)
        if callable(lister):
            candidates |= set(lister())

        removed: list[str] = []
        for sandbox_id in sorted(candidates):
            if sandbox_id in protected:
                continue
            if _destroy_sandbox(sandbox_id, self._factory):
                self._destroyed += 1
                removed.append(sandbox_id)
        if removed:
            LOGGER.info("cleanup removed orphan sandboxes: %s", removed)
        return removed

    @staticmethod
    def run_workspace(thread_id: str, run_id: str) -> str:
        """Per-run path layout required by the contract."""
        return f"/workspace/{thread_id}/{run_id}"


def _destroy_sandbox(sandbox_id: str, factory: SandboxFactory) -> bool:
    """Destroy one orphan sandbox through the factory's own connection settings."""
    handle = factory.reconnect(sandbox_id)
    if handle is None:
        return False  # already gone
    try:
        handle.close()
    except Exception:  # noqa: BLE001 - already gone is a fine outcome
        LOGGER.info("orphan sandbox %s could not be destroyed cleanly", sandbox_id)
    return True


def managed_pool(
    database: Database,
    *,
    config: SandboxRuntimeConfig | None = None,
    warm_pool_size: int = 1,
    factory: SandboxFactory | None = None,
    registry: SandboxRegistry | None = None,
) -> SandboxManager:
    """Build a manager wired for real use, starting the pool."""
    manager = SandboxManager(
        factory=factory or OpenSandboxFactory(config),
        registry=registry or MongoSandboxRegistry(database),
        warm_pool_size=warm_pool_size,
    )
    manager.start()
    return manager


def call_with_recovery(
    manager: SandboxManager,
    user_id: str,
    operation: Callable[[SandboxBackendProxy], Any],
) -> Any:
    """Run one operation, recovering once if the sandbox turns out to be broken."""
    proxy = manager.get_or_create(user_id)
    try:
        return operation(proxy)
    except SandboxReplacedError:
        raise
    except Exception:
        healthy = manager.ensure_healthy(user_id)
        return operation(healthy)
