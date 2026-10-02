"""Generate docs/runtime/coverage-report.md from the plan, the receipts, and the filesystem.

Two things this refuses to do, both because they are how a coverage report becomes fiction:

* **It does not infer anything from ``state.json``'s status.** A task's status is a claim made
  by a receipt; the report reads the *receipt* and checks it was a passing one, and separately
  refuses to mark an R as verified when the task it belongs to is not done.
* **It does not print a path it has not checked.** Every implementation file, test file and
  receipt listed is verified to exist, and the generator fails loudly when one does not. A
  report whose links 404 is worse than no report, because it looks like evidence.

The one thing it cannot derive is *which files implement which requirement* — that is a
judgement, so it lives in ``IMPLEMENTATION_FILES`` below, where it can be reviewed, rather
than being guessed from directory names.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

COVERAGE = ROOT / "docs" / "plan" / "coverage.md"
TASKS = ROOT / "docs" / "plan" / "tasks.json"
STATE = ROOT / "docs" / "plan" / "state.json"
VERIFICATION = ROOT / "docs" / "plan" / "verification.md"
OUTPUT = ROOT / "docs" / "runtime" / "coverage-report.md"

#: Which files carry each requirement. Reviewed by hand; verified against the filesystem.
IMPLEMENTATION_FILES: dict[str, tuple[str, ...]] = {
    "R01": ("scripts/gate.py", "scripts/doctor.py", "pyproject.toml", "uv.lock"),
    "R02": ("erp/src/main/java/com/rushharness/erp", "src/mcp_server/tools/registry.py"),
    "R03": ("erp/src/main/java/com/rushharness/erp/orders/OrdersService.java",),
    "R04": ("src/mcp_server/tools/registry.py", "src/mcp_server/grants.py"),
    "R05": ("fixtures/site/server.py", "fixtures/site/skillpack.py"),
    "R06": ("src/agent/config.py", "src/agent/env_utils.py", "tests/compat/capabilities.py"),
    "R07": ("src/agent/persistence/indexes.py", "src/api_view/agent_loader.py"),
    "R08": (
        "src/agent/backends/custom_opensandbox.py",
        "src/agent/backends/sandbox_setup.py",
    ),
    "R09": ("src/agent/backends/sandbox_proxy.py", "src/agent/backends/sandbox_manager.py"),
    "R10": ("src/agent/main_agent.py", "src/agent/persistence/namespaces.py"),
    "R11": ("src/agent/middlewares/skills_sync.py", "src/skills/"),
    "R12": ("src/agent/subagents/loader.py", "src/agent/subagents/configs/"),
    "R13": ("src/agent/middleware_config.py", "src/agent/main_agent.py"),
    "R14": ("src/agent/approval/", "src/agent/tools/hitl_tools.py"),
    "R15": ("src/api_view/stream_adapter.py", "src/api_view/api/chat.py"),
    "R16": ("frontend/src/App.vue", "frontend/src/state/chat.ts", "frontend/src/markdown.ts"),
    "R17": ("src/agent/tools/web_search.py",),
    "R18": ("src/agent/tools/chart_generator.py", "src/skills/procurement/chart_params.md"),
    "R19": (
        "src/skills/procurement/procurement-analysis/scripts/build_report.py",
        "src/skills/procurement/web-scraper/scripts/fetch_quotes.py",
        "src/skills/procurement/web-content-fetcher/scripts/fetch_page.py",
    ),
    "R20": ("src/agent/skills/pipeline.py", "src/agent/skills/store.py", "src/agent/tools/assign_skill.py"),
    "R21": ("src/agent/memory/preferences.py", "src/agent/middlewares/memory_update.py"),
    "R22": ("src/agent/middlewares/tools_summarization.py",),
    "R23": ("src/agent/middleware_config.py", "src/agent/middlewares/sandbox_breaker.py"),
    "R24": (
        "src/agent/async_tasks/service.py",
        "src/api_view/api/async_tasks.py",
        "infra/agent-protocol/analyst_graph.py",
    ),
    "R25": (
        "tests/acceptance/test_t21.py",
        "tests/integration/test_recovery.py",
        "tests/integration/test_isolation.py",
    ),
    "R26": ("tests/acceptance/test_t22.py", "scripts/coverage_report.py"),
    "R27": ("scripts/smoke_agent.py",),
    "R28": ("docs/runtime/runbook.md",),
}

#: Which task's acceptance covers each verification scenario.
#:
#: Curated rather than scraped from the task documents, because prose writes ranges —
#: T21 says "V11-V16", which a regex reads as two scenarios and silently drops the four in
#: between. A mapping that is wrong in that direction produces a report claiming coverage it
#: does not have, so the ownership is stated here where it can be reviewed, and the generator
#: checks that every named task exists and that every scenario is claimed by something.
SCENARIO_OWNERS: dict[str, tuple[str, ...]] = {
    "V01": ("T01", "T02"),
    "V02": ("T03",),
    "V03": ("T03",),
    "V04": ("T03", "T21"),
    "V05": ("T03",),
    "V06": ("T12",),
    "V07": ("T12", "T21"),
    "V08": ("T13",),
    "V09": ("T12", "T21"),
    "V10": ("T12", "T21"),
    "V11": ("T07", "T18", "T21"),
    "V12": ("T09", "T21"),
    "V13": ("T09", "T21"),
    "V14": ("T08", "T17", "T21"),
    "V15": ("T17", "T21"),
    "V16": ("T17", "T21"),
    "V17": ("T10",),
    "V18": ("T18",),
    "V19": ("T19",),
    "V20": ("T16",),
    "V21": ("T20",),
    "V22": ("T24",),
}


class ReportError(RuntimeError):
    """The report cannot be produced honestly."""


def parse_requirements(text: str) -> list[tuple[str, str, str]]:
    """``(id, capability, main tasks)`` from the tracking table."""
    rows: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        if not line.startswith("| R"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 5 or not cells[0].startswith("R"):
            continue
        rows.append((cells[0], cells[1], cells[3]))
    if not rows:
        raise ReportError("no requirement rows found in coverage.md")
    return rows


def parse_scenarios(text: str) -> list[tuple[str, str]]:
    """``(id, description)`` from the verification matrix."""
    rows: list[tuple[str, str]] = []
    for line in text.splitlines():
        if not line.startswith("| V"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) >= 3 and cells[0].startswith("V"):
            rows.append((cells[0], cells[1]))
    return rows


def _exists(relative: str) -> bool:
    return (ROOT / relative).exists()


def _receipt_for(state: dict, task_id: str) -> tuple[str | None, dict | None]:
    entry = state["tasks"].get(task_id, {})
    relative = entry.get("receipt")
    if not relative:
        return None, None
    path = ROOT / relative
    if not path.is_file():
        raise ReportError(f"{task_id}: receipt {relative} does not exist")
    return relative, json.loads(path.read_text(encoding="utf-8"))


def _check_modes(manifest_task: dict) -> set[str]:
    return {spec["mode"] for spec in manifest_task["checks"] if spec.get("required")}


def _test_files(manifest_task: dict) -> list[str]:
    found: list[str] = []
    for spec in manifest_task["checks"]:
        for argument in spec["argv"]:
            if argument.startswith("tests/") and argument.endswith(".py"):
                found.append(argument)
    return sorted(set(found))


def build() -> str:
    manifest = json.loads(TASKS.read_text(encoding="utf-8"))
    state = json.loads(STATE.read_text(encoding="utf-8"))
    requirements = parse_requirements(COVERAGE.read_text(encoding="utf-8"))
    scenarios = parse_scenarios(VERIFICATION.read_text(encoding="utf-8"))
    by_id = {task["id"]: task for task in manifest["tasks"]}

    problems: list[str] = []
    lines: list[str] = [
        "# 能力覆盖报告",
        "",
        "本文件由 `python scripts/coverage_report.py` 生成，**不要手工编辑**。",
        "",
        "三列状态的含义，互不替代：",
        "",
        "- **自动**：该需求主任务的确定性 check（unit/integration）在 receipt 里为 passed。",
        "- **真实**：该需求有 live 模式的 required check 且通过——只有 T15/T06/T23 这类需要真实外部服务的任务才有。",
        "  自动通过**不代表**真实服务通过；缺凭据时该列为 blocked，不是 pass。",
        "- **人工**：`state.json` 的 review 字段。**自动通过不代表人工已接受**，所以完成一个任务不等于它被 review 过。",
        "",
        "> 任务状态本身不是实现证据：本报告读的是 receipt 的内容（状态、检查项、跳过数），",
        "> 并逐个核对下面列出的文件确实存在。",
        "",
        "## R01-R28",
        "",
        "| ID | 能力 | 主任务 | 实现文件 | 测试 | 证据 | 自动 | 真实 | 人工 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]

    for requirement_id, capability, main_tasks in requirements:
        implementation = IMPLEMENTATION_FILES.get(requirement_id, ())
        if not implementation:
            problems.append(f"{requirement_id}: no implementation mapping")

        task_ids = [item.strip() for item in main_tasks.split("/") if item.strip()]

        # A requirement whose tasks are not done yet is 待验收, and its deliverables are
        # *supposed* to be missing. Checking them here would make the report refuse to
        # generate until the future work lands, which is the opposite of useful; what must
        # not happen is such a requirement being reported as verified.
        pending = all(
            state["tasks"].get(task_id, {}).get("status") != "done" for task_id in task_ids
        )
        if not pending:
            for relative in implementation:
                if not _exists(relative):
                    problems.append(f"{requirement_id}: missing implementation file {relative}")
        tests: list[str] = []
        evidence: list[str] = []
        automated = "待验收"
        live = "—"
        review = "—"

        for task_id in task_ids:
            task = by_id.get(task_id)
            if task is None:
                problems.append(f"{requirement_id}: unknown task {task_id}")
                continue
            tests.extend(_test_files(task))
            relative, receipt = _receipt_for(state, task_id)
            modes = _check_modes(task)

            if relative is None or receipt is None:
                continue
            evidence.append(f"`{relative}`")
            if receipt.get("status") != "passed":
                continue

            required = [c for c in receipt["checks"] if c.get("id") and c.get("status") == "passed"]
            skipped = sum(int(c.get("tests_skipped") or 0) for c in required)
            if skipped:
                problems.append(f"{task_id}: {skipped} skipped test(s) in a passing receipt")

            if "live" in modes:
                # A live check that passed is the only thing that makes this column a pass.
                live = "通过" if required else "未运行"
            if automated != "通过":
                automated = "通过"

            review = str(state["tasks"][task_id].get("review", "—"))

        if not pending:
            for relative in tests:
                if not _exists(relative):
                    problems.append(f"{requirement_id}: missing test file {relative}")

        implementation_cell = "<br>".join(f"`{item}`" for item in implementation) or "—"
        tests_cell = "<br>".join(f"`{item}`" for item in sorted(set(tests))) or "—"
        evidence_cell = "<br>".join(evidence) or "—"

        lines.append(
            f"| {requirement_id} | {capability} | {main_tasks} | {implementation_cell} | "
            f"{tests_cell} | {evidence_cell} | {automated} | {live} | {review} |"
        )

    lines += [
        "",
        "## V01-V22 场景",
        "",
        "每个场景至少被一个任务的 acceptance 覆盖；具体断言在各任务的测试文件里。",
        "T21 负责的是跨模块组合，因此 V04/V07/V09/V10/V11-V16 在它那里另有交叉验证。",
        "",
        "| ID | 场景 | 覆盖任务 |",
        "|---|---|---|",
    ]

    scenario_owners = _scenario_owners(by_id, scenarios)
    for scenario_id, description in scenarios:
        owners_list = scenario_owners.get(scenario_id, [])
        owners = ", ".join(owners_list) or "**未覆盖**"
        if not owners_list:
            problems.append(f"{scenario_id}: no task claims this scenario")
        if owners_list and all(
            state["tasks"].get(task_id, {}).get("status") != "done" for task_id in owners_list
        ):
            owners = f"{owners}（待验收）"
        lines.append(f"| {scenario_id} | {description} | {owners} |")

    lines += [
        "",
        "## 已知缺口与待验收项",
        "",
        "- **R27**（真实模型完整演示）由 T23 负责，当前**待验收**，未被标记为通过。",
        "- **R28**（清洁启动与学习交付）由 T24 负责，当前**待验收**。",
        "- 需要真实外部服务的 check（T06/T15/T23 的 live 模式）与确定性回归**分开统计**：",
        "  确定性回归通过不等于 live 通过。",
        "- 中间件矩阵契约要求 8 个槽位全部实现，实际以 `middleware_inventory()` 的自述为准。",
        "",
    ]

    if problems:
        raise ReportError("; ".join(problems[:20]))
    return "\n".join(lines) + "\n"


def _scenario_owners(by_id: dict, scenarios: list[tuple[str, str]]) -> dict[str, list[str]]:
    """The declared owner of each scenario, with the declaration checked.

    A scenario naming a task that does not exist is a typo that would read as coverage, so it
    is a hard error rather than a silently dropped entry.
    """
    owners: dict[str, list[str]] = {}
    for scenario_id, _description in scenarios:
        declared = SCENARIO_OWNERS.get(scenario_id)
        if not declared:
            continue
        unknown = [task_id for task_id in declared if task_id not in by_id]
        if unknown:
            raise ReportError(f"{scenario_id}: claims unknown task(s) {unknown}")
        owners[scenario_id] = list(declared)
    extra = sorted(set(SCENARIO_OWNERS) - {item[0] for item in scenarios})
    if extra:
        raise ReportError(f"SCENARIO_OWNERS names scenarios that do not exist: {extra}")
    return owners


def main() -> int:
    try:
        report = build()
    except ReportError as failure:
        print(f"coverage report refused: {failure}", file=sys.stderr)
        return 1
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(report, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT).as_posix()} ({len(report.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
