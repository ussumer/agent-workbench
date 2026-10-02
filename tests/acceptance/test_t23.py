"""T23 acceptance: the demo round, read back from the evidence it left behind.

This suite does not re-run the eight cases. It opens what the round wrote and asks whether the
files support the claims made about them, which is the harder question — a trial file is a claim
about a conversation that already happened, and the only way to find out whether it is honest is
to compare it with the stream, the trace and the ERP's own answer.

What is asserted, and why each one is worth an assertion:

* **The verdict is about this run.** A round directory can be reused, and a stale trial counted
  as this round's is a wrong verdict in both directions — it can report a failure that never
  happened, or a pass that is no longer true. So every counted trial has to carry the run's own id.
* **A run that died is not a pass.** ``ok`` once meant "no failure recorded", and a run that dies
  records no failure. The status of *every* run in a trial is checked, not just the last one.
* **The work itself was done.** Each case names the tool call without which it cannot have
  happened (``order_create`` for a create), and the trace has to contain it. The cases where no
  single call is necessary are deliberately not in that list: the model may reach an answer by
  several routes, and pinning the route it happened to take would fail on a legitimate variation.
* **The evidence is the wire's.** The tool trace is rebuilt from ``events.jsonl`` here, by the
  same parser the runner uses, so a trace produced by some other path than the frames would show.
* **The assets are real artifacts.** Three chart families as decodable PNGs with matching digests,
  the search's own record of which engine served it, and two viewports whose measured layout has
  no overlap — this is what "the UI works" means here, as opposed to a claim that it does.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (str(REPO_ROOT / "scripts"), str(REPO_ROOT / "tests")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import demo  # noqa: E402  - its `trace_of` is the wire's own parser, reused rather than copied
from live import assets as live_assets  # noqa: E402
from live import scenarios  # noqa: E402

#: Which round to read. Overridable so a second round can be audited without editing this file.
ROUND_NAME = os.environ.get("T23_ROUND", "r1")
ROUND = REPO_ROOT / "artifacts" / "live" / ROUND_NAME

CASES: tuple[str, ...] = tuple(scenario.id for scenario in scenarios.SCENARIOS)

#: Tools without which a case cannot have been carried out. Kept to the causally necessary ones
#: on purpose — see the module docstring. A create cannot happen without ``order_create``
#: whatever else the agent chose to do first; D06's preference may be recorded by the middleware
#: or by the model, so no single call is required and none is asserted.
NECESSARY_TOOLS: dict[str, tuple[str, ...]] = {
    "D01": ("inventory_warning",),
    "D02": ("order_create",),
    "D03": ("order_update",),
    "D04": ("order_create",),
    "D05": ("chart_generator",),
}

#: Model doubles the live preflight refuses. Repeated here so this suite fails for the same
#: reason the round would have, rather than only trusting that the round would have noticed.
DOUBLE_MARKERS: tuple[str, ...] = ("scripted", "fake", "stub", "dummy", "mock", "placeholder")


def _read(relative: str) -> dict[str, Any]:
    path = ROUND / relative
    if not path.is_file():
        pytest.fail(
            f"{path.relative_to(REPO_ROOT)} 不存在。T23 的证据由 "
            f"`python scripts/demo.py --round {ROUND_NAME}` 产出（资产用 `--assets`，"
            "截图用 `--screenshots`）；本套件只读证据、不重跑模型，"
            "证据不在就说明这一轮还没跑完。"
        )
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _stamp() -> dict[str, Any]:
    return _read("round.json")


@lru_cache(maxsize=1)
def _summary() -> dict[str, Any]:
    return _read("summary.json")


@lru_cache(maxsize=1)
def _assets() -> dict[str, Any]:
    return _read("assets.json")


@lru_cache(maxsize=1)
def _shots() -> dict[str, Any]:
    return _read("screenshots.json")


@lru_cache(maxsize=1)
def _preflight() -> dict[str, Any]:
    return _read("preflight.json")


@lru_cache(maxsize=1)
def _all_trials() -> tuple[dict[str, Any], ...]:
    found = []
    for directory in sorted(ROUND.glob("*-*")):
        trial_file = directory / "trial.json"
        if trial_file.is_file():
            found.append(json.loads(trial_file.read_text(encoding="utf-8")))
    return tuple(found)


def _case_trials(case_id: str) -> tuple[dict[str, Any], ...]:
    return tuple(entry for entry in _all_trials() if entry.get("scenario") == case_id)


# --------------------------------------------------------------------------- the round


def test_the_round_names_itself_and_the_cases_it_ran() -> None:
    stamp = _stamp()
    assert stamp.get("run_id"), "round.json 没有 run_id，无法判断哪些试验属于本轮"
    assert stamp.get("cases") == list(CASES)
    assert stamp.get("trials_per_case") == scenarios.TRIALS_PER_SCENARIO
    assert stamp.get("opened_at")


def test_the_summary_counts_only_this_runs_trials() -> None:
    # The defect this pins: a directory reused from an earlier run had its old trials counted
    # as this round's — a failure yesterday appeared as a failure today.
    assert _summary().get("ignored_from_other_runs") == []


def test_the_round_met_the_contract_it_declared() -> None:
    summary = _summary()
    assert summary["trial_total"] == scenarios.TRIAL_TOTAL
    assert summary["required_successes"] == scenarios.REQUIRED_SUCCESSES
    assert summary["successes"] >= scenarios.REQUIRED_SUCCESSES, (
        f"只有 {summary['successes']}/{summary['trial_total']} 次成功，"
        f"要求 {scenarios.REQUIRED_SUCCESSES}"
    )
    assert summary["passed_overall"] is True


def test_every_case_met_its_own_minimum() -> None:
    summary = _summary()
    assert set(summary["per_case"]) == set(CASES)
    assert summary["cases_short_of_minimum"] == []
    assert summary["every_case_met_minimum"] is True


def test_no_trial_hid_a_failed_run() -> None:
    # ``ok`` used to mean "no failure recorded", and a run that dies records no failure: two
    # trials were counted as passes while their runs had reported ``failed`` in seven seconds.
    for entry in _all_trials():
        label = f"{entry.get('scenario')}-{entry.get('trial')}"
        statuses = entry.get("statuses") or []
        assert statuses, f"{label} 没有记录每次运行的状态，无法看出这一轮里有没有死掉的运行"
        assert "failed" not in statuses, f"{label} 里有运行以 failed 结束：{statuses}"


@pytest.mark.parametrize("case_id", CASES)
def test_case_trials_are_present_and_belong_to_this_run(case_id: str) -> None:
    trials = _case_trials(case_id)
    assert len(trials) == scenarios.TRIALS_PER_SCENARIO, f"{case_id} 的试验数不对"
    run_id = _stamp()["run_id"]
    for entry in trials:
        assert entry.get("round_run_id") == run_id, f"{case_id} 里有不属于本轮的试验"
        assert entry.get("thread_id"), f"{case_id} 的试验没有 thread"
        assert entry.get("trace"), f"{case_id} 有一次试验没有任何工具调用"


def _called_tools(entries: tuple[dict[str, Any], ...]) -> set[str]:
    """Tools a case invoked — including the ones approval intercepted before invocation.

    A gated write never reaches its tool body, so it fires no tool callback and cannot appear
    in ``trace``; the interrupt is where it is visible (``candidates[].tool_name``). Reading
    only the trace would report a correctly-gated create as "never called" — which is how
    D04's create looked, while its approval interrupt named ``order_create`` with the right
    arguments and the ERP ended up with exactly one new order.
    """
    names: set[str] = set()
    for entry in entries:
        names.update(call["tool"] for call in entry["trace"])
        for item in entry["interrupts"]:
            for candidate in item.get("candidates") or []:
                if candidate.get("tool_name"):
                    names.add(str(candidate["tool_name"]))
    return names


@pytest.mark.parametrize("case_id", sorted(NECESSARY_TOOLS))
def test_the_work_itself_was_done(case_id: str) -> None:
    called = _called_tools(_case_trials(case_id))
    missing = [name for name in NECESSARY_TOOLS[case_id] if name not in called]
    assert not missing, f"{case_id} 没有调用 {missing}；它实际调用的是 {sorted(called)}"


def test_every_recorded_tool_call_carries_its_arguments() -> None:
    # The arguments are what explain *why* the model chose something: a delegated sub-agent's
    # calls never appear in the parent's state, so without them a delegation reads as a bare
    # ``task`` and a fabricated answer cannot be told from a real one.
    for entry in _all_trials():
        label = f"{entry.get('scenario')}-{entry.get('trial')}"
        for call in entry["trace"]:
            assert call.get("tool"), f"{label} 有一条没有工具名的调用"
            assert "arguments" in call, f"{label} 的 {call.get('tool')} 没有记录参数"
        assert any(call.get("arguments") for call in entry["trace"]), (
            f"{label} 的调用全都没有参数，读不出模型为什么这么做"
        )


def test_the_wire_replays_into_the_recorded_calls() -> None:
    # ``trace`` comes from callbacks and ``stream_tools`` from the stream, and they differ by
    # design: a sub-agent runs inside ``task`` and only the main agent's calls reach the wire.
    # Re-deriving the wire's list from the raw frames here keeps the two from drifting.
    for entry in _case_trials("D03"):
        name = f"{entry['scenario']}-{entry['trial']}"
        path = ROUND / name / "events.jsonl"
        assert path.is_file(), f"{name} 没有保留原始事件流"
        frames = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert frames, f"{name} 的事件流是空的"
        assert [call["tool"] for call in demo.trace_of(frames)] == entry["stream_tools"]
        assert len(entry["trace"]) >= len(entry["stream_tools"])


def test_each_trial_kept_its_scenario_snapshot() -> None:
    for entry in _all_trials():
        name = f"{entry['scenario']}-{entry['trial']}"
        snapshot = _read(f"{name}/scenario.json")
        assert snapshot["id"] == entry["scenario"]
        assert snapshot["turns"], f"{name} 的场景快照没有用户话术"


def test_every_trial_recorded_the_stream_it_delivered() -> None:
    for entry in _all_trials():
        name = f"{entry['scenario']}-{entry['trial']}"
        lines = [
            line
            for line in (ROUND / name / "events.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        assert len(lines) == entry["event_count"], f"{name} 的事件数与记录不符"


# ------------------------------------------------------------------- the human surfaces


def test_interactive_cases_attribute_their_decisions_to_the_automated_client() -> None:
    interactive = [scenario for scenario in scenarios.SCENARIOS if scenario.interactive]
    assert interactive, "场景里没有任何交互用例，这条断言就没有意义"
    for scenario in interactive:
        for entry in _case_trials(scenario.id):
            for decision in entry["decisions"]:
                assert decision["actor"] == scenarios.TEST_ACTOR, (
                    f"{scenario.id} 的交互动作没有标测试操作者"
                )


def test_d02_answered_the_supplement_before_anything_was_approved() -> None:
    scenario = scenarios.by_id("D02")
    assert scenario.supplements, "D02 没有声明补充内容"
    for entry in _case_trials("D02"):
        kinds = [item.get("interrupt_type") for item in entry["interrupts"]]
        assert kinds and kinds[0] == "order_info_supplement", (
            f"D02 应当先中断补充信息，实际是 {kinds}"
        )
        assert "hitl_approval" in kinds, f"D02 补充之后没有出现审批：{kinds}"
        assert entry["decisions"][0]["answer"] == {
            "supplement": scenario.supplements[0].payload()
        }
        assert entry["facts"].get("orders_added") == [
            str(scenario.numbers.get("total_amount") or "1275.00")
        ], f"D02 的订单对不上：{entry['facts']}"


def test_d03_rejected_then_approved_and_the_change_landed() -> None:
    for entry in _case_trials("D03"):
        approvals = [
            item
            for item in entry["interrupts"]
            if item.get("interrupt_type") == "hitl_approval"
        ]
        assert len(approvals) >= 2, f"D03 应当先被拒一次、再批一次，实际 {len(approvals)} 次"

        answers = [
            decision["answer"]["decisions"][0]["type"]
            for decision in entry["decisions"]
            if "decisions" in decision["answer"]
        ]
        assert answers[:2] == ["reject", "approve"], f"D03 的两次决策是 {answers}"

        approved = approvals[1]["candidates"][0]
        assert approved["tool_name"] == "order_update"
        assert approved["arguments"]["lines"][0]["quantity"] == 60

        order = entry["facts"].get("order") or {}
        assert order.get("version") == 2, f"批准之后订单没有变成 version=2：{order}"
        assert order.get("lines", [{}])[0].get("quantity") == 60


def test_d04_two_concurrent_approvals_produced_one_write() -> None:
    for entry in _case_trials("D04"):
        assert entry["facts"].get("race_statuses") == [409, 200], (
            f"D04 的并发批准应当一成一败，实际 {entry['facts'].get('race_statuses')}"
        )
        assert len(entry["facts"].get("orders_added") or []) == 1, (
            f"D04 只应产生一张订单：{entry['facts']}"
        )
        assert (entry["facts"].get("action") or {}).get("status") == "executed"


# ------------------------------------------------------------------------- the assets


def test_the_assets_passed_on_this_round() -> None:
    record = _assets()
    assert record["problems"] == []
    assert record["ok"] is True


@pytest.mark.parametrize("chart_type", ("bar", "line", "pie"))
def test_each_chart_is_a_real_png_with_a_matching_digest(chart_type: str) -> None:
    charts = {item["chart_type"]: item for item in _assets()["charts"]}
    chart = charts.get(chart_type)
    assert chart is not None, f"{chart_type} 图表没有被验证"
    assert chart["ok"] is True, f"{chart_type} 图表验证失败：{chart}"
    assert chart["is_png"] is True
    assert chart["digest_matches"] is True
    assert chart["size"] > 0


def test_the_search_evidence_names_the_engine_that_served_it() -> None:
    # ``dependencies.md`` forbids swapping the engine silently, and the way a reader checks
    # that is by finding the engine in the evidence: an unstated engine is indistinguishable
    # from one that was changed in the code.
    search = _assets()["search"]
    assert search["engine"], "搜索证据没有写明引擎"
    assert search["returned"] > 0
    assert search["samples"], "搜索没有留下可引用的样本"


def test_the_engine_in_the_evidence_is_the_one_in_the_code() -> None:
    from agent.tools.web_search import DEFAULT_ENGINE

    assert _assets()["search"]["engine"] == DEFAULT_ENGINE

    contract = (REPO_ROOT / "docs" / "plan" / "contracts" / "external.md").read_text(
        encoding="utf-8"
    )
    assert DEFAULT_ENGINE in contract, (
        f"contracts/external.md 没有写上 {DEFAULT_ENGINE}；"
        "文档、代码与证据三处不一致即为暗换引擎"
    )


def test_the_recorded_search_query_is_the_one_the_assets_module_runs() -> None:
    assert _assets()["search"]["query"] == live_assets.SEARCH_QUERY


def test_a_live_search_still_returns_something_citable() -> None:
    """The one live call in this suite: the engine is real and still answers with links.

    Everything else here reads evidence, because re-running the model would prove less than
    opening what it wrote. This is the exception, and it is cheap: the standard tier omits the
    ``link`` field more often than the pro tiers, which is the trade-off that made this engine
    a deliberate choice rather than a silent one.
    """
    from agent.env_utils import load_env
    from agent.tools.web_search import search

    key = load_env().get("ZHIPU_API_KEY", "")
    if not key:
        pytest.fail("ZHIPU_API_KEY 未配置；live 模式下这条检查不能跳过")

    response = search(live_assets.SEARCH_QUERY, api_key=key, count=5)
    assert response.engine == _assets()["search"]["engine"]
    assert response.results, "真实搜索没有返回任何可引用结果"
    assert all(item.link for item in response.results)


def test_the_two_viewports_rendered_without_overlap() -> None:
    record = _shots()
    assert record["problems"] == [], f"布局有问题：{record['problems']}"
    assert record["ok"] is True
    assert [item["viewport"] for item in record["viewports"]] == ["desktop", "mobile"]


@pytest.mark.parametrize("viewport", ("desktop", "mobile"))
def test_each_viewport_kept_its_screenshot_and_its_layout(viewport: str) -> None:
    entries = {item["viewport"]: item for item in _shots()["viewports"]}
    entry = entries.get(viewport)
    assert entry is not None, f"{viewport} 没有截图记录"
    assert entry["ok"] is True, f"{viewport} 的布局有问题：{entry['problems']}"
    assert entry["overflow_x"] <= 1, f"{viewport} 横向溢出 {entry['overflow_x']}px"
    assert entry["bytes"] > 0
    assert (ROUND / "screenshots" / entry["screenshot"]).is_file()
    assert entry["regions"], f"{viewport} 没有测量到任何可见区域"


# ------------------------------------------------------------- the definitions themselves


def test_the_scenario_definitions_are_self_consistent() -> None:
    assert scenarios.problems() == []
    assert len(CASES) == 8
    assert scenarios.all_expectation_count() >= 24


def test_the_round_ran_a_real_model() -> None:
    record = _preflight()
    assert record["live"] is True
    lowered = str(record["model_id"]).lower()
    hits = [marker for marker in DOUBLE_MARKERS if marker in lowered]
    assert not hits, f"这一轮用的是模型替身 {record['model_id']!r}（命中 {hits}）"
    assert record["model_host"], "没有记录模型主机"
    assert record["secrets_checked"] > 0


def test_the_rounds_cases_agree_with_the_contract_numbers() -> None:
    numbers = scenarios.contract_numbers()
    assert numbers, "合同数字是空的"
    expected = json.loads(
        (REPO_ROOT / "fixtures" / "expected-v1.json").read_text(encoding="utf-8")
    )
    assert str(expected["quote_comparison"]["total_amount"]) == numbers["quote_total"]
    assert str(expected["reorder_cost_summary"]["total_amount"]) == numbers["skill_total"]


def test_no_trial_is_a_bare_claim_without_its_stream() -> None:
    # Every trial's answer is reassembled *from the wire*, so a trial whose stream is missing
    # cannot be checked at all — the file would be an assertion about nothing.
    for directory in sorted(ROUND.glob("*-*")):
        if not (directory / "trial.json").is_file():
            continue
        assert (directory / "events.jsonl").is_file(), f"{directory.name} 没有事件流"
        payload: Mapping[str, Any] = json.loads(
            (directory / "trial.json").read_text(encoding="utf-8")
        )
        if payload.get("ok"):
            assert payload.get("answer"), f"{directory.name} 判为通过却没有留下回答"
