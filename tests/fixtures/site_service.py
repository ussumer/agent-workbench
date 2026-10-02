"""Start the demo quote site for acceptance tests.

The site is launched as its own process and reached over real HTTP, so the tests
exercise the same interface the sandbox scraper will later use.
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
SITE_SCRIPT = REPO_ROOT / "fixtures" / "site" / "server.py"

STARTUP_TIMEOUT_SECONDS = 60
SHUTDOWN_TIMEOUT_SECONDS = 15


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class FixtureSite:
    base_url: str
    port: int
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


def _wait_until_ready(port: int, process: subprocess.Popen, log_path: Path) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    with httpx.Client(timeout=httpx.Timeout(3.0, connect=1.0), trust_env=False) as probe:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
                raise RuntimeError(
                    f"fixture site exited during startup with code {process.returncode}:\n{tail}"
                )
            try:
                if probe.get(f"http://127.0.0.1:{port}/health").status_code == 200:
                    return
            except httpx.HTTPError:
                time.sleep(0.3)
    tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
    raise RuntimeError(f"fixture site not ready within {STARTUP_TIMEOUT_SECONDS}s:\n{tail}")


@contextlib.contextmanager
def running_site(
    run_dir: Path,
    *,
    port: int | None = None,
    test_mode: bool = True,
    host: str = "127.0.0.1",
) -> Iterator[FixtureSite]:
    """Launch the demo site and yield a live handle.

    ``test_mode=False`` starts the published surface, which has no injected
    failure endpoints. ``host="0.0.0.0"`` is needed when the site must also be
    reachable from inside a sandbox container (T08); the returned ``base_url``
    always uses loopback so host-side assertions keep working unchanged.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / ("site-test.log" if test_mode else "site.log")
    resolved_port = port if port is not None else free_port()

    env = dict(os.environ)
    env["FIXTURES_HOST"] = host
    env["FIXTURES_PORT"] = str(resolved_port)
    env["FIXTURES_TEST_MODE"] = "1" if test_mode else "0"
    env["PYTHONUNBUFFERED"] = "1"

    log_handle = log_path.open("wb")
    process = subprocess.Popen(
        [sys.executable, str(SITE_SCRIPT)],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    site = FixtureSite(
        base_url=f"http://127.0.0.1:{resolved_port}",
        port=resolved_port,
        process=process,
        log_path=log_path,
    )
    try:
        _wait_until_ready(resolved_port, process, log_path)
        yield site
    finally:
        site.stop()
        log_handle.close()
