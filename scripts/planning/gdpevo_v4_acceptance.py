"""Read-only final T67 gate: actual calibration plus six independently recorded Stage3 votes.

Raw vote JSON/YAML:
reviewer_id, clean_context: true, decision: pass|fail, detail,
calibration_output (absolute run directory), source_revision (full frozen git_head),
checks: {<all twelve REVIEWER_CHECKS>: {pass: bool, detail: nonempty string}}.

Official summary YAML additionally binds calibration_output/source_revision and
reviewer_records: [{reviewer_id, path (relative to report or absolute), sha256}].
This gate checks evidence consistency; clean_context is a reviewer attestation,
not a cryptographic proof of independent human/agent identity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.planning import gdpevo_v4_taskset as runtime

REVIEWER_CHECKS = (
    "scenario_lineage", "train_predict_design", "transfer_band", "environment_design",
    "environment_lifecycle", "environment_capabilities", "leakage_control",
    "notes_interpretability", "evaluation_design", "difficulty_calibration",
    "construction_process", "overall",
)
SUMMARY_CHECKS = (
    "leakage_check", "business_realism_check", "environment_design_check",
    "environment_lifecycle_check", "environment_capabilities_check", "transfer_design_check",
    "difficulty_check", "eval_design_check", "overall",
)

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def read(path):
    import yaml
    value = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("review record must be an object: " + str(path))
    return value

def conclusion(value, label, require_pass=False):
    if not isinstance(value, dict) or type(value.get("pass")) is not bool:
        raise ValueError(label + " requires a boolean pass")
    if not isinstance(value.get("detail"), str) or not value["detail"].strip():
        raise ValueError(label + " requires a nonempty detail")
    if require_pass and value["pass"] is not True:
        raise ValueError(label + " did not pass")

def binding(value, output, revision, label):
    path = value.get("calibration_output")
    if not isinstance(path, str) or not Path(path).is_absolute() or Path(path).resolve() != output:
        raise ValueError(label + " is bound to a different calibration output")
    if value.get("source_revision") != revision:
        raise ValueError(label + " source revision differs from frozen run")

def verify(output, review):
    output, review = Path(output).resolve(), Path(review).resolve()
    result = {"status": "blocked", "t67_complete": False, "learning_gain_proven": False,
              "calibration_output": str(output), "review": str(review), "reasons": []}
    try:
        calibration = runtime.verify(output)
        result["calibration"] = calibration
        if calibration.get("status") != "calibration_passed" or calibration.get("calibration_complete") is not True:
            raise ValueError("actual calibration is incomplete or failed")
        protocol = read(output / "protocol.json")
        source = read(output / "source-revision.json")
        revision = source.get("git_head")
        if not isinstance(revision, str) or not re.fullmatch("[a-f0-9]{40}", revision):
            raise ValueError("frozen run source revision missing or invalid")
        group = Path(protocol["task_group"]).name
        report = read(review)
        binding(report, output, revision, "summary")
        if report.get("task_group_id") != group:
            raise ValueError("summary task_group_id differs from actual calibration task group")
        conclusion(report.get("script_check"), "script_check", require_pass=True)
        summary = report.get("manual_review")
        if not isinstance(summary, dict):
            raise ValueError("official manual_review summary missing")
        for name in SUMMARY_CHECKS:
            conclusion(summary.get(name), "manual_review." + name, require_pass=name == "overall")
        votes = report.get("reviewer_records")
        if not isinstance(votes, list) or len(votes) != 6:
            raise ValueError("exactly six raw independent reviewer records are required")
        reviewers, paths, checked = set(), set(), []
        pass_votes = 0
        for reference in votes:
            if not isinstance(reference, dict):
                raise ValueError("reviewer reference must be an object")
            identity = reference.get("reviewer_id")
            if not isinstance(identity, str) or not identity.strip() or identity != identity.strip():
                raise ValueError("reviewer_id missing or malformed")
            if identity.casefold() in reviewers:
                raise ValueError("duplicate reviewer_id")
            reviewers.add(identity.casefold())
            relative = reference.get("path")
            if not isinstance(relative, str) or not relative:
                raise ValueError("raw reviewer record path missing")
            path = (review.parent / relative).resolve()
            if path in paths or path == review:
                raise ValueError("duplicate or summary-as-vote record")
            paths.add(path)
            expected_hash = reference.get("sha256")
            if not isinstance(expected_hash, str) or not re.fullmatch("[a-f0-9]{64}", expected_hash) or digest(path) != expected_hash:
                raise ValueError("raw reviewer record hash differs: " + identity)
            raw = read(path)
            if raw.get("reviewer_id") != identity or raw.get("clean_context") is not True:
                raise ValueError("raw reviewer identity/clean context attestation missing: " + identity)
            binding(raw, output, revision, identity)
            if raw.get("decision") not in {"pass", "fail"} or not isinstance(raw.get("detail"), str) or not raw["detail"].strip():
                raise ValueError("raw reviewer decision/detail invalid: " + identity)
            checks = raw.get("checks")
            if not isinstance(checks, dict):
                raise ValueError("official twelve reviewer checks missing: " + identity)
            for name in REVIEWER_CHECKS:
                conclusion(checks.get(name), identity + "." + name)
            passed = raw["decision"] == "pass"
            if checks["overall"]["pass"] is not passed:
                raise ValueError("raw reviewer decision contradicts overall check: " + identity)
            pass_votes += int(passed)
            checked.append({"reviewer_id": identity, "path": str(path), "sha256": expected_hash,
                            "decision": raw["decision"], "clean_context": True})
        summary_votes = report.get("review_votes")
        conclusion(summary_votes, "review_votes", require_pass=True)
        fail_votes = 6 - pass_votes
        if (type(summary_votes.get("pass_votes")) is not int or type(summary_votes.get("fail_votes")) is not int
                or summary_votes["pass_votes"] != pass_votes or summary_votes["fail_votes"] != fail_votes):
            raise ValueError("summary vote counts differ from six raw reviewer decisions")
        if pass_votes < 5:
            raise ValueError("fewer than five of six independent reviewers passed")
        result.update(status="passed", t67_complete=True, task_group_id=group, source_revision=revision,
                      source_revision_sha256=digest(output / "source-revision.json"),
                      review_sha256=digest(review), reviewer_records=checked,
                      pass_votes=pass_votes, fail_votes=fail_votes)
    except Exception as error:
        result["reasons"].append(type(error).__name__ + ": " + str(error)[:1000])
    return result

def main():
    parser = argparse.ArgumentParser(description="Read-only final T67 calibration and Stage3 gate")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    args = parser.parse_args()
    value = verify(args.output, args.review)
    print(json.dumps(value, ensure_ascii=False))
    if value["status"] != "passed":
        raise SystemExit(2)

if __name__ == "__main__":
    main()

