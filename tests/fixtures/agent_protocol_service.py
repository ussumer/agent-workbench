"""Standalone Agent Protocol service helpers for tests.

The service runs from its own virtual environment (see
``infra/agent-protocol/README.md`` for why it cannot share the application lock).
Tests never substitute an in-process fake: CAP-08 exists to prove that a real server
process, reached over HTTP by the official client, produces a task result.

If the environment or the config is missing, the tests fail with the exact command
needed to fix it rather than skipping.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_DIR = REPO_ROOT / ".venv-agent-protocol"
CONFIG_PATH = REPO_ROOT / "infra" / "agent-protocol" / "langgraph.json"
LOG_DIR = REPO_ROOT / "infra" / "agent-protocol"

SERVICE_HOST = "127.0.0.1"
SERVICE_PORT = 8123
GRAPH_ID = "echo"

STARTUP_TIMEOUT_SECONDS = 180

PROVISION_COMMAND = "python scripts/provision_agent_protocol.py"

START_COMMAND = (
    r'$env:PYTHONIOENCODING="utf-8"; '
    r".venv-agent-protocol\Scripts\langgraph.exe dev "
    r"--config infra/agent-protocol/langgraph.json --port 8123 --host 127.0.0.1 "
    r"--no-browser --no-reload"
)


class AgentProtocolUnavailable(RuntimeError):
    """The standalone Agent Protocol service cannot be reached or provisioned."""


@dataclass(frozen=True)
class ServiceHandle:
    """A reachable service plus how it was obtained."""

    base_url: str
    started_by_test: bool

    def url(self, path: str = "") -> str:
        return f"{self.base_url}{path}"


def environment_python() -> Path:
    candidate = ENV_DIR / "Scripts" / "python.exe"
    return candidate if candidate.is_file() else ENV_DIR / "bin" / "python"


def require_environment() -> Path:
    """Fail with an actionable message when the isolated environment is absent."""
    python = environment_python()
    if not python.is_file():
        raise AgentProtocolUnavailable(
            f"the Agent Protocol service environment is missing at {ENV_DIR}. Provision it with:\n"
            f"  {PROVISION_COMMAND}"
        )
    if not CONFIG_PATH.is_file():
        raise AgentProtocolUnavailable(f"missing service config: {CONFIG_PATH}")
    return python


def service_health(base_url: str, *, timeout: float = 5.0) -> bool:
    """Whether the service answers its readiness probe."""
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            response = client.get(f"{base_url}/ok")
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def _launcher() -> Path:
    for name in ("langgraph.exe", "langgraph"):
        candidate = ENV_DIR / "Scripts" / name
        if candidate.is_file():
            return candidate
    candidate = ENV_DIR / "bin" / "langgraph"
    if candidate.is_file():
        return candidate
    raise AgentProtocolUnavailable(
        f"no langgraph entry point in {ENV_DIR}. Provision it with:\n  {PROVISION_COMMAND}"
    )


@contextlib.contextmanager
def running_service(
    *,
    port: int = SERVICE_PORT,
    reuse_running: bool = True,
    startup_timeout: float = STARTUP_TIMEOUT_SECONDS,
) -> Iterator[ServiceHandle]:
    """Yield a reachable service, starting one only if needed.

    Reusing an already-running instance keeps the suite fast during development; the
    tests that need to observe startup do so by checking the health probe themselves.
    """
    require_environment()
    base_url = f"http://{SERVICE_HOST}:{port}"

    if reuse_running and service_health(base_url):
        yield ServiceHandle(base_url=base_url, started_by_test=False)
        return

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stdout_path = LOG_DIR / "server.out.log"
    stderr_path = LOG_DIR / "server.err.log"

    env = dict(os.environ)
    # The CLI and its logs crash on a GBK console without this.
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            [
                str(_launcher()),
                "dev",
                "--config",
                str(CONFIG_PATH),
                "--port",
                str(port),
                "--host",
                SERVICE_HOST,
                "--no-browser",
                "--no-reload",
            ],
            cwd=str(REPO_ROOT),
            stdout=stdout,
            stderr=stderr,
            env=env,
        )

    try:
        deadline = time.monotonic() + startup_timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AgentProtocolUnavailable(
                    f"the Agent Protocol service exited with code {process.returncode}; "
                    f"see {stderr_path}"
                )
            if service_health(base_url):
                break
            time.sleep(1.0)
        else:
            raise AgentProtocolUnavailable(
                f"the Agent Protocol service did not become ready within {startup_timeout:.0f}s; "
                f"see {stdout_path} and {stderr_path}"
            )
        yield ServiceHandle(base_url=base_url, started_by_test=True)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:  # pragma: no cover - stubborn child
                process.kill()
                process.wait(timeout=30)


def client(base_url: str):
    """Official synchronous Agent Protocol client."""
    from langgraph_sdk import get_sync_client

    return get_sync_client(url=base_url)


async def async_client(base_url: str):
    """Official asynchronous Agent Protocol client."""
    from langgraph_sdk import get_client

    return get_client(url=base_url)


def assistant_id_for(sync_client, graph_id: str = GRAPH_ID) -> str:
    """The id of one served graph, discovered through the protocol.

    Discovered rather than configured: assistant ids are allocated by the service, so a fresh
    database and a reused one hand out different values for the same graph.
    """
    assistants = sync_client.assistants.search()
    for assistant in assistants:
        if assistant.get("graph_id") == graph_id:
            return assistant["assistant_id"]
    raise AgentProtocolUnavailable(
        f"the service exposes no {graph_id!r} graph; got "
        f"{[a.get('graph_id') for a in assistants]}"
    )


def echo_assistant_id(sync_client) -> str:
    """Backwards-compatible name for the echo graph's assistant id (used by T06)."""
    return assistant_id_for(sync_client, GRAPH_ID)


def diagnostic() -> dict[str, object]:
    """Environment facts for the evidence record."""
    return {
        "env_dir": str(ENV_DIR),
        "env_present": ENV_DIR.is_dir(),
        "config": str(CONFIG_PATH),
        "python": sys.version.split()[0],
        "start_command": START_COMMAND,
    }
