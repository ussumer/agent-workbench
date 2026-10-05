"""Zero-model checks for operation-level TRACE feedback packaging."""
import json
from pathlib import Path

import pytest

from scripts.planning.gdpevo_trace_feedback import PRIVATE_MARKERS, prepare, verify

ATTEMPT = Path("/mnt/c/dev/rsi-eval/procurement_eval/runs/planning-session-20261005/attempt-v3-tool-diagnostic-20261005")


@pytest.fixture(scope="module")
def package(tmp_path_factory):
    return prepare(tmp_path_factory.mktemp("trace-feedback") / "input", attempt=ATTEMPT)


def test_four_train_rows_and_two_arms(package):
    assert len(package["records"]) == 4
    assert {(r["task_id"], r["arm"]) for r in package["records"]} == {
        ("packages-train-04", "text"), ("packages-train-04", "compute"),
        ("kits-train-03", "text"), ("kits-train-03", "compute"),
    }


def test_compute_operations_are_completed_and_task_loaded(package):
    for row in package["records"]:
        if row["arm"] == "compute":
            assert row["feedback"]["persistent_task_loaded"] is True
            assert row["feedback"]["completed_operation_count"] == row["feedback"]["operation_count"]
            assert all(op["status"] == "completed" for op in row["operations"])


def test_text_rows_have_no_tool_trace(package):
    assert all("operations" not in row for row in package["records"] if row["arm"] == "text")


def test_versions_are_contiguous(package):
    for row in package["records"]:
        if row["arm"] == "compute":
            assert [op["base_version"] for op in row["operations"]] == list(range(len(row["operations"])))


def test_public_curator_input_has_no_private_markers(package):
    text = json.dumps(package, ensure_ascii=False).lower()
    assert all(marker not in text for marker in PRIVATE_MARKERS)
    assert all(row["task"]["split"] == "train" for row in package["records"])


def test_feedback_is_zero_model_and_no_learning_claim(package):
    assert package["model_calls"] == 0
    assert package["learning_gain_proven"] is False
    assert package["production_assignment_changed"] is False


def test_prepare_is_immutable(tmp_path, package):
    output = tmp_path / "input"
    prepare(output, attempt=ATTEMPT)
    with pytest.raises(FileExistsError):
        prepare(output, attempt=ATTEMPT)


def test_verify_replays_manifest(tmp_path, package):
    output = tmp_path / "input"
    prepare(output, attempt=ATTEMPT)
    assert verify(output)["status"] == "passed"
