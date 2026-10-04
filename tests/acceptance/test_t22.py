"""T22 acceptance: a deterministic regression over the evidence, and a coverage audit.

This suite does not re-run the other tasks. It audits what they left behind, which is the
stronger question: a receipt is a claim about a run that already happened, and the only way
to find out whether the claim is honest is to open it and compare it with the code.

What is asserted, and why each one is worth an assertion:

* **Every requirement and scenario is traceable** to files that exist. A coverage table whose
  links 404 is worse than no table, because it reads like evidence.
* **No passing receipt hides a skip or a zero collection.** ``passed`` with skipped tests is
  how a suite stops testing something without anyone noticing.
* **``gate --all`` cannot re-enter itself and does not read task status.** The acceptance
  names both; the first is a hang if it regresses, and the second is a report that grades its
  own homework.
* **The audits are over the application, not over a fixture of it.** ``src/`` has no
  unimplemented marker, and the model double appears only where a deterministic test needs one.

The three-side builds are separate required checks on this task; the tests below assert their
artifacts exist, so "the build passed" and "the artifact is there" are not conflated.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import coverage_report  # noqa: E402
import gate  # noqa: E402

TASKS = REPO_ROOT / "docs" / "plan" / "tasks.json"
STATE = REPO_ROOT / "docs" / "plan" / "state.json"
REPORT = REPO_ROOT / "docs" / "runtime" / "coverage-report.md"

#: Requirements whose owning task has not run yet. **Empty now** — T23 and T24 are both done —
#: but the loop below still runs over whatever is listed here, so the next requirement that
#: arrives pending is covered without anyone remembering to restore the rule.
PENDING_REQUIREMENTS: tuple[str, ...] = ()

#: Requirements that graduated, and the task whose gate says so. Each must read 「通过」 in the
#: report and must *still* refuse to claim human review: a gate can fill the automated and live
#: columns, and the third one is not something it can reach.
DELIVERED_REQUIREMENTS: dict[str, str] = {"R27": "T23", "R28": "T24"}

FAKE_MARKERS = (
    "ScriptedChatModel",
    "scripted_model",
    "FakeChatModel",
    "GenericFakeChatModel",
)

#: This file, by path. The audits below define the patterns they look for, so they must not
#: scan themselves — and naming it once keeps that exclusion one line long and reviewable.
SELF_TEST_FILE = "tests/acceptance/test_t22.py"

UNIMPLEMENTED_MARKERS = ("TODO", "FIXME", "XXX:", "NotImplementedError", "raise NotImplemented")


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(TASKS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def state() -> dict:
    return json.loads(STATE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def done_tasks(state, manifest) -> list[dict]:
    return [
        task for task in manifest["tasks"] if state["tasks"].get(task["id"], {}).get("status") == "done"
    ]


def receipt_of(state: dict, task_id: str) -> dict | None:
    relative = state["tasks"].get(task_id, {}).get("receipt")
    if not relative:
        return None
    path = REPO_ROOT / relative
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _test_files_of(task: dict) -> list[str]:
    """Test files a task's checks name. Not a test — the name is prefixed so pytest agrees."""
    found: list[str] = []
    for spec in task["checks"]:
        for argument in spec["argv"]:
            if argument.startswith("tests/") and argument.endswith(".py"):
                found.append(argument)
    return sorted(set(found))


# --------------------------------------------------------------------------- #
# the coverage report is real
# --------------------------------------------------------------------------- #


def test_the_committed_coverage_report_matches_the_generator():
    """Regenerated and compared: a report that has drifted is a document, not evidence."""
    assert REPORT.is_file(), "docs/runtime/coverage-report.md 不存在"

    assert REPORT.read_text(encoding="utf-8") == coverage_report.build(), (
        "覆盖报告与生成器不一致；运行 python scripts/coverage_report.py 重新生成"
    )


def test_the_generator_refuses_to_print_a_path_that_does_not_exist():
    """The property that makes the report trustworthy, tested on a doctored mapping."""
    # A requirement whose tasks are *done*, so the check is actually reached: pending
    # requirements are exempt by design, and doctoring one of those would test nothing.
    original = dict(coverage_report.IMPLEMENTATION_FILES)
    try:
        coverage_report.IMPLEMENTATION_FILES["R19"] = ("src/does/not/exist.py",)
        with pytest.raises(coverage_report.ReportError) as failure:
            coverage_report.build()
        assert "src/does/not/exist.py" in str(failure.value)
        assert "missing implementation file" in str(failure.value)
    finally:
        coverage_report.IMPLEMENTATION_FILES.clear()
        coverage_report.IMPLEMENTATION_FILES.update(original)


def test_every_requirement_from_the_tracking_table_appears():
    requirements = coverage_report.parse_requirements(
        (REPO_ROOT / "docs" / "plan" / "coverage.md").read_text(encoding="utf-8")
    )
    text = REPORT.read_text(encoding="utf-8")

    assert len(requirements) == 28, [item[0] for item in requirements]
    for requirement_id, _capability, _tasks in requirements:
        assert f"| {requirement_id} |" in text, requirement_id


def test_every_verification_scenario_appears_with_an_owner():
    scenarios = coverage_report.parse_scenarios(
        (REPO_ROOT / "docs" / "plan" / "verification.md").read_text(encoding="utf-8")
    )
    text = REPORT.read_text(encoding="utf-8")

    assert len(scenarios) == 22, len(scenarios)
    for scenario_id, _description in scenarios:
        assert f"| {scenario_id} |" in text, scenario_id
        assert "**未覆盖**" not in text.split(f"| {scenario_id} |")[1].split("\n")[0], scenario_id


def test_the_scenario_map_names_only_tasks_that_exist(manifest):
    known = {task["id"] for task in manifest["tasks"]}
    unknown = {
        task_id
        for owners in coverage_report.SCENARIO_OWNERS.values()
        for task_id in owners
        if task_id not in known
    }

    assert unknown == set(), unknown


def test_a_graduated_requirement_never_claims_human_review(state):
    """Two rules, one report: pending work is not claimed, delivered work is not self-reviewed.

    R27 and R28 have both graduated — T23's gate passed in live mode, T24's in integration mode —
    and their rows carry 「通过」 in the columns a gate can fill. The column it cannot fill is
    checked here as well, because that is the claim a reader is most likely to over-read: a
    green report is evidence, not approval.
    """
    text = REPORT.read_text(encoding="utf-8")

    def row(requirement_id: str) -> str:
        return next(line for line in text.splitlines() if line.startswith(f"| {requirement_id} |"))

    for requirement_id in PENDING_REQUIREMENTS:
        pending = row(requirement_id)
        assert "待验收" in pending, pending
        assert "| 通过 |" not in pending, pending

    for requirement_id, owner in DELIVERED_REQUIREMENTS.items():
        delivered = row(requirement_id)
        assert "| 通过 |" in delivered, delivered
        assert "not_reviewed" in delivered, delivered
        assert state["tasks"][owner]["status"] == "done", f"{owner} 还没有 done"


def test_the_report_separates_automated_live_and_human_status():
    """Three columns, because the three are not interchangeable."""
    text = REPORT.read_text(encoding="utf-8")

    for header in ("自动", "真实", "人工"):
        assert f"| {header} |" in text, header
    assert "自动通过**不代表**真实服务通过" in text
    assert "自动通过不代表人工已接受" in text


def test_a_requirement_whose_tasks_are_all_pending_is_not_reported_as_passed():
    """The rule the previous test states, exercised on the data rather than the prose."""
    requirements = coverage_report.parse_requirements(
        (REPO_ROOT / "docs" / "plan" / "coverage.md").read_text(encoding="utf-8")
    )
    text = REPORT.read_text(encoding="utf-8")

    for requirement_id, _capability, main_tasks in requirements:
        task_ids = [item.strip() for item in main_tasks.split("/") if item.strip()]
        if not all(
            json.loads(STATE.read_text(encoding="utf-8"))["tasks"].get(task_id, {}).get("status")
            != "done"
            for task_id in task_ids
        ):
            continue
        line = next(line for line in text.splitlines() if line.startswith(f"| {requirement_id} |"))
        assert "待验收" in line, line


# --------------------------------------------------------------------------- #
# the evidence behind it
# --------------------------------------------------------------------------- #


def test_every_done_task_has_a_passing_receipt_on_disk(state, done_tasks):
    missing = []
    for task in done_tasks:
        receipt = receipt_of(state, task["id"])
        if receipt is None or receipt.get("status") != "passed":
            missing.append(task["id"])

    assert missing == [], f"这些任务登记为 done 但没有通过的 receipt：{missing}"


def test_every_required_check_of_a_done_task_passed(state, done_tasks):
    failures = []
    for task in done_tasks:
        receipt = receipt_of(state, task["id"])
        if receipt is None:
            continue
        required = {spec["id"] for spec in task["checks"] if spec.get("required")}
        by_id = {result["id"]: result for result in receipt["checks"]}
        for check_id in sorted(required):
            result = by_id.get(check_id)
            if result is None or result.get("status") != "passed":
                failures.append(f"{task['id']}/{check_id}")

    assert failures == [], failures


def test_no_passing_receipt_hides_a_skipped_test(state, done_tasks):
    """`passed` with skips is how a suite quietly stops testing something."""
    offenders = []
    for task in done_tasks:
        receipt = receipt_of(state, task["id"])
        if receipt is None:
            continue
        for result in receipt["checks"]:
            if result.get("status") != "passed":
                continue
            if int(result.get("tests_skipped") or 0) > 0:
                offenders.append(f"{task['id']}/{result['id']}")

    assert offenders == [], offenders


def test_no_test_check_collected_zero_tests(state, done_tasks):
    """A zero collection passes pytest and asserts nothing."""
    offenders = []
    for task in done_tasks:
        receipt = receipt_of(state, task["id"])
        if receipt is None:
            continue
        for result in receipt["checks"]:
            if result.get("tests_total") is not None and int(result["tests_total"]) == 0:
                offenders.append(f"{task['id']}/{result['id']}")

    assert offenders == [], offenders


def test_every_test_check_meets_the_minimum_its_manifest_declares(state, done_tasks, manifest):
    by_id = {task["id"]: task for task in manifest["tasks"]}
    shortfalls = []
    for task in done_tasks:
        receipt = receipt_of(state, task["id"])
        if receipt is None:
            continue
        for spec in by_id[task["id"]]["checks"]:
            if spec["kind"] != "test" or not spec.get("min_tests"):
                continue
            result = next(
                (item for item in receipt["checks"] if item["id"] == spec["id"]), None
            )
            if result is None or int(result.get("tests_total") or 0) < spec["min_tests"]:
                shortfalls.append(
                    f"{task['id']}/{spec['id']}: "
                    f"{(result or {}).get('tests_total')} < {spec['min_tests']}"
                )

    assert shortfalls == [], shortfalls


def test_every_task_declares_at_least_one_required_check(manifest):
    """A task with no required check cannot fail, which is not the same as passing."""
    offenders = [
        task["id"]
        for task in manifest["tasks"]
        if not any(spec.get("required") for spec in task["checks"])
    ]

    assert offenders == [], offenders


def test_a_passing_receipt_records_the_revision_it_ran_against(state, done_tasks):
    """Without this, a receipt cannot be tied to the code that produced it."""
    offenders = []
    for task in done_tasks:
        receipt = receipt_of(state, task["id"])
        if receipt is None:
            continue
        revision = receipt.get("source_revision")
        if not isinstance(revision, dict) or not revision:
            offenders.append(task["id"])

    assert offenders == [], offenders


# --------------------------------------------------------------------------- #
# gate --all
# --------------------------------------------------------------------------- #


def test_gate_all_does_not_re_enter_itself(manifest):
    """The real manifest, checked: a check that shells out to gate.py would loop."""
    gate.assert_no_self_invocation(manifest)


def test_the_self_invocation_guard_actually_fires(manifest):
    """A guard nobody has seen refuse anything is a comment."""
    doctored = json.loads(json.dumps(manifest))
    doctored["tasks"][0]["checks"][0]["argv"] = ["python", "scripts/gate.py", "T00"]

    with pytest.raises(gate.GateError) as failure:
        gate.assert_no_self_invocation(doctored)

    assert "gate.py" in str(failure.value)


def test_gate_all_targets_every_task_without_reading_state(manifest):
    """The target list comes from the manifest alone, so it cannot echo a recorded claim."""
    targets = gate.all_mode_targets(manifest)

    assert targets == [task["id"] for task in manifest["tasks"]]
    assert len(targets) == len(manifest["tasks"])


def test_gate_all_does_not_consult_state_json():
    """Asserted against the source: the runner must not read the file that records claims."""
    source = (REPO_ROOT / "scripts" / "gate.py").read_text(encoding="utf-8")

    all_branch = source.split("if args.all:", 1)[1].split("if args.all else", 1)[0]
    # The *name* of the file appears in the message printed to the operator, which is the
    # point of the message. What must not appear is a way to read it.
    for read in ("load_state", "state[", "read_text", "json.loads", "open("):
        assert read not in all_branch, f"--all 分支里出现了 {read}"
    assert "all_mode_targets(manifest)" in all_branch
    # And the aggregate status comes from the checks, not from the recorded review.
    assert "STATUS_PASSED" in source
    assert "review" not in source.split("def main(")[1]


def test_gate_separates_blocked_from_failed(manifest):
    """A blocked prerequisite is not a failure, and the exit codes say so."""
    assert gate.EXIT_PASSED == 0
    assert gate.EXIT_FAILED == 1
    assert gate.EXIT_BLOCKED == 2
    assert gate.STATUS_BLOCKED != gate.STATUS_FAILED
    assert gate._blocked_reason  # noqa: SLF001 - the marker's reader exists


def test_a_blocked_check_is_reported_with_its_reason():
    assert gate._blocked_reason("GATE_BLOCKED: missing configuration: ZHIPU_API_KEY") is not None
    assert gate._blocked_reason("all tests passed") is None


# --------------------------------------------------------------------------- #
# the audits the task asks for
# --------------------------------------------------------------------------- #


def test_the_audits_cover_the_whole_manifest(done_tasks, manifest):
    """Sanity for this file: the evidence checks above really do cover every done task."""
    assert len(done_tasks) == sum(
        1 for entry in json.loads(STATE.read_text(encoding="utf-8"))["tasks"].values()
        if entry["status"] == "done"
    )
    assert len(done_tasks) >= 20


SKIP_DECORATORS = {"skip", "skipif", "xfail"}


def _is_pytest_mark(decorator: object) -> bool:
    """``@pytest.mark.skip`` / ``skipif`` / ``xfail``, and nothing else."""
    import ast

    node = decorator
    if isinstance(node, ast.Call):
        node = node.func
    if not isinstance(node, ast.Attribute) or node.attr not in SKIP_DECORATORS:
        return False
    inner = node.value
    return (
        isinstance(inner, ast.Attribute)
        and inner.attr == "mark"
        and isinstance(inner.value, ast.Name)
        and inner.value.id == "pytest"
    )


def _skip_sites(path: Path) -> list[int]:
    """Line numbers of *real* skip markers, found through the syntax tree.

    A text scan is not good enough here, and T00 is the reason: its suite contains the string
    ``"@pytest.mark.skip(reason='not runnable here')"`` as the payload of a test that checks
    the gate *fails* a receipt with skipped tests. A grep would call that a skip in a mandatory
    file and be wrong. Parsing finds decorators and calls, which is what a skip actually is.
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[int] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for decorator in node.decorator_list:
                if _is_pytest_mark(decorator):
                    found.append(decorator.lineno)
        elif isinstance(node, ast.Call):
            target = node.func
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "skip"
                and isinstance(target.value, ast.Name)
                and target.value.id == "pytest"
            ):
                found.append(node.lineno)
    return found


def test_no_mandatory_test_file_carries_a_skip_marker(done_tasks):
    """`mandatory tests 无 skip` — read from the syntax tree, not from the text."""
    offenders: list[str] = []

    for task in done_tasks:
        for relative in _test_files_of(task):
            path = REPO_ROOT / relative
            if not path.is_file():
                continue
            lines = _skip_sites(path)
            if lines:
                offenders.append(f"{relative}: lines {lines}")

    assert offenders == [], f"必需检查的测试文件里出现了真正的 skip/xfail：{offenders}"


def test_the_skip_audit_distinguishes_a_marker_from_a_string(tmp_path):
    """The property that makes the audit above worth trusting, tested on both cases."""
    marked = tmp_path / "marked.py"
    marked.write_text(
        "import pytest\n\n\n@pytest.mark.skip(reason='x')\ndef test_a():\n    pass\n",
        encoding="utf-8",
    )
    quoted = tmp_path / "quoted.py"
    quoted.write_text(
        "PAYLOAD = \"@pytest.mark.skip(reason='x')\"\n\n\ndef test_b():\n    pass\n",
        encoding="utf-8",
    )

    assert _skip_sites(marked) == [4]
    assert _skip_sites(quoted) == []


def test_live_mode_checks_do_not_use_a_model_double(manifest, state):
    """`live 模式禁止 fake` — a live check that stubbed the model would prove nothing."""
    offenders: list[str] = []
    for task in manifest["tasks"]:
        if state["tasks"].get(task["id"], {}).get("status") != "done":
            continue
        live = any(spec["mode"] == "live" and spec.get("required") for spec in task["checks"])
        if not live:
            continue
        for relative in _test_files_of(task):
            path = REPO_ROOT / relative
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            for marker in FAKE_MARKERS:
                if marker in text:
                    offenders.append(f"{relative}: {marker}")

    assert offenders == [], offenders


def test_the_application_has_no_unimplemented_marker():
    """`无实现 TODO` — scanned over src/, where a marker would mean a promise, not a feature."""
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "src").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for marker in UNIMPLEMENTED_MARKERS:
            if marker in text:
                offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}: {marker}")

    assert offenders == [], offenders


def test_no_task_returns_a_canned_demo_reply(manifest, state):
    """A fixed answer presented as a result is the failure `不能只 grep` is guarding against."""
    # Word boundaries matter: "scanned" contains "canned", and a substring match would flag a
    # test whose name happens to hold those letters. The first run of this audit did exactly
    # that, which is the same class of mistake the audit exists to catch.
    suspicious = re.compile(
        r"(演示回复|固定回复|\bcanned\b|\bfabricated\b|\bfabricate\b)", re.IGNORECASE
    )
    offenders: list[str] = []

    for task in manifest["tasks"]:
        if state["tasks"].get(task["id"], {}).get("status") != "done":
            continue
        for relative in _test_files_of(task):
            # This file defines the pattern, so it necessarily contains it. Scanning itself
            # could only ever find the detector, and widening this exclusion would take a
            # deliberate edit rather than a shrug.
            if relative == SELF_TEST_FILE:
                continue
            path = REPO_ROOT / relative
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if suspicious.search(line) and not line.lstrip().startswith("#"):
                    offenders.append(f"{relative}: {line.strip()[:80]}")

    assert offenders == [], offenders


def test_the_scripted_model_double_is_declared_by_the_tests_that_use_it(done_tasks):
    """It is a legitimate integration double; the audit is that it stays inside tests."""
    in_src = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / "src").rglob("*.py")
        if any(marker in path.read_text(encoding="utf-8") for marker in FAKE_MARKERS)
    ]

    assert in_src == [], f"模型替身不应出现在 src/：{in_src}"


# --------------------------------------------------------------------------- #
# the three sides
# --------------------------------------------------------------------------- #


def test_the_java_artifact_exists():
    """`java-build` is a separate required check; this asserts it produced something."""
    jars = sorted((REPO_ROOT / "erp" / "target").glob("*.jar"))

    assert jars, "erp/target 下没有构建产物；java-build 是否真的跑过？"
    assert any(jar.stat().st_size > 0 for jar in jars)


def test_the_frontend_build_output_exists():
    dist = REPO_ROOT / "frontend" / "dist"

    assert dist.is_dir(), "frontend/dist 不存在；frontend-build 是否真的跑过？"
    assert (dist / "index.html").is_file()
    assert list(dist.rglob("*.js")), "构建产物里没有 JS"


def test_the_frontend_has_its_own_test_suite_and_it_is_not_empty():
    """`三端构建和测试通过` — the frontend side has tests, not only a build."""
    specs = sorted((REPO_ROOT / "frontend" / "src").rglob("*.spec.ts")) + sorted(
        (REPO_ROOT / "frontend" / "tests").rglob("*.spec.ts")
    )

    assert specs, "前端没有任何 spec 文件"


def test_the_python_suites_are_discoverable():
    """Each suite the step names exists and is not empty.

    "unit" is a **check mode**, not a directory: the repository's suites are ``acceptance``
    (some of whose checks run in unit mode), ``contract``, ``integration``, plus the CAP
    matrix under ``compat`` and the plan guard under ``plan``. Asserting a ``tests/unit``
    directory would be asserting a layout this project never had, so what is asserted is that
    every suite that exists has tests in it and that unit-mode checks are really declared.
    """
    for suite in ("acceptance", "contract", "integration"):
        directory = REPO_ROOT / "tests" / suite
        assert directory.is_dir(), f"tests/{suite} 不存在"
        assert list(directory.rglob("test_*.py")), f"tests/{suite} 里没有测试文件"

    # ``tests/compat`` is a library, not a suite: it holds the CAP-01..08 experiments that
    # T06's acceptance imports. Asserting test files there would be asserting a shape it
    # never had, so what is asserted is that the experiments themselves are present.
    compat = REPO_ROOT / "tests" / "compat"
    assert (compat / "capabilities.py").is_file()
    assert (compat / "streaming.py").is_file()


def test_unit_mode_checks_exist_somewhere(manifest):
    """The step asks for unit tests to be run; this is what "unit" means here."""
    modes = {spec["mode"] for task in manifest["tasks"] for spec in task["checks"]}

    assert "unit" in modes
    assert {"integration", "live"} <= modes
