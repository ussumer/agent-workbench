#!/usr/bin/env python3
"""Run the demo as a *session*: start it, look at it, stop it, reset it.

This is not a test runner and not a second implementation of the stack. ``up`` runs the same
assembly the acceptance rounds run — :func:`tests.live.stack.running_stack` — with two things
changed on purpose, both from the same idea: a session has to be findable afterwards.

* the run directory is ``artifacts/dev/`` rather than a temp directory, so the logs are still
  there when something goes wrong at 3am;
* the MongoDB database is named rather than unique per process, so **restarting keeps the demo's
  data** — which is what "数据默认保留" means, and why ``reset`` is a separate command.

Nothing here holds a credential or prints one. ``doctor`` reports which configuration names are
present, never their values.

Usage::

    python scripts/dev.py doctor
    python scripts/dev.py up
    python scripts/dev.py status
    python scripts/dev.py smoke
    python scripts/dev.py logs
    python scripts/dev.py down
    python scripts/dev.py reset --yes
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(REPO_ROOT / "src"), str(REPO_ROOT / "tests")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

#: Everything a session owns lives under here. ``reset`` removes this directory and nothing else.
DEV_ROOT = REPO_ROOT / "artifacts" / "dev"
SESSION = DEV_ROOT / "session.json"
STOP_FILE = DEV_ROOT / "stop"

#: The database a session uses. Named, so a restart finds the same data; prefixed like the
#: acceptance databases, so ``mongo_service.drop_test_database`` scope still applies and the
#: reset path cannot reach a database this project did not create.
DEFAULT_DATABASE = "dev"

#: How long ``up`` waits for the stack to answer. The Java ERP alone takes most of a minute.
START_TIMEOUT_SECONDS = 300.0
STOP_TIMEOUT_SECONDS = 120.0


# --------------------------------------------------------------------------- helpers


def _session() -> dict[str, Any] | None:
    if not SESSION.is_file():
        return None
    try:
        return json.loads(SESSION.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _alive(pid: int) -> bool:
    """Whether a process id still exists. Windows has no ``os.kill(pid, 0)``."""
    if pid <= 0:
        return False
    if os.name == "nt":
        completed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            check=False,
            text=True,
        )
        return str(pid) in (completed.stdout or "")
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _health(base_url: str, path: str = "/api/demo/users", timeout: float = 5.0) -> bool:
    import httpx

    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=2.0), trust_env=False) as client:
            return client.get(f"{base_url}{path}").status_code < 500
    except httpx.HTTPError:
        return False


def _tail(path: str | Path, lines: int = 25) -> str:
    """The last lines of a log file, indented, or why there are none."""
    resolved = Path(path)
    if not resolved.is_file():
        return f"  （没有 {resolved}）"
    content = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join("  " + line for line in content[-lines:])


def _running_session() -> dict[str, Any] | None:
    """The session, but only if its process is still up."""
    session = _session()
    if session is None:
        return None
    if not _alive(int(session.get("pid") or 0)):
        return None
    return session


# --------------------------------------------------------------------------- commands


def command_doctor(_args: argparse.Namespace) -> int:
    return subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "doctor.py")]).returncode


#: The control service's process id, when this script started it. Kept so ``down`` can stop it
#: too instead of leaving a service nobody is pointing at.
SANDBOX_PID = DEV_ROOT / "sandbox.pid"


def sandbox_start_command() -> str:
    """The documented way to start the control service — taken from the fixture, not restated.

    One definition: the string a user is told to paste and the string the acceptance failure
    prints have to be the same one, or the instructions rot independently of the code.
    """
    from fixtures import sandbox_service

    return sandbox_service.START_COMMAND


def _ensure_sandbox_service() -> tuple[bool, str]:
    """Start the sandbox control service unless it already answers.

    It is part of the demo stack, so the launcher owns it. The alternative is what the first
    version of this command did: the stack failed to start with a Python traceback naming a
    service the user was expected to have known about. Turning "start the demo" into "guess which
    service is missing" is the failure this task exists to remove.
    """
    from fixtures import sandbox_service

    config = sandbox_service.control_settings()
    if sandbox_service.service_health(config):
        return True, "已在运行"

    logs = DEV_ROOT / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / "opensandbox.log"
    env = dict(os.environ)
    env["OPENSANDBOX_INSECURE_SERVER"] = "YES"
    executable = (
        REPO_ROOT
        / ".venv"
        / ("Scripts/opensandbox-server.exe" if os.name == "nt" else "bin/opensandbox-server")
    )
    handle = log_path.open("wb")
    process = subprocess.Popen(
        [str(executable), "--config", "infra/sandbox/sandbox.toml"],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
    )
    SANDBOX_PID.write_text(str(process.pid), encoding="utf-8")

    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if sandbox_service.service_health(config):
            return True, f"由 dev.py 启动（pid {process.pid}）"
        if process.poll() is not None:
            return False, (
                f"沙箱控制服务启动后立刻退出（exit {process.returncode}）；"
                f"见 {log_path.relative_to(REPO_ROOT)}"
            )
        time.sleep(2.0)
    return False, f"沙箱控制服务 90 秒内没有就绪；见 {log_path.relative_to(REPO_ROOT)}"


def command_up(args: argparse.Namespace) -> int:
    existing = _running_session()
    if existing is not None:
        print(f"已经在运行：{existing['base_url']}（pid {existing['pid']}）")
        return 0

    DEV_ROOT.mkdir(parents=True, exist_ok=True)
    STOP_FILE.unlink(missing_ok=True)
    SESSION.unlink(missing_ok=True)

    # Prerequisites before the stack, so a missing service is reported as a missing service
    # rather than as a traceback from four frames deep inside the assembly.
    ok, detail = _ensure_sandbox_service()
    print(f"沙箱控制服务：{detail}")
    if not ok:
        print("先解决它再启动：", file=sys.stderr)
        print(f"  {sandbox_start_command()}", file=sys.stderr)
        return 1

    logs_dir = DEV_ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    serve_log = (logs_dir / "serve.log").open("wb")
    child = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "_serve",
            "--database",
            args.database,
        ],
        cwd=str(REPO_ROOT),
        stdout=serve_log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    print(f"启动中（pid {child.pid}），日志在 {DEV_ROOT.relative_to(REPO_ROOT)}/logs/")

    deadline = time.monotonic() + args.wait
    while time.monotonic() < deadline:
        session = _session()
        if session is not None and _health(str(session.get("base_url") or "")):
            print(f"就绪：{session['base_url']}")
            print(f"  数据库  {session['database']}")
            print(f"  日志    {Path(session['run_dir']).relative_to(REPO_ROOT)}")
            return 0
        if child.poll() is not None:
            # The reason, not just the fact. A start that fails silently is the failure mode
            # this whole command exists to prevent, and the first version of it redirected the
            # child's output to DEVNULL — so it reported "exit 1" and nothing else.
            print(f"启动失败：子进程已退出（exit {child.returncode}）。最后几行：", file=sys.stderr)
            print(_tail(serve_log.name), file=sys.stderr)
            return 1
        time.sleep(2.0)

    print(f"{args.wait:.0f} 秒内没有就绪；子进程仍在跑，可以看日志：", file=sys.stderr)
    print("  python scripts/dev.py logs", file=sys.stderr)
    return 1


def command_serve(args: argparse.Namespace) -> int:
    """The process ``up`` spawns. Hidden: not part of what a user is meant to type."""
    from live.stack import running_stack

    with running_stack(
        run_dir=DEV_ROOT / "run", database_name=args.database, preserve_data=True
    ) as stack:
        SESSION.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "base_url": stack.base_url,
                    "database": str(stack.settings.database),
                    "run_dir": str(DEV_ROOT / "run"),
                    "services": {
                        "erp": stack.erp.base_url,
                        "gateway": stack.gateway.base_url,
                        "site": stack.site.base_url,
                        "app": stack.base_url,
                    },
                    "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        # The stack serves the app itself (``running_stack`` opens the socket the background
        # analyst calls back into), so this process only has to stay inside the block. Waiting
        # on a marker rather than on a signal is not portability theatre: on Windows a stop
        # arrives as a TerminateProcess, which runs no handler, and the *services* started
        # inside this block would be left running with nothing pointing at them.
        while not STOP_FILE.exists():
            time.sleep(1.0)
    return 0


def command_status(_args: argparse.Namespace) -> int:
    session = _running_session()
    if session is None:
        stale = _session()
        if stale is not None:
            print(f"没有在运行（{SESSION.relative_to(REPO_ROOT)} 是上一次会话留下的，pid 已退出）")
        else:
            print("没有在运行。用 `python scripts/dev.py up` 启动。")
        return 1

    print(f"运行中：{session['base_url']}（pid {session['pid']}，{session.get('started_at')}）")
    print(f"  数据库  {session['database']}")
    # Each service answers on its own path; asking all of them for `/health` reported the ERP
    # as unknown when it was fine.
    probes = {
        "erp": "/api/erp/v1/health/ready",
        "gateway": "/health",
        "site": "/health",
        "app": "/api/demo/users",
    }
    for name, url in (session.get("services") or {}).items():
        state = "ok" if _health(str(url), path=probes.get(name, "/health")) else "?"
        print(f"  {name:<8} {url}  {state}")
    return 0


def command_logs(args: argparse.Namespace) -> int:
    session = _session()
    run_dir = Path(session["run_dir"]) if session else DEV_ROOT / "run"
    if not run_dir.is_dir():
        print(f"没有日志目录：{run_dir}", file=sys.stderr)
        return 1
    logs = sorted(run_dir.rglob("*.log"))
    if not logs:
        print(f"{run_dir} 里没有日志")
        return 1
    for path in logs:
        print(f"===== {path.relative_to(REPO_ROOT)}")
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as failure:
            print(f"  (读不到：{failure})")
            continue
        for line in lines[-args.tail :]:
            print("  " + line)
    return 0


def command_smoke(args: argparse.Namespace) -> int:
    """One real read-only question through the running session's HTTP surface.

    A smoke test that only checked a port would pass for a stack whose model is not configured
    or whose agent cannot call a tool. This asks a question and requires the answer to have
    come from a tool call, which is the only part that could not be faked by prose.
    """
    import httpx

    session = _running_session()
    if session is None:
        print("没有在运行；先 `python scripts/dev.py up`。", file=sys.stderr)
        return 1

    base = str(session["base_url"])
    thread = f"devsmoke-{int(time.time())}"
    with httpx.Client(base_url=base, timeout=httpx.Timeout(300.0, connect=10.0)) as client:
        client.post("/api/demo/session", json={"user_id": "demo-a"})
        response = client.post(
            "/api/chat/stream",
            json={
                "thread_id": thread,
                "request_id": f"{thread}-t1",
                "message": "哪些配件库存不足？只做只读查询，不要下单。",
            },
        )
        if response.status_code >= 400:
            print(f"HTTP {response.status_code}: {response.text[:300]}", file=sys.stderr)
            return 1
        # The wire format is parsed by the runner's own reader, not by a second one written
        # here: the first version of this command re-implemented it, got the framing wrong, and
        # reported "no tool calls" for a run that had made them.
        from demo import read_frames, trace_of

        frames = read_frames(response.text)
        tools = [call["tool"] for call in trace_of(frames)]
        statuses = [
            str(((frame.get("envelope") or {}).get("payload") or {}).get("status") or "")
            for frame in frames
            if frame.get("event") == "done"
        ]
        state = client.get(f"/api/chat/{thread}/state").json().get("data") or {}

    print(f"线程 {thread}：{len(frames)} 帧，done={statuses[-1] if statuses else '?'}")
    print(f"  线上可见的调用：{', '.join(str(name) for name in tools if name) or '（没有）'}")
    print(f"  待处理中断：{len(state.get('pending_interrupts') or [])}")

    # What this can and cannot claim. The answer was produced by a run that delegated, which is
    # the only thing the wire shows: a sub-agent's own calls run inside ``task`` and are not on
    # it (see ``api_view/stream_adapter``). So a passing smoke says "the app answers and the
    # agent took the delegation path", not "the ERP was read" — the rounds in
    # ``artifacts/live/`` are where the ERP itself is checked.
    if "task" not in tools:
        print("烟测失败：这一轮没有走委派路径，主 Agent 不该自己答数据问题。", file=sys.stderr)
        return 1
    if statuses and statuses[-1] == "failed":
        print("烟测失败：这一轮以 failed 结束。", file=sys.stderr)
        return 1
    print("烟测通过。")
    return 0


def command_seed(args: argparse.Namespace) -> int:
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "seed_demo.py")] + args.rest
    ).returncode


def command_down(args: argparse.Namespace) -> int:
    session = _running_session()
    if session is None:
        print("没有在运行。")
        STOP_FILE.unlink(missing_ok=True)
        return 0

    pid = int(session["pid"])
    DEV_ROOT.mkdir(parents=True, exist_ok=True)
    STOP_FILE.write_text("stop\n", encoding="utf-8")
    print(f"正在停止 pid {pid}（数据保留；要清数据用 `reset --yes`）…")

    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        if not _alive(pid):
            STOP_FILE.unlink(missing_ok=True)
            print("已停止。")
            _stop_sandbox_service()
            return 0
        time.sleep(2.0)

    print(f"{args.timeout:.0f} 秒内没有干净退出，强制结束。", file=sys.stderr)
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True)
    STOP_FILE.unlink(missing_ok=True)
    _stop_sandbox_service()
    return 1


def _stop_sandbox_service() -> None:
    """Stop the control service if **this script** started it.

    Only then: a service that was already running when ``up`` was called belongs to whoever
    started it, and a demo teardown that killed it would break their session.
    """
    if not SANDBOX_PID.is_file():
        return
    try:
        pid = int(SANDBOX_PID.read_text(encoding="utf-8").strip())
    except ValueError:
        SANDBOX_PID.unlink(missing_ok=True)
        return
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True)
    SANDBOX_PID.unlink(missing_ok=True)
    print(f"  沙箱控制服务已停止（pid {pid}，由本次会话启动）")


def command_reset(args: argparse.Namespace) -> int:
    """Drop the demo's own state. Never anything else.

    Three things are removed, and the list is the whole of it: the session's MongoDB database
    (guarded to names this project created), the run directory, and the stop marker. Sandbox
    containers are **not** touched — they are per owner and owned by the sandbox service, and a
    reset command that reached into them is how "不误删其他容器" stops being true.
    """
    if _running_session() is not None:
        print("还在运行；先 `python scripts/dev.py down`。", file=sys.stderr)
        return 1
    if not args.yes:
        print("reset 会删掉演示数据库与 artifacts/dev/。确认后加 --yes。", file=sys.stderr)
        return 1

    from api_view.web_config import DEFAULT_TEST_DATABASE
    from fixtures import mongo_service

    session = _session() or {}
    label = str(session.get("database") or "").removeprefix(DEFAULT_TEST_DATABASE).strip("_")
    settings = mongo_service.unique_settings(label or DEFAULT_DATABASE)
    print(f"删除数据库 {settings.database}")

    from pymongo import MongoClient

    with MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000) as client:
        client.drop_database(settings.database)

    if DEV_ROOT.is_dir():
        shutil.rmtree(DEV_ROOT, ignore_errors=True)
    STOP_FILE.unlink(missing_ok=True)
    print(f"已重置：{settings.database}、artifacts/dev/。沙箱容器未动。")
    return 0


# --------------------------------------------------------------------------- entry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="环境诊断（只报配置名，不报值）").set_defaults(func=command_doctor)

    up = sub.add_parser("up", help="启动演示栈（数据保留）")
    up.add_argument("--database", default=DEFAULT_DATABASE, help="会话数据库后缀（默认 dev）")
    up.add_argument("--wait", type=float, default=START_TIMEOUT_SECONDS)
    up.set_defaults(func=command_up)

    serve = sub.add_parser("_serve", help=argparse.SUPPRESS)
    serve.add_argument("--database", default=DEFAULT_DATABASE)
    serve.set_defaults(func=command_serve)

    sub.add_parser("status", help="当前会话与各服务的地址").set_defaults(func=command_status)

    logs = sub.add_parser("logs", help="看会话日志")
    logs.add_argument("--tail", type=int, default=40)
    logs.set_defaults(func=command_logs)

    sub.add_parser("smoke", help="通过 API 问一个只读问题并检查工具调用").set_defaults(func=command_smoke)

    seed = sub.add_parser("seed", help="准备演示数据（见 scripts/seed_demo.py）")
    seed.add_argument("rest", nargs="*")
    seed.set_defaults(func=command_seed)

    down = sub.add_parser("down", help="停止会话（保留数据）")
    down.add_argument("--timeout", type=float, default=STOP_TIMEOUT_SECONDS)
    down.set_defaults(func=command_down)

    reset = sub.add_parser("reset", help="删除演示数据库与 artifacts/dev/（不动沙箱容器）")
    reset.add_argument("--yes", action="store_true")
    reset.set_defaults(func=command_reset)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
