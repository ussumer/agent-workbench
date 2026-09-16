#!/usr/bin/env python3
"""Task acceptance gate.

Runs the ``required`` checks declared in ``docs/plan/tasks.json`` and writes a
verifiable receipt plus raw evidence under ``artifacts/tasks/<TASK>/<attempt>/``.

Design rules taken from docs/plan/execution.md:

* Commands are executed as argument arrays (never through a shell), with a cwd
  and a timeout taken from the manifest.
* A test check is only ``passed`` when its JUnit report exists, collected more
  than the manifest minimum, and reported zero failures and zero skips. Zero
  collected tests is a failure, not a pass.
* Missing credentials/services raise ``blocked`` (process exit 2); a genuine
  failure raises ``failed`` (exit 1). There is no path that converts an error
  into success.
* This runner never edits ``state.json``; the coding agent registers completion
  after validating the receipt.

Example::

    python scripts/gate.py T00
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import config  # noqa: E402

SCHEMA_VERSION = 1
STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_BLOCKED = "blocked"

EXIT_PASSED = 0
EXIT_FAILED = 1
EXIT_BLOCKED = 2

DEFAULT_TIMEOUT_SECONDS = 900
BLOCKED_MARKER = "GATE_BLOCKED:"

CAPABILITY_MAP_FILE = "gate_capabilities.json"

# Source trees hashed for the fallback (non-git) source revision record.
SOURCE_TREES = ("src", "scripts", "tests", "erp/src", "frontend/src", "fixtures")
SOURCE_FILE_SUFFIXES = (".py", ".java", ".ts", ".vue", ".json", ".yaml", ".yml", ".xml", ".sql")


class GateError(RuntimeError):
    """Raised for gate configuration problems (bad task id, missing manifest)."""


@dataclass
class CheckOutcome:
    """Normalised result of one manifest check."""

    id: str
    mode: str
    argv: list[str]
    cwd: str
    status: str
    exit_code: int | None
    started_at: str
    finished_at: str
    duration_seconds: float
    log: str | None = None
    log_sha256: str | None = None
    report: str | None = None
    tests_total: int | None = None
    tests_failed: int | None = None
    tests_skipped: int | None = None
    reason: str | None = None
    executed_argv: list[str] = field(default_factory=list)

    def to_receipt(self) -> dict:
        data: dict = {
            "id": self.id,
            "mode": self.mode,
            "argv": self.argv,
            "cwd": self.cwd,
            "status": self.status,
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": round(self.duration_seconds, 3),
        }
        if self.log:
            data["log"] = self.log
            data["log_sha256"] = self.log_sha256
        if self.executed_argv and self.executed_argv != self.argv:
            data["executed_argv"] = self.executed_argv
        if self.report:
            data["report"] = self.report
        if self.tests_total is not None:
            data["tests_total"] = self.tests_total
            data["tests_failed"] = self.tests_failed
            data["tests_skipped"] = self.tests_skipped
        if self.reason:
            data["reason"] = self.reason
        return data


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest(root: Path) -> dict:
    path = root / "docs/plan/tasks.json"
    if not path.is_file():
        raise GateError(f"manifest not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def find_task(manifest: dict, task_id: str) -> dict:
    for task in manifest["tasks"]:
        if task["id"] == task_id:
            return task
    raise GateError(f"unknown task id: {task_id}")


def load_capability_map(root: Path) -> dict[str, list[str]]:
    """Optional task -> required-capability mapping used for the blocked preflight."""
    path = root / "scripts" / CAPABILITY_MAP_FILE
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: list(v) for k, v in data.get("tasks", {}).items()}


def required_capabilities(task: dict, capability_map: dict[str, list[str]]) -> list[str]:
    """Capabilities that must be configured before a task's checks can run.

    ``live`` checks always need the real model. Service-specific needs are
    declared in ``gate_capabilities.json`` so the manifest stays untouched.
    """
    needed: list[str] = []
    if any(spec.get("mode") == "live" for spec in task["checks"]):
        needed.append("model")
    for capability in capability_map.get(task["id"], []):
        if capability not in needed:
            needed.append(capability)
    return needed


def new_attempt_dir(root: Path, task_id: str) -> Path:
    """Create a fresh attempt directory, never overwriting an earlier attempt."""
    base = root / "artifacts" / "tasks" / task_id
    base.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = base / stamp
    suffix = 2
    while candidate.exists():
        candidate = base / f"{stamp}-{suffix}"
        suffix += 1
    candidate.mkdir(parents=True)
    return candidate


def materialise(spec: dict, evidence_dir_posix: str) -> list[str]:
    """Substitute ``{evidence_dir}`` with a POSIX path relative to the repo root."""
    return [arg.replace("{evidence_dir}", evidence_dir_posix) for arg in spec["argv"]]


def resolve_program(program: str, cwd: Path, env: dict[str, str] | None = None) -> str:
    """Resolve a program for the current OS.

    Two cases matter:

    * The manifest is written for a POSIX shell (``./mvnw``). Windows cannot
      execute that script directly, so an explicit platform shim maps it to the
      wrapper's official ``.cmd`` entry point.
    * Bare names such as ``uv`` and ``npm`` must be looked up on the *gate's*
      resolved PATH (which the toolchain detection extended), not on whatever
      PATH the parent process happens to export. ``subprocess`` would otherwise
      inherit the parent search path even when a custom environment is passed.

    The manifest argv is what gets recorded in the receipt; the resolved form is
    recorded separately and appears in the log header.
    """
    if program.startswith("./") or program.startswith(".\\"):
        relative = program[2:]
        candidate = cwd / relative
        if os.name == "nt":
            # The POSIX wrapper script cannot be executed here, so its official
            # Windows entry point takes precedence when both exist.
            for ext in (".cmd", ".bat", ".exe"):
                if (cwd / f"{relative}{ext}").is_file():
                    return str(cwd / f"{relative}{ext}")
        return str(candidate)

    resolved = config.resolve_tool(program, env or {})
    return resolved or program


# --------------------------------------------------------------------------- #
# execution
# --------------------------------------------------------------------------- #


def parse_junit(path: Path) -> tuple[int, int, int]:
    """Return ``(total, failed, skipped)`` counted exactly as the plan checker does."""
    root = ET.parse(path).getroot()
    cases = list(root.iter("testcase"))
    failed = sum(
        1 for case in cases if case.find("failure") is not None or case.find("error") is not None
    )
    skipped = sum(1 for case in cases if case.find("skipped") is not None)
    return len(cases), failed, skipped


def run_process(
    argv: list[str],
    executed_argv: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout: float,
) -> tuple[int | None, str, bool]:
    """Run one command and return ``(exit_code, combined_output, timed_out)``."""
    header = f"$ {' '.join(executed_argv)}\n[cwd] {cwd}\n[manifest argv] {' '.join(argv)}\n\n"
    try:
        completed = subprocess.run(
            executed_argv,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        partial = _decode(exc.stdout) + _decode(exc.stderr)
        return None, header + partial + f"\n[gate] timed out after {timeout:.0f}s\n", True
    except FileNotFoundError as exc:
        return None, header + f"\n[gate] command not found: {exc}\n", False
    except OSError as exc:
        return None, header + f"\n[gate] could not execute command: {exc}\n", False
    output = header + _decode(completed.stdout) + _decode(completed.stderr)
    return completed.returncode, output, False


def _decode(raw: bytes | None) -> str:
    if not raw:
        return ""
    return raw.decode("utf-8", errors="replace")


def run_check(
    spec: dict,
    repo_root: Path,
    evidence_dir: Path,
    evidence_dir_posix: str,
    env: dict[str, str],
    secrets: set[str],
    timeout: float,
) -> CheckOutcome:
    """Execute a single manifest check and normalise the outcome."""
    check_id = spec["id"]
    argv = materialise(spec, evidence_dir_posix)
    cwd = (repo_root / spec["cwd"]).resolve()
    report_rel = (
        spec["report"].replace("{evidence_dir}", evidence_dir_posix)
        if spec.get("report")
        else None
    )
    started = utc_now()
    started_monotonic = time.monotonic()

    outcome = CheckOutcome(
        id=check_id,
        mode=spec["mode"],
        argv=argv,
        cwd=spec["cwd"],
        status=STATUS_FAILED,
        exit_code=None,
        started_at=started,
        finished_at=started,
        duration_seconds=0.0,
        report=report_rel,
    )

    if not cwd.is_dir():
        outcome.reason = f"working directory does not exist: {spec['cwd']}"
        outcome.finished_at = utc_now()
        _write_log(outcome, evidence_dir, repo_root, secrets, extra=outcome.reason)
        return outcome

    executed = list(argv)
    executed[0] = resolve_program(argv[0], cwd, env)
    outcome.executed_argv = executed

    exit_code, output, timed_out = run_process(argv, executed, cwd, env, timeout)
    outcome.exit_code = exit_code
    outcome.duration_seconds = time.monotonic() - started_monotonic

    if timed_out:
        outcome.status = STATUS_FAILED
        outcome.reason = f"timeout after {timeout:.0f}s"
        outcome.finished_at = utc_now()
        _write_log(outcome, evidence_dir, repo_root, secrets, extra=output)
        return outcome

    if exit_code is None:
        outcome.status = STATUS_FAILED
        outcome.reason = "command could not be started"
        outcome.finished_at = utc_now()
        _write_log(outcome, evidence_dir, repo_root, secrets, extra=output)
        return outcome

    if spec["kind"] == "test":
        status, reason, counts = _evaluate_test(
            exit_code, output, repo_root, report_rel, spec.get("min_tests", 1)
        )
        outcome.status = status
        outcome.reason = reason
        if counts is not None:
            outcome.tests_total, outcome.tests_failed, outcome.tests_skipped = counts
    elif BLOCKED_MARKER in output or exit_code == EXIT_BLOCKED:
        outcome.status = STATUS_BLOCKED
        outcome.reason = _blocked_reason(output) or "check reported blocked"
    elif exit_code == 0:
        outcome.status = STATUS_PASSED
    else:
        outcome.status = STATUS_FAILED
        outcome.reason = f"exit code {exit_code}"

    outcome.finished_at = utc_now()
    _write_log(outcome, evidence_dir, repo_root, secrets, extra=output)
    return outcome


def _blocked_reason(output: str) -> str | None:
    for line in output.splitlines():
        if line.strip().startswith(BLOCKED_MARKER):
            return line.strip()[len(BLOCKED_MARKER) :].strip() or "blocked"
    return None


def _evaluate_test(
    exit_code: int,
    output: str,
    repo_root: Path,
    report_rel: str | None,
    min_tests: int,
) -> tuple[str, str, tuple[int, int, int] | None]:
    """Decide a test check's status. Zero collection is never a pass."""
    blocked = _blocked_reason(output)
    if report_rel is None:
        return STATUS_FAILED, "manifest test check has no report path", None

    report_path = repo_root / report_rel
    if not report_path.is_file():
        if blocked:
            return STATUS_BLOCKED, blocked, None
        return STATUS_FAILED, f"missing JUnit report: {report_rel}", None
    try:
        total, failed, skipped = parse_junit(report_path)
    except ET.ParseError as exc:
        return STATUS_FAILED, f"unreadable JUnit report: {exc}", None

    counts = (total, failed, skipped)
    if total == 0:
        return STATUS_FAILED, "zero tests collected", counts
    if total < min_tests:
        return STATUS_FAILED, f"collected {total} tests, manifest requires {min_tests}", counts
    if failed:
        return STATUS_FAILED, f"{failed} test(s) failed", counts
    if skipped:
        return STATUS_FAILED, f"{skipped} test(s) skipped; skips are not a pass", counts
    if exit_code != 0:
        return STATUS_FAILED, f"exit code {exit_code} despite a clean report", counts
    return STATUS_PASSED, "all tests passed", counts


def _write_log(
    outcome: CheckOutcome,
    evidence_dir: Path,
    repo_root: Path,
    secrets: set[str],
    extra: str,
) -> None:
    """Write the redacted log for a check and record its repo-relative path + hash."""
    body = extra or ""
    if outcome.reason and outcome.reason not in body:
        body = f"{body}\n[gate] status={outcome.status} reason={outcome.reason}\n"
    redacted = config.redact(body, secrets)
    log_path = evidence_dir / f"{outcome.id}.log"
    log_path.write_text(redacted, encoding="utf-8")
    outcome.log = log_path.relative_to(repo_root).as_posix()
    outcome.log_sha256 = sha256_file(log_path)


# --------------------------------------------------------------------------- #
# source revision
# --------------------------------------------------------------------------- #


def source_revision(root: Path) -> dict:
    """Describe the exact source state a receipt was produced from."""
    revision: dict = {}
    head = _git(root, "rev-parse", "HEAD")
    if head:
        revision["vcs"] = "git"
        revision["head"] = head
        status_out = _git(root, "status", "--porcelain") or ""
        lines = [line for line in status_out.splitlines() if line.strip()]
        revision["dirty_files"] = len(lines)
        revision["worktree_digest"] = hashlib.sha256(status_out.encode("utf-8")).hexdigest()
    else:
        revision["vcs"] = "none"
    revision["source_manifest_digest"] = _source_manifest_digest(root)
    return revision


def _git(root: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _source_manifest_digest(root: Path) -> str:
    entries: list[str] = []
    for tree in SOURCE_TREES:
        base = root / tree
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in SOURCE_FILE_SUFFIXES:
                continue
            rel = path.relative_to(root).as_posix()
            entries.append(f"{rel}:{sha256_file(path)}")
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# task execution
# --------------------------------------------------------------------------- #


def run_task(
    task_id: str,
    repo_root: Path,
    env: dict[str, str],
    capability_map: dict[str, list[str]],
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    extra_capabilities: list[str] | None = None,
) -> tuple[dict, Path]:
    """Run every check of a task and build its receipt."""
    manifest = load_manifest(repo_root)
    task = find_task(manifest, task_id)
    secrets = config.secret_values(env)
    evidence_dir = new_attempt_dir(repo_root, task_id)
    evidence_dir_posix = evidence_dir.relative_to(repo_root).as_posix()

    started = utc_now()
    outcomes: list[CheckOutcome] = []

    needed = required_capabilities(task, capability_map)
    for capability in extra_capabilities or []:
        if capability not in needed:
            needed.append(capability)
    missing = config.missing_config(env, needed)
    if missing:
        for spec in task["checks"]:
            outcome = CheckOutcome(
                id=spec["id"],
                mode=spec["mode"],
                argv=materialise(spec, evidence_dir_posix),
                cwd=spec["cwd"],
                status=STATUS_BLOCKED,
                exit_code=None,
                started_at=started,
                finished_at=started,
                duration_seconds=0.0,
                reason="missing configuration: " + "; ".join(missing),
                report=(
                    spec["report"].replace("{evidence_dir}", evidence_dir_posix)
                    if spec.get("report")
                    else None
                ),
            )
            _write_log(outcome, evidence_dir, repo_root, secrets, extra="")
            outcomes.append(outcome)
    else:
        for spec in task["checks"]:
            outcomes.append(
                run_check(
                    spec,
                    repo_root,
                    evidence_dir,
                    evidence_dir_posix,
                    env,
                    secrets,
                    timeout,
                )
            )

    status = _aggregate_status(task, outcomes)
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "task_id": task_id,
        "title": task["title"],
        "status": status,
        "started_at": started,
        "finished_at": utc_now(),
        "source_revision": source_revision(repo_root),
        "capabilities_required": needed,
        "checks": [outcome.to_receipt() for outcome in outcomes],
        "artifacts": [],
    }
    receipt["artifacts"] = _collect_artifacts(repo_root, evidence_dir, receipt)
    _write_receipt(evidence_dir, receipt)
    return receipt, evidence_dir / "receipt.json"


def _aggregate_status(task: dict, outcomes: list[CheckOutcome]) -> str:
    """All required checks must pass; otherwise blocked outranks failed."""
    required = {spec["id"] for spec in task["checks"] if spec.get("required")}
    relevant = [o for o in outcomes if o.id in required]
    if any(o.status == STATUS_FAILED for o in relevant):
        return STATUS_FAILED
    if any(o.status == STATUS_BLOCKED for o in relevant):
        return STATUS_BLOCKED
    if all(o.status == STATUS_PASSED for o in relevant) and relevant:
        return STATUS_PASSED
    return STATUS_FAILED


def _collect_artifacts(repo_root: Path, evidence_dir: Path, receipt: dict) -> list[dict]:
    """Hash every evidence file, ensuring each JUnit report is included."""
    candidates: set[Path] = set()
    for outcome in receipt["checks"]:
        if outcome.get("log"):
            candidates.add(repo_root / outcome["log"])
        if outcome.get("report"):
            candidates.add(repo_root / outcome["report"])
    for path in evidence_dir.iterdir():
        if path.is_file():
            candidates.add(path)

    artifacts: list[dict] = []
    for path in sorted(candidates):
        if not path.is_file():
            continue
        if path.name == "receipt.json":
            continue
        artifacts.append(
            {
                "path": path.relative_to(repo_root).as_posix(),
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
        )
    return artifacts


def _write_receipt(evidence_dir: Path, receipt: dict) -> None:
    (evidence_dir / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def summarise(receipt: dict) -> str:
    lines = [f"task {receipt['task_id']}: {receipt['status'].upper()}"]
    for check in receipt["checks"]:
        detail = f"  - {check['id']} [{check['mode']}] {check['status']}"
        if check.get("exit_code") is not None:
            detail += f" exit={check['exit_code']}"
        if check.get("tests_total") is not None:
            detail += (
                f" tests={check['tests_total']}"
                f" failed={check['tests_failed']} skipped={check['tests_skipped']}"
            )
        if check.get("reason"):
            detail += f" :: {check['reason']}"
        lines.append(detail)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", nargs="?", help="task id, for example T00")
    parser.add_argument("--all", action="store_true", help="run every task (T22 semantics)")
    parser.add_argument("--live", action="store_true", help="require live capabilities (T23 semantics)")
    parser.add_argument("--root", default=str(config.ROOT), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    env = config.build_env(config.load_dotenv())
    extra_capabilities = ["model"] if args.live else []
    capability_map = load_capability_map(root)

    try:
        manifest = load_manifest(root)
    except GateError as exc:
        print(f"gate error: {exc}", file=sys.stderr)
        return EXIT_FAILED

    targets = [task["id"] for task in manifest["tasks"]] if args.all else [args.task]
    if any(target is None for target in targets):
        parser.error("provide a task id, for example: python scripts/gate.py T00")

    saw_failed = False
    saw_blocked = False
    for task_id in targets:
        try:
            receipt, receipt_path = run_task(
                task_id,
                root,
                env,
                capability_map,
                extra_capabilities=extra_capabilities,
            )
        except GateError as exc:
            print(f"gate error: {exc}", file=sys.stderr)
            return EXIT_FAILED
        print(summarise(receipt))
        print(f"  receipt: {receipt_path.relative_to(root).as_posix()}")
        saw_failed |= receipt["status"] == STATUS_FAILED
        saw_blocked |= receipt["status"] == STATUS_BLOCKED

    # A real failure always outranks a blocked prerequisite.
    if saw_failed:
        return EXIT_FAILED
    if saw_blocked:
        return EXIT_BLOCKED
    return EXIT_PASSED


if __name__ == "__main__":
    raise SystemExit(main())
