
import pytest
from scripts.planning.gdpevo_taskset import load_taskset
from scripts.planning.gdpevo_training import (
    curator_messages,
    evaluation_messages,
    train_feedback,
    train_messages,
    train_rubric,
)

pytestmark = pytest.mark.unit


def test_train_rejects_test_and_only_exposes_assigned_policies():
    public, _ = load_taskset()
    messages = train_messages(public, public["tasks"][0])
    assert "pit_stop每个物料本轮盈余最多1件" not in str(messages)
    assert "test-01" not in str(messages)
    with pytest.raises(ValueError):
        train_messages(public, public["tasks"][5])


def test_curator_rejects_test_record():
    public, _ = load_taskset()
    records = [{"task": t, "attempts": []} for t in public["tasks"][:5]]
    assert len(curator_messages(records)) == 2
    records[0]["task"] = public["tasks"][5]
    with pytest.raises(ValueError):
        curator_messages(records)


def test_feedback_contains_diagnostics_without_gold():
    task = load_taskset()[0]["tasks"][0]
    grade = {"business_success": False, "points": {task["task_id"] + "-allocation": False}}
    feedback = train_feedback(task, grade)
    assert "allocation" in feedback["failed_fields"]
    assert set(feedback) == {"success", "failed_fields", "feedback_kind"}
    assert "a-final" not in str(feedback)


def test_raw_and_curated_keep_same_base_actor_messages():
    public, _ = load_taskset()
    task = public["tasks"][5]
    raw = [{"task": t, "attempts": []} for t in public["tasks"][:5]]
    a = evaluation_messages(public, task, "retrieval-v1", raw)
    b = evaluation_messages(public, task, "curated-v1", {"body": "通用策略"})
    assert a[1] == b[1]
    assert a[0]["content"].split("冻结的训练经验")[0] == b[0]["content"].split("冻结的训练经验")[0]


def test_raw_test_leakage_rejected():
    public, _ = load_taskset()
    with pytest.raises(ValueError):
        evaluation_messages(
            public, public["tasks"][5], "retrieval-v1", [{"task": public["tasks"][6]}]
        )


def test_training_rubric_uses_same_six_output_fields():
    public, _ = load_taskset()
    rubric = train_rubric(public["tasks"][0])
    assert len(rubric) == 6 and sum(p["weight"] for p in rubric) == 15
    assert {p["field"] for p in rubric} == {
        "disposition",
        "allocation",
        "source_selection",
        "freight_cents",
        "commitment_ledger",
        "approval_request",
    }
