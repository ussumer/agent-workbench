"""OpenSandbox helpers for integration and acceptance tests.

The control service and the execution image are external prerequisites: the tests
report a precise, actionable failure when either is missing instead of skipping or
silently substituting a local shell. Nothing here executes commands on the host as
a fallback — the whole point of T08 is that execution happens in a container.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx

#: Docker CLI output is not guaranteed to be decodable with the host's default codec.
DOCKER_ENCODING = "utf-8"

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agent.backends.custom_opensandbox import OpenSandboxBackend, create_backend  # noqa: E402
from agent.backends.sandbox_setup import SCRATCH_ROOT, SandboxRuntimeConfig  # noqa: E402

CONTROL_HOST = "127.0.0.1"
CONTROL_PORT = 18080

#: How a sandbox reaches a service running on the host, under bridge networking.
SANDBOX_HOST_ALIAS = "host.docker.internal"

STARTUP_TIMEOUT_SECONDS = 60
SANDBOX_CREATE_TIMEOUT_SECONDS = 300

START_COMMAND = (
    r'$env:OPENSANDBOX_INSECURE_SERVER="YES"; '
    r".venv\Scripts\opensandbox-server.exe --config infra/sandbox/sandbox.toml"
)


class SandboxUnavailable(RuntimeError):
    """The sandbox control service or execution image is not available."""


def control_settings() -> SandboxRuntimeConfig:
    """Runtime configuration pointed at the local control service."""
    env = dict(os.environ)
    # The integration profile owns a fixed local control endpoint. Development .env
    # may use an alias or a hosted domain; neither changes the test service contract.
    env["OPENSANDBOX_DOMAIN"] = f"{CONTROL_HOST}:{CONTROL_PORT}"
    env["OPENSANDBOX_BASE_URL"] = f"http://{CONTROL_HOST}:{CONTROL_PORT}"
    config = SandboxRuntimeConfig.from_env(env)
    # Tests never use a hosted endpoint or an api_key: see infra/sandbox/README.md.
    return SandboxRuntimeConfig(
        domain=config.domain,
        protocol=config.protocol,
        image=config.image,
        timeout_seconds=config.timeout_seconds,
        ready_timeout_seconds=config.ready_timeout_seconds,
        default_exec_timeout=config.default_exec_timeout,
        cpu=config.cpu,
        memory=config.memory,
    )


def service_health(config: SandboxRuntimeConfig, *, timeout: float = 10.0) -> dict | None:
    """Return the control service health document, or ``None`` when unreachable."""
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            response = client.get(f"{config.base_url()}/health")
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        return response.json()
    except ValueError:
        return {"status": "unknown"}


def require_control_service(config: SandboxRuntimeConfig) -> dict:
    """Fail with an actionable message when the control service is not running."""
    health = service_health(config)
    if health is None:
        raise SandboxUnavailable(
            f"OpenSandbox control service is not answering at {config.base_url()}. Start it with:\n"
            f"  {START_COMMAND}"
        )
    return health


def _docker(*args: str, timeout: float = 120) -> subprocess.CompletedProcess[str]:
    """Run a read-only docker command with an explicit, tolerant text encoding."""
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        encoding=DOCKER_ENCODING,
        errors="replace",
        timeout=timeout,
        check=False,
    )


def require_image(config: SandboxRuntimeConfig) -> None:
    """Fail when the pinned execution image has not been pulled."""
    completed = _docker("image", "inspect", config.image, "--format", "{{.Id}}")
    if completed.returncode != 0:
        raise SandboxUnavailable(
            f"execution image {config.image} is missing; run:\n  docker pull {config.image}"
        )


@contextmanager
def running_backend(
    *,
    config: SandboxRuntimeConfig | None = None,
    prepare: bool = True,
    metadata: dict[str, str] | None = None,
) -> Iterator[tuple[OpenSandboxBackend, object]]:
    """Create a real sandbox, yield the backend, and always destroy the container."""
    resolved = config or control_settings()
    require_control_service(resolved)
    require_image(resolved)

    labels = {"purpose": "t08-acceptance", "run": uuid.uuid4().hex[:8], **(metadata or {})}
    backend, report = create_backend(resolved, prepare=prepare, metadata=labels)
    try:
        yield backend, report
    finally:
        backend.close()


def container_id_for(sandbox_id: str) -> str | None:
    """Find the Docker container backing one sandbox, for lifecycle assertions."""
    completed = _docker("ps", "-a", "--format", "{{.ID}}\t{{.Names}}")
    if completed.returncode != 0:
        return None
    for line in completed.stdout.splitlines():
        if sandbox_id in line:
            return line.split("\t", 1)[0].strip()
    return None


def container_exists(container_id: str) -> bool:
    return _docker("inspect", container_id).returncode == 0


def wait_until_container_gone(container_id: str, *, timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not container_exists(container_id):
            return True
        time.sleep(0.5)
    return not container_exists(container_id)


def write_host_sentinel(directory: Path) -> tuple[Path, str]:
    """Create a host-only file the sandbox must never be able to read or change.

    The digest is taken from the bytes that actually landed on disk. Hashing the
    pre-write string would be wrong on Windows, where text mode rewrites ``\\n`` as
    ``\\r\\n`` and the comparison would then fail for the wrong reason.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "host-sentinel.txt"
    path.write_text(f"host-only-{uuid.uuid4().hex}\n", encoding="utf-8", newline="")
    return path, host_sentinel_digest(path)


def host_sentinel_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sandbox_url_for_host_config(site_base_url: str) -> str:
    """Rewrite a host-side base URL into the address a sandbox must use."""
    _, _, host_port = site_base_url.partition("://")
    _, _, port = host_port.partition(":")
    return f"http://{SANDBOX_HOST_ALIAS}:{port or '80'}"


# --------------------------------------------------------------------------- #
# sandbox pool (T09)
# --------------------------------------------------------------------------- #


class CountingFactory:
    """Wraps a real factory and records every container it creates or reconnects.

    Used to make leak assertions concrete: "no container left behind" is checked
    against the actual set of created sandbox ids, not against a feeling.
    """

    def __init__(self, inner) -> None:
        self._inner = inner
        self.created_ids: list[str] = []
        self.reconnect_attempts: list[str] = []

    def create(self):
        handle = self._inner.create()
        self.created_ids.append(handle.sandbox_id)
        return handle

    def reconnect(self, sandbox_id: str):
        self.reconnect_attempts.append(sandbox_id)
        return self._inner.reconnect(sandbox_id)

    def image_digest(self) -> str:
        return self._inner.image_digest()

    def list_managed(self) -> list[str]:
        return self._inner.list_managed()

    def live_containers(self) -> list[str]:
        """Created sandboxes whose container still exists."""
        return [sid for sid in self.created_ids if container_id_for(sid) is not None]

    def leaked_containers(self, expected_alive: set[str] | None = None) -> list[str]:
        expected = expected_alive or set()
        return [sid for sid in self.live_containers() if sid not in expected]


@contextmanager
def running_pool(
    settings,
    *,
    warm_pool_size: int = 1,
    warm_in_background: bool = False,
    factory=None,
    registry=None,
) -> Iterator[tuple[object, CountingFactory]]:
    """A real manager over a private test database, always torn down.

    ``warm_in_background=False`` by default so tests observe the pool in a settled
    state instead of racing a background thread; the concurrent tests flip it on
    deliberately.
    """
    from pymongo import MongoClient

    from agent.backends.sandbox_manager import (
        MongoSandboxRegistry,
        OpenSandboxFactory,
        SandboxManager,
    )

    config = control_settings()
    require_control_service(config)
    require_image(config)

    counting = CountingFactory(
        factory or OpenSandboxFactory(
            config,
            metadata={"purpose": "t09-acceptance"},
            # Stable across restarts of this database, separate from other test/dev pools.
            marker="rush-harness-" + hashlib.sha256(settings.database.encode()).hexdigest()[:16],
        )
    )
    with MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000, tz_aware=True) as client:
        database = client[settings.database]
        manager = SandboxManager(
            factory=counting,
            registry=registry or MongoSandboxRegistry(database),
            warm_pool_size=warm_pool_size,
            replenish_in_background=warm_in_background,
        )
        manager.start()
        try:
            yield manager, counting
        finally:
            manager.shutdown()


def seed_user_file(proxy, user_id: str, name: str = "secret.txt") -> str:
    """Write a per-user marker file so cross-user isolation can be tested."""
    path = f"{SCRATCH_ROOT}/{user_id}-{name}"
    proxy.upload_files([(path, f"private-data-for-{user_id}".encode())])
    return path
