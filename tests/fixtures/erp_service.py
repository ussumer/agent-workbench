"""Start the real packaged Java ERP for acceptance tests.

This module is test infrastructure, not business logic: it builds the Spring Boot
jar, launches it as a separate process against a throwaway file database, waits
for readiness and hands back an HTTP client. Acceptance then talks to the
service exactly like the MCP gateway will, over real HTTP with the internal
service token.

Nothing here swaps the ERP for a Python stub, and nothing points at the
developer's own database file.
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
ERP_DIR = REPO_ROOT / "erp"
SCRIPTS_DIR = REPO_ROOT / "scripts"

SERVICE_TOKEN = "acceptance-service-token"
BUILD_TIMEOUT_SECONDS = 900
STARTUP_TIMEOUT_SECONDS = 180
SHUTDOWN_TIMEOUT_SECONDS = 30


def _config_module():
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    from lib import config

    return config


def java_executable() -> str:
    """Resolve the JDK's java launcher, reusing the same detection the gate uses."""
    config = _config_module()
    env = config.build_env(config.load_dotenv())
    home = env.get("JAVA_HOME")
    if not home:
        raise RuntimeError("no JDK found; set JAVA_HOME to a directory containing bin/javac")
    candidate = Path(home) / "bin" / ("java.exe" if os.name == "nt" else "java")
    if not candidate.is_file():
        raise RuntimeError(f"java launcher missing at {candidate}")
    return str(candidate)


def _mvnw() -> list[str]:
    name = "mvnw.cmd" if os.name == "nt" else "mvnw"
    script = ERP_DIR / name
    if not script.is_file():
        raise RuntimeError(f"maven wrapper missing at {script}")
    return [str(script)]


def _jar_path() -> Path | None:
    candidates = [
        path
        for path in ERP_DIR.glob("target/procurement-erp-*.jar")
        if not path.name.endswith(".original")
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _newest_source_mtime() -> float:
    newest = 0.0
    for pattern in ("src/**/*.java", "src/**/*.sql", "src/**/*.yml", "pom.xml"):
        for path in ERP_DIR.glob(pattern):
            if path.is_file():
                newest = max(newest, path.stat().st_mtime)
    return newest


def ensure_jar(log: Path | None = None) -> Path:
    """Return an up-to-date Spring Boot jar, building it when missing or stale."""
    jar = _jar_path()
    if jar is not None and jar.stat().st_mtime >= _newest_source_mtime():
        return jar

    env = dict(os.environ)
    config = _config_module()
    env = config.build_env({**config.load_dotenv(), **env})
    completed = subprocess.run(
        [*_mvnw(), "-B", "-DskipTests", "package"],
        cwd=str(ERP_DIR),
        env=env,
        capture_output=True,
        timeout=BUILD_TIMEOUT_SECONDS,
        check=False,
    )
    if log is not None:
        log.write_bytes(completed.stdout + completed.stderr)
    if completed.returncode != 0:
        tail = (completed.stdout + completed.stderr).decode("utf-8", "replace")[-4000:]
        raise RuntimeError(f"java package failed (exit {completed.returncode}):\n{tail}")

    jar = _jar_path()
    if jar is None:
        raise RuntimeError("java package succeeded but no jar was produced")
    return jar


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class ErpService:
    """A running ERP process plus the coordinates acceptance needs to talk to it."""

    base_url: str
    token: str
    db_path: Path
    port: int
    process: subprocess.Popen
    log_path: Path

    def url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def database_files(self) -> list[Path]:
        """Files H2 created for this database (``<name>.mv.db`` and friends)."""
        return sorted(self.db_path.parent.glob(f"{self.db_path.name}*"))

    def headers(self, *, actor: str = "demo-a", request_id: str | None = None) -> dict[str, str]:
        headers = {"X-Service-Token": self.token, "X-Actor-Id": actor}
        if request_id:
            headers["X-Request-Id"] = request_id
        return headers

    def client(self, **kwargs) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            headers=self.headers(),
            timeout=httpx.Timeout(30.0, connect=10.0),
            **kwargs,
        )

    def stop(self) -> None:
        if self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:  # pragma: no cover - only on a wedged JVM
            self.process.kill()
            self.process.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)


def _wait_until_ready(base_url: str, process: subprocess.Popen, log_path: Path) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    last_error = "no attempt made"
    with httpx.Client(timeout=httpx.Timeout(5.0, connect=2.0)) as probe:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
                raise RuntimeError(
                    f"erp exited during startup with code {process.returncode}:\n{tail}"
                )
            try:
                response = probe.get(f"{base_url}/api/erp/v1/health/ready")
                if response.status_code == 200:
                    return
                last_error = f"readiness returned {response.status_code}"
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(1.0)
    tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
    raise RuntimeError(f"erp not ready within {STARTUP_TIMEOUT_SECONDS}s ({last_error}):\n{tail}")


@contextlib.contextmanager
def running_erp(
    run_dir: Path,
    *,
    seed_path: Path,
    db_name: str = "acceptance",
    token: str = SERVICE_TOKEN,
) -> Iterator[ErpService]:
    """Launch the packaged ERP against ``run_dir`` and yield a live handle.

    ``run_dir`` owns both the database files and the captured stdout/stderr, so a
    failed attempt leaves its evidence behind instead of polluting the demo data.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "erp.log"
    build_log = run_dir / "java-package.log"
    jar = ensure_jar(build_log if build_log != log_path else None)

    db_path = (run_dir / "db" / db_name).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    port = free_port()

    env = dict(os.environ)
    env["JAVA_HOME"] = str(Path(java_executable()).parent.parent)
    env["ERP_DB_PATH"] = str(db_path)
    env["ERP_SEED_PATH"] = str(seed_path.resolve())
    env["ERP_SERVICE_TOKEN"] = token

    command = [java_executable(), "-jar", str(jar), f"--server.port={port}"]
    log_handle = log_path.open("wb")
    process = subprocess.Popen(
        command,
        cwd=str(ERP_DIR),
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    service = ErpService(
        base_url=f"http://127.0.0.1:{port}",
        token=token,
        db_path=db_path,
        port=port,
        process=process,
        log_path=log_path,
    )
    try:
        _wait_until_ready(service.base_url, process, log_path)
        yield service
    finally:
        service.stop()
        log_handle.close()
