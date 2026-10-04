"""Assertions over one already completed live T46 session.

The test never calls the model. ``PLANNING_LIVE_SESSION`` must point at the
attempt produced by ``scripts/planning/live_baseline.py``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def result() -> dict:
    raw = os.environ.get("PLANNING_LIVE_SESSION")
    if not raw:
        pytest.fail("PLANNING_LIVE_SESSION is required; refusing to run a fake live test")
    path = Path(raw) / "result.json"
    if not path.is_file():
        pytest.fail(f"live result missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def test_live_uses_real_model_stack_and_budget(result):
    assert result["terminal_status"] == "completed"
    assert result["model_id"]
    assert result["model_calls"] > 0
    assert result["metrics"]["usage_complete"] is True
    assert result["metrics"]["reserved_upper_cny"] <= 2.0
    assert result["source_revision"]["files"]
    assert result["sandbox_boundary"]["containers"]


def test_live_initial_plan_is_independently_optimal_and_writes_orders(result):
    grade = result["initial_judge"]["grade"]
    assert grade["feasible"] and grade["optimal"] and grade["accepted"]
    assert result["initial_proposal"]["plan"]["lines"]
    assert result["orders"]
    assert result["erp_after"]


def test_live_revision_rejects_old_approval_and_reuses_episode_data(result):
    assert result["revision_http_status"] == 200
    assert result["stale_approval"]["status"] == 400
    assert "STALE_PLAN" in result["stale_approval"]["body"]
    assert result["goal_after_revision"]["data"]["problem"]["revision"] == 2
    assert result["episode_export"]["episode"]["thread_id"] == result["thread_id"]
    revision_execs = [e for e in result["kernel_executions"] if e["base_version"] >= 2]
    assert revision_execs
    assert any(e["read_names"] and "load_state" in e["code"] for e in revision_execs)
