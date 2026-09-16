"""T00 acceptance: the gate must refuse fake success.

These tests drive the gate's own functions against a throwaway mini-repository in
a temporary directory. They deliberately never invoke ``gate.py T00`` against
this repository: doing so would recurse, because this file *is* T00's check.
The outer ``scripts/gate.py T00`` run is the only place the real gate executes.

Covered behaviours (required by docs/plan/tasks/T00.md):
success, failure, zero collected tests, skipped tests, missing command, timeout,
missing configuration, log redaction, plus receipt/evidence integrity.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import gate  # noqa: E402
from lib import config  # noqa: E402

pytestmark = pytest.mark.unit

# Capabilities the plan depends on, pinned by docs/runtime/versions.md. The
# Mongo-backed Store in particular is easy to get wrong: it lives in
# `langgraph-store-mongodb`, not in `langchain-mongodb`.
FRAMEWORK_IMPORTS: tuple[tuple[str, str], ...] = (
    ("deepagents", "create_deep_agent"),
    ("deepagents", "AsyncSubAgent"),
    ("deepagents.backends", "CompositeBackend"),
    ("deepagents.backends", "StoreBackend"),
    ("langgraph.graph", "StateGraph"),
    ("langgraph.types", "Command"),
    ("langgraph.types", "interrupt"),
    ("langgraph.stream", "ProtocolEvent"),
    ("langgraph.checkpoint.mongodb", "MongoDBSaver"),
    ("langgraph.store.mongodb", "MongoDBStore"),
    ("langgraph.store.memory", "InMemoryStore"),
    ("mcp.server.fastmcp", "FastMCP"),
    ("langchain_mcp_adapters.client", "MultiServerMCPClient"),
    ("opensandbox", "Sandbox"),
    ("fastapi", "FastAPI"),
)

# A minimal, deterministic subprocess environment: no model or service secrets,
# so the "missing configuration" case cannot be satisfied by the developer's own
# machine settings.
_BASE_ENV_KEYS = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "LOCALAPPDATA",
    "APPDATA",
    "PROGRAMFILES",
    "PROGRAMDATA",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "OS",
    "LANG",
)


def base_env(**extra: str) -> dict[str, str]:
    env = {key: os.environ[key] for key in _BASE_ENV_KEYS if key in os.environ}
    env.update(extra)
    return env


def make_repo(tmp_path: Path, checks: list[dict], task_id: str = "T90") -> Path:
    """Create a throwaway repository containing only what the gate needs."""
    root = tmp_path / "mini-repo"
    (root / "docs" / "plan").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "plan_version": "1.0",
        "tasks": [
            {
                "id": task_id,
                "title": "gate self-check",
                "file": "docs/plan/tasks/T90.md",
                "depends_on": [],
                "requirements": ["R01"],
                "reads": [],
                "checks": checks,
            }
        ],
    }
    (root / "docs" / "plan" / "tasks.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    return root


def acceptance_check(
    check_id: str,
    argv: list[str],
    min_tests: int = 1,
    mode: str = "unit",
) -> dict:
    return {
        "id": check_id,
        "mode": mode,
        "kind": "test",
        "required": True,
        "min_tests": min_tests,
        "argv": argv,
        "cwd": ".",
        "report": "{evidence_dir}/acceptance.xml",
    }


def command_check(check_id: str, argv: list[str], mode: str = "unit") -> dict:
    return {
        "id": check_id,
        "mode": mode,
        "kind": "command",
        "required": True,
        "argv": argv,
        "cwd": ".",
    }


def pytest_check(repo: Path, test_file: str, min_tests: int = 1, mode: str = "unit") -> dict:
    """A manifest check that runs pytest inside the throwaway repository.

    ``--basetemp`` keeps pytest from creating its ``*-current`` symlink beside a
    numbered temp directory; this host refuses to traverse that reparse point, and
    the generated sample tests have no need for a shared base temp.
    """
    return acceptance_check(
        "acceptance",
        [
            sys.executable,
            "-m",
            "pytest",
            test_file,
            "-q",
            "-p",
            "no:cacheprovider",
            "--basetemp=.pytest-basetemp",
            "--junitxml={evidence_dir}/acceptance.xml",
        ],
        min_tests=min_tests,
        mode=mode,
    )


def write_test_file(repo: Path, name: str, body: str) -> None:
    path = repo / "tests" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def run(repo: Path, env: dict[str, str], timeout: float = 120.0) -> dict:
    receipt, _ = gate.run_task("T90", repo, env, {}, timeout=timeout)
    return receipt


# --------------------------------------------------------------------------- #
# argv / platform plumbing
# --------------------------------------------------------------------------- #


def test_materialise_replaces_evidence_dir_with_posix_path():
    spec = {"argv": ["tool", "--out={evidence_dir}/x.xml"], "report": "{evidence_dir}/x.xml"}
    argv = gate.materialise(spec, "artifacts/tasks/T00/20260101T000000Z")
    assert argv == ["tool", "--out=artifacts/tasks/T00/20260101T000000Z/x.xml"]


def test_parse_junit_counts_total_failed_and_skipped(tmp_path: Path):
    report = tmp_path / "r.xml"
    report.write_text(
        '<testsuites><testsuite name="s">'
        '<testcase name="ok"/>'
        '<testcase name="bad"><failure>boom</failure></testcase>'
        '<testcase name="err"><error>boom</error></testcase>'
        '<testcase name="skip"><skipped/></testcase>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    assert gate.parse_junit(report) == (4, 2, 1)


def test_resolve_program_resolves_relative_manifest_command(tmp_path: Path):
    """`./mvnw` from the manifest must resolve to a real file in the working dir.

    On Windows the POSIX wrapper script is not executable, so the shim maps it to
    the wrapper's official ``.cmd`` entry point. This test never skips: a skipped
    case would itself violate the gate's "no skips" rule.
    """
    wrapper = tmp_path / "mvnw"
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    if os.name == "nt":
        (tmp_path / "mvnw.cmd").write_text("@echo off\n", encoding="utf-8")

    resolved = gate.resolve_program("./mvnw", tmp_path)
    if os.name == "nt":
        assert resolved.endswith("mvnw.cmd")
    else:
        assert resolved == str(wrapper)


# --------------------------------------------------------------------------- #
# positive path
# --------------------------------------------------------------------------- #


def test_resolve_program_searches_the_gate_environment_path(tmp_path: Path):
    """Bare manifest commands must resolve on the gate's PATH, not the parent's.

    ``subprocess`` searches the *parent* PATH even when a custom environment is
    passed, so the gate has to resolve bare names itself or `uv`/`npm` fail on a
    machine where the toolchain is not on the inherited PATH.
    """
    bindir = tmp_path / "tools"
    bindir.mkdir()
    name = "rush-tool.cmd" if os.name == "nt" else "rush-tool"
    tool = bindir / name
    tool.write_text("@echo off\n" if os.name == "nt" else "#!/bin/sh\n", encoding="utf-8")
    if os.name != "nt":
        tool.chmod(0o755)

    resolved = gate.resolve_program("rush-tool", tmp_path, {"PATH": str(bindir)})
    assert Path(resolved) == tool


def test_passing_test_check_produces_passed_receipt(tmp_path: Path):
    repo = make_repo(tmp_path, [pytest_check(tmp_path, "tests/test_ok.py", min_tests=2)])
    write_test_file(
        repo,
        "test_ok.py",
        "def test_one():\n    assert 1 + 1 == 2\n\n\ndef test_two():\n    assert 'a' in 'abc'\n",
    )
    receipt = run(repo, base_env())
    assert receipt["status"] == gate.STATUS_PASSED
    check = receipt["checks"][0]
    assert check["exit_code"] == 0
    assert (check["tests_total"], check["tests_failed"], check["tests_skipped"]) == (2, 0, 0)


def test_passing_command_check_produces_passed_receipt(tmp_path: Path):
    repo = make_repo(
        tmp_path,
        [command_check("noop", [sys.executable, "-c", "print('fine')"])],
    )
    receipt = run(repo, base_env())
    assert receipt["status"] == gate.STATUS_PASSED
    assert receipt["checks"][0]["exit_code"] == 0


# --------------------------------------------------------------------------- #
# negative paths: none of these may ever be reported as passed
# --------------------------------------------------------------------------- #


def test_failing_test_produces_failed_receipt(tmp_path: Path):
    repo = make_repo(tmp_path, [pytest_check(tmp_path, "tests/test_bad.py")])
    write_test_file(repo, "test_bad.py", "def test_bad():\n    assert False, 'expected failure'\n")
    receipt = run(repo, base_env())
    assert receipt["status"] == gate.STATUS_FAILED
    assert receipt["checks"][0]["tests_failed"] == 1


def test_zero_collected_tests_is_failure_not_pass(tmp_path: Path):
    repo = make_repo(tmp_path, [pytest_check(tmp_path, "tests/test_empty.py")])
    write_test_file(repo, "test_empty.py", "VALUE = 1  # no test functions here\n")
    receipt = run(repo, base_env())
    assert receipt["status"] == gate.STATUS_FAILED
    assert "zero tests collected" in receipt["checks"][0]["reason"]


def test_skipped_test_is_failure_not_pass(tmp_path: Path):
    repo = make_repo(tmp_path, [pytest_check(tmp_path, "tests/test_skipped.py")])
    write_test_file(
        repo,
        "test_skipped.py",
        "import pytest\n\n\n@pytest.mark.skip(reason='not runnable here')\n"
        "def test_skipped():\n    assert True\n",
    )
    receipt = run(repo, base_env())
    assert receipt["status"] == gate.STATUS_FAILED
    assert receipt["checks"][0]["tests_skipped"] == 1


def test_min_tests_floor_is_enforced(tmp_path: Path):
    repo = make_repo(tmp_path, [pytest_check(tmp_path, "tests/test_one.py", min_tests=5)])
    write_test_file(repo, "test_one.py", "def test_only_one():\n    assert True\n")
    receipt = run(repo, base_env())
    assert receipt["status"] == gate.STATUS_FAILED
    assert receipt["checks"][0]["tests_total"] == 1
    assert "requires 5" in receipt["checks"][0]["reason"]


def test_missing_test_file_is_failure(tmp_path: Path):
    """Pointing pytest at a file that does not exist must never pass."""
    repo = make_repo(tmp_path, [pytest_check(tmp_path, "tests/test_absent.py")])
    receipt = run(repo, base_env())
    assert receipt["status"] == gate.STATUS_FAILED
    reason = receipt["checks"][0]["reason"]
    assert "zero tests collected" in reason or "missing JUnit report" in reason


def test_declared_report_that_is_never_written_is_failure(tmp_path: Path):
    """The manifest's declared report path is authoritative, not whatever pytest wrote."""
    repo = make_repo(
        tmp_path,
        [
            acceptance_check(
                "acceptance",
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "tests/test_report.py",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    "--basetemp=.pytest-basetemp",
                    "--junitxml={evidence_dir}/actual.xml",
                ],
            )
        ],
    )
    write_test_file(repo, "test_report.py", "def test_ok():\n    assert True\n")
    receipt = run(repo, base_env())
    assert receipt["status"] == gate.STATUS_FAILED
    assert "missing JUnit report" in receipt["checks"][0]["reason"]


def test_missing_command_is_failure(tmp_path: Path):
    repo = make_repo(
        tmp_path,
        [command_check("ghost", ["rush-harness-definitely-not-a-real-binary"])],
    )
    receipt = run(repo, base_env())
    assert receipt["status"] == gate.STATUS_FAILED
    assert receipt["checks"][0]["reason"] == "command could not be started"


def test_timeout_is_failure(tmp_path: Path):
    repo = make_repo(
        tmp_path,
        [command_check("slow", [sys.executable, "-c", "import time; time.sleep(30)"])],
    )
    receipt = run(repo, base_env(), timeout=2.0)
    assert receipt["status"] == gate.STATUS_FAILED
    assert "timeout" in receipt["checks"][0]["reason"]


def test_nonzero_command_exit_is_failure(tmp_path: Path):
    repo = make_repo(
        tmp_path,
        [command_check("bad-exit", [sys.executable, "-c", "import sys; sys.exit(3)"])],
    )
    receipt = run(repo, base_env())
    assert receipt["status"] == gate.STATUS_FAILED
    assert receipt["checks"][0]["exit_code"] == 3


# --------------------------------------------------------------------------- #
# blocked path
# --------------------------------------------------------------------------- #


def test_missing_configuration_blocks_live_check(tmp_path: Path):
    repo = make_repo(
        tmp_path,
        [pytest_check(tmp_path, "tests/test_live.py", mode="live")],
    )
    write_test_file(repo, "test_live.py", "def test_live():\n    assert True\n")
    receipt = run(repo, base_env())
    assert receipt["status"] == gate.STATUS_BLOCKED
    assert receipt["capabilities_required"] == ["model"]
    assert "MODEL_API_KEY" in receipt["checks"][0]["reason"]


def test_live_check_runs_when_model_config_is_present(tmp_path: Path):
    """The blocked preflight must not fire when the capability is configured."""
    repo = make_repo(tmp_path, [pytest_check(tmp_path, "tests/test_live_ok.py", mode="live")])
    write_test_file(repo, "test_live_ok.py", "def test_live_ok():\n    assert True\n")
    env = base_env(
        MODEL_API_KEY="unit-test-key",
        MODEL_BASE_URL="http://127.0.0.1:1/v1",
        MODEL_ID="unit-test-model",
    )
    receipt = run(repo, env)
    assert receipt["status"] == gate.STATUS_PASSED


def test_blocked_marker_from_command_is_honoured(tmp_path: Path):
    repo = make_repo(
        tmp_path,
        [
            command_check(
                "service",
                [sys.executable, "-c", "print('GATE_BLOCKED: mongodb unreachable')"],
                mode="integration",
            )
        ],
    )
    receipt = run(repo, base_env())
    assert receipt["status"] == gate.STATUS_BLOCKED
    assert "mongodb unreachable" in receipt["checks"][0]["reason"]


# --------------------------------------------------------------------------- #
# evidence integrity
# --------------------------------------------------------------------------- #


def test_logs_are_redacted_before_they_reach_disk(tmp_path: Path):
    secret = "unit-secret-value-abcdef123456"
    repo = make_repo(
        tmp_path,
        [
            command_check(
                "echo-secret",
                [sys.executable, "-c", "import os; print('key=' + os.environ['DEMO_API_KEY'])"],
            )
        ],
    )
    env = base_env(DEMO_API_KEY=secret)
    receipt = run(repo, env)

    log_path = repo / receipt["checks"][0]["log"]
    text = log_path.read_text(encoding="utf-8")
    assert secret not in text
    assert config.REDACTION_MARKER in text
    assert secret not in json.dumps(receipt, ensure_ascii=False)


def test_receipt_hashes_every_declared_evidence_file(tmp_path: Path):
    repo = make_repo(tmp_path, [pytest_check(tmp_path, "tests/test_ok2.py", min_tests=1)])
    write_test_file(repo, "test_ok2.py", "def test_ok():\n    assert True\n")
    receipt = run(repo, base_env())

    report = receipt["checks"][0]["report"]
    hashed = {entry["path"]: entry["sha256"] for entry in receipt["artifacts"]}
    assert report in hashed, "the JUnit report must be covered by a hash"
    assert receipt["checks"][0]["log"] in hashed
    assert receipt["source_revision"]["source_manifest_digest"]


def test_second_run_creates_a_new_attempt_without_touching_the_first(tmp_path: Path):
    repo = make_repo(tmp_path, [pytest_check(tmp_path, "tests/test_attempts.py")])
    write_test_file(repo, "test_attempts.py", "def test_ok():\n    assert True\n")
    first = run(repo, base_env())
    second = run(repo, base_env())

    first_dir = (repo / first["checks"][0]["log"]).parent
    second_dir = (repo / second["checks"][0]["log"]).parent
    assert first_dir != second_dir
    assert (first_dir / "receipt.json").is_file()
    assert (second_dir / "receipt.json").is_file()


def test_gate_never_rewrites_plan_state(tmp_path: Path):
    repo = make_repo(tmp_path, [pytest_check(tmp_path, "tests/test_state.py")])
    write_test_file(repo, "test_state.py", "def test_ok():\n    assert True\n")
    state_path = repo / "docs" / "plan" / "state.json"
    original = '{"schema_version": 1, "plan_version": "1.0", "tasks": {}}\n'
    state_path.write_text(original, encoding="utf-8")

    run(repo, base_env())

    assert state_path.read_text(encoding="utf-8") == original


def test_locked_framework_imports_resolve():
    """Every capability the plan requires must import at the locked version.

    This is the permanent form of the signature inspection T00 performs: if a
    future dependency bump moves or renames one of these, the gate fails here
    instead of deep inside T06/T07/T08/T10/T11.
    """
    import importlib

    failures: list[str] = []
    for module_name, attribute in FRAMEWORK_IMPORTS:
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001 - report every failure at once
            failures.append(f"{module_name}: {type(exc).__name__}: {exc}")
            continue
        if not hasattr(module, attribute):
            failures.append(f"{module_name}.{attribute} is missing")

    assert not failures, "locked framework imports failed:\n" + "\n".join(failures)


def test_repository_manifest_is_self_consistent_for_t00():
    """The real manifest must describe T00 exactly as the plan checker expects."""
    manifest = gate.load_manifest(SCRIPTS.parent)
    task = gate.find_task(manifest, "T00")
    assert [check["id"] for check in task["checks"]] == [
        "acceptance",
        "java-build",
        "frontend-build",
    ]
    assert all(check["required"] for check in task["checks"])
    acceptance = task["checks"][0]
    assert acceptance["kind"] == "test"
    assert acceptance["min_tests"] >= 8
    assert "{evidence_dir}" in acceptance["report"]
