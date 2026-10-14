"""Read-only T67 final gate: test fixtures are not actual calibration/review evidence."""
import importlib.util
import json
from pathlib import Path
import pytest

SPEC = importlib.util.spec_from_file_location("t67_acceptance", Path(__file__).parents[2] / "scripts/planning/gdpevo_v4_acceptance.py")
acceptance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acceptance)
REVISION = "a" * 40

def fixture(tmp_path, monkeypatch, passes=5):
    output = tmp_path / "actual-run"
    output.mkdir()
    (output / "protocol.json").write_text(json.dumps({"task_group": str(tmp_path / "task_group_025")}))
    (output / "source-revision.json").write_text(json.dumps({"git_head": REVISION}))
    reports = tmp_path / "reviews"
    reports.mkdir()
    refs = []
    for number in range(6):
        name = f"reviewer-{number + 1}"
        passed = number < passes
        checks = {key: {"pass": True, "detail": "Independent evidence considered."} for key in acceptance.REVIEWER_CHECKS}
        checks["overall"]["pass"] = passed
        vote = {"reviewer_id": name, "clean_context": True, "decision": "pass" if passed else "fail",
                "checks": checks, "detail": "Independent review conclusion.",
                "calibration_output": str(output), "source_revision": REVISION}
        path = reports / (name + ".json")
        path.write_text(json.dumps(vote))
        refs.append({"reviewer_id": name, "path": path.name, "sha256": acceptance.digest(path)})
    report = {"task_group_id": "task_group_025", "calibration_output": str(output), "source_revision": REVISION,
              "script_check": {"pass": True, "detail": "Script check passed."},
              "review_votes": {"pass": passes >= 5, "pass_votes": passes, "fail_votes": 6 - passes, "detail": "Six independent votes."},
              "manual_review": {key: {"pass": True, "detail": "Reviewed summary."} for key in acceptance.SUMMARY_CHECKS},
              "reviewer_records": refs}
    review = reports / "task_group_025.yaml"
    review.write_text(json.dumps(report))
    monkeypatch.setattr(acceptance.runtime, "verify", lambda path: {"status": "calibration_passed", "calibration_complete": True})
    return output, review, report

def rewrite(path, value):
    path.write_text(json.dumps(value))

def test_final_gate_requires_real_calibration_before_votes(tmp_path, monkeypatch):
    output, review, _ = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(acceptance.runtime, "verify", lambda path: {"status": "blocked", "calibration_complete": False})
    result = acceptance.verify(output, review)
    assert result["status"] == "blocked"
    assert result["t67_complete"] is False

def test_five_of_six_independent_bound_votes_pass_readonly(tmp_path, monkeypatch):
    output, review, report = fixture(tmp_path, monkeypatch)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = acceptance.verify(output, review)
    assert result["status"] == "passed"
    assert result["t67_complete"] is True
    assert result["pass_votes"] == 5 and result["fail_votes"] == 1
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before

@pytest.mark.parametrize("mutation", ["missing_review", "wrong_run", "wrong_source", "wrong_group", "missing_vote",
                                      "changed_vote", "duplicate_reviewer", "duplicate_record", "not_clean", "raw_wrong_run",
                                      "raw_wrong_source", "missing_checks", "wrong_counts", "script_failed", "overall_failed"])
def test_final_gate_rejects_unbound_or_incomplete_review(tmp_path, monkeypatch, mutation):
    output, review, report = fixture(tmp_path, monkeypatch)
    raw_path = review.parent / report["reviewer_records"][0]["path"]
    if mutation == "missing_review":
        review.unlink()
    elif mutation == "wrong_run":
        report["calibration_output"] = str(tmp_path / "other-run")
    elif mutation == "wrong_source":
        report["source_revision"] = "b" * 40
    elif mutation == "wrong_group":
        report["task_group_id"] = "other-group"
    elif mutation == "missing_vote":
        raw_path.unlink()
    elif mutation == "changed_vote":
        raw_path.write_text(raw_path.read_text() + "\nchanged")
    elif mutation == "duplicate_reviewer":
        report["reviewer_records"][1]["reviewer_id"] = report["reviewer_records"][0]["reviewer_id"]
    elif mutation == "duplicate_record":
        report["reviewer_records"][1] = report["reviewer_records"][0].copy()
    elif mutation in {"not_clean", "raw_wrong_run", "raw_wrong_source", "missing_checks"}:
        raw = json.loads(raw_path.read_text())
        if mutation == "not_clean":
            raw["clean_context"] = False
        elif mutation == "raw_wrong_run":
            raw["calibration_output"] = str(tmp_path / "other-run")
        elif mutation == "raw_wrong_source":
            raw["source_revision"] = "b" * 40
        else:
            raw["checks"] = {}
        rewrite(raw_path, raw)
        report["reviewer_records"][0]["sha256"] = acceptance.digest(raw_path)
    elif mutation == "wrong_counts":
        report["review_votes"].update(pass_votes=6, fail_votes=0)
    elif mutation == "script_failed":
        report["script_check"]["pass"] = False
    elif mutation == "overall_failed":
        report["manual_review"]["overall"]["pass"] = False
    if review.exists():
        rewrite(review, report)
    result = acceptance.verify(output, review)
    assert result["status"] == "blocked"
    assert result["t67_complete"] is False

def test_four_pass_votes_never_pass_final_gate(tmp_path, monkeypatch):
    output, review, report = fixture(tmp_path, monkeypatch, passes=4)
    report["review_votes"]["pass"] = True
    rewrite(review, report)
    assert acceptance.verify(output, review)["status"] == "blocked"

def test_cli_blocked_result_has_nonzero_exit(tmp_path, monkeypatch, capsys):
    output, review, report = fixture(tmp_path, monkeypatch)
    report["script_check"]["pass"] = False
    rewrite(review, report)
    monkeypatch.setattr("sys.argv", ["gate", "--output", str(output), "--review", str(review)])
    with pytest.raises(SystemExit) as error:
        acceptance.main()
    assert error.value.code == 2
    assert json.loads(capsys.readouterr().out)["t67_complete"] is False

