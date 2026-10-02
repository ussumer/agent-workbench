"""Start the real MCP gateway process for acceptance tests.

The gateway is launched exactly the way it runs in production — a separate
process serving Streamable HTTP — so the tests exercise genuine MCP protocol
traffic rather than importing the tool functions directly.
"""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"

STARTUP_TIMEOUT_SECONDS = 90
SHUTDOWN_TIMEOUT_SECONDS = 20

DEFAULT_GRANT_SECRET = "acceptance-grant-secret"
DEFAULT_ERP_TOKEN = "acceptance-service-token"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def port_is_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        return sock.connect_ex((host, port)) == 0


def wait_until_closed(port: int, *, timeout: float = 10.0, host: str = "127.0.0.1") -> bool:
    """Wait for the OS to finish tearing the listener down, then report the final state."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not port_is_open(port, host):
            return True
        time.sleep(0.2)
    return not port_is_open(port, host)


@dataclass
class McpGateway:
    """A running gateway process plus what tests need to talk to it."""

    base_url: str
    mcp_url: str
    port: int
    grant_secret: str
    erp_token: str
    process: subprocess.Popen
    log_path: Path

    def stop(self) -> None:
        if self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:  # pragma: no cover - only on a wedged process
            self.process.kill()
            self.process.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)

    def log_tail(self, limit: int = 4000) -> str:
        return self.log_path.read_text(encoding="utf-8", errors="replace")[-limit:]


def _wait_until_serving(port: int, process: subprocess.Popen, log_path: Path) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    url = f"http://127.0.0.1:{port}/mcp"
    with httpx.Client(timeout=httpx.Timeout(3.0, connect=1.0)) as probe:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
                raise RuntimeError(
                    f"gateway exited during startup with code {process.returncode}:\n{tail}"
                )
            try:
                # Any HTTP status means the ASGI app is answering; the MCP handshake is the
                # tests' own first real operation.
                probe.get(url)
                return
            except httpx.HTTPError:
                time.sleep(0.5)
    tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
    raise RuntimeError(f"gateway not serving within {STARTUP_TIMEOUT_SECONDS}s:\n{tail}")


@contextlib.contextmanager
def running_gateway(
    run_dir: Path,
    *,
    erp_base_url: str,
    erp_token: str = DEFAULT_ERP_TOKEN,
    grant_secret: str = DEFAULT_GRANT_SECRET,
    port: int | None = None,
    extra_env: dict[str, str] | None = None,
) -> Iterator[McpGateway]:
    """Launch the gateway against ``erp_base_url`` and yield a live handle."""
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "mcp-gateway.log"
    resolved_port = port if port is not None else free_port()

    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUNBUFFERED"] = "1"
    env["ERP_BASE_URL"] = erp_base_url
    env["ERP_SERVICE_TOKEN"] = erp_token
    env["MCP_GRANT_SECRET"] = grant_secret
    env["MCP_HOST"] = "127.0.0.1"
    env["MCP_PORT"] = str(resolved_port)
    env.update(extra_env or {})

    log_handle = log_path.open("wb")
    process = subprocess.Popen(
        [sys.executable, "-m", "mcp_server.server_main"],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    base_url = f"http://127.0.0.1:{resolved_port}"
    gateway = McpGateway(
        base_url=base_url,
        mcp_url=f"{base_url}/mcp",
        port=resolved_port,
        grant_secret=grant_secret,
        erp_token=erp_token,
        process=process,
        log_path=log_path,
    )
    try:
        _wait_until_serving(resolved_port, process, log_path)
        yield gateway
    finally:
        gateway.stop()
        log_handle.close()
