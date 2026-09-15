"""Tests for the delivered plan helper, not tests of the unbuilt application."""

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/plan_guard.py"
SPEC = importlib.util.spec_from_file_location("plan_guard", SCRIPT)
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


class PlanGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.plan = self.root / "docs/plan"
        self.plan.mkdir(parents=True)
        task = {
            "id": "T00", "file": "docs/plan/T00.md", "depends_on": [],
            "reads": ["docs/plan/spec.md"], "requirements": ["R01"],
            "checks": [{
                "id": "acceptance", "kind": "test", "mode": "unit", "required": True,
                "min_tests": 1, "argv": ["pytest", "test.py"], "cwd": ".",
                "report": "{evidence_dir}/report.xml",
            }],
        }
        other = copy.deepcopy(task)
        other.update(id="T01", file="docs/plan/T01.md", depends_on=["T00"], requirements=["R02"])
        self.manifest = {"schema_version": 1, "plan_version": "1.0", "tasks": [task, other]}
        self.state = {"schema_version": 1, "plan_version": "1.0", "tasks": {
            tid: {"status": "pending", "review": "not_reviewed", "receipt": None}
            for tid in ("T00", "T01")
        }}
        for tid in ("T00", "T01"):
            (self.plan / f"{tid}.md").write_text("\n".join(guard.TASK_SECTIONS), encoding="utf-8")
        (self.plan / "spec.md").write_text("# Spec\n", encoding="utf-8")
        (self.plan / "coverage.md").write_text("| R01 | one |\n| R02 | two |\n", encoding="utf-8")

    def write(self):
        for name, data in (("tasks.json", self.manifest), ("state.json", self.state)):
            (self.plan / name).write_text(json.dumps(data), encoding="utf-8")

    def errors(self):
        self.write()
        return guard.validate(self.root)[0]

    def make_receipt(self, report="<testsuite><testcase name='one'/></testsuite>"):
        folder = self.root / "artifacts/tasks/T00/fixture"
        folder.mkdir(parents=True, exist_ok=True)
        log = folder / "output.log"
        log.write_text("fixture only\n", encoding="utf-8")
        xml = folder / "report.xml"
        xml.write_text(report, encoding="utf-8")
        self.receipt = {
            "schema_version": 1, "task_id": "T00", "status": "passed",
            "started_at": "2026-09-16T00:00:00Z", "finished_at": "2026-09-16T00:00:01Z",
            "source_revision": {"fixture": True},
            "checks": [{
                "id": "acceptance", "mode": "unit", "status": "passed", "exit_code": 0,
                "argv": ["pytest", "test.py"], "cwd": ".", "tests_total": 1,
                "tests_failed": 0, "tests_skipped": 0,
                "log": log.relative_to(self.root).as_posix(), "log_sha256": guard.sha256(log),
                "report": xml.relative_to(self.root).as_posix(),
            }],
            "artifacts": [{"path": xml.relative_to(self.root).as_posix(), "sha256": guard.sha256(xml)}],
        }
        self.receipt_path = folder / "receipt.json"
        self.save_receipt()
        self.state["tasks"]["T00"].update(
            status="done", receipt=self.receipt_path.relative_to(self.root).as_posix(),
        )

    def save_receipt(self):
        self.receipt_path.write_text(json.dumps(self.receipt), encoding="utf-8")

    def test_valid_pending_plan_selects_first_task(self):
        self.assertEqual(self.errors(), [])
        self.assertEqual([t["id"] for t in guard.eligible(self.manifest, self.state)], ["T00"])

    def test_done_requires_receipt(self):
        self.state["tasks"]["T00"]["status"] = "done"
        self.assertIn("done requires", " ".join(self.errors()))

    def test_valid_receipt_unlocks_dependency(self):
        self.make_receipt()
        self.assertEqual(self.errors(), [])
        self.assertEqual([t["id"] for t in guard.eligible(self.manifest, self.state)], ["T01"])

    def test_blocks_premature_active_task(self):
        self.state["tasks"]["T01"]["status"] = "in_progress"
        self.assertIn("dependency T00 is not done", " ".join(self.errors()))

    def test_blocks_multiple_active_tasks(self):
        self.manifest["tasks"][1]["depends_on"] = []
        for row in self.state["tasks"].values():
            row["status"] = "in_progress"
        self.assertIn("Multiple active", " ".join(self.errors()))

    def test_cycle_is_rejected(self):
        self.manifest["tasks"][0]["depends_on"] = ["T01"]
        self.assertIn("cycle", " ".join(self.errors()))

    def test_broken_required_reading(self):
        (self.plan / "spec.md").unlink()
        self.assertIn("missing required reading", " ".join(self.errors()))

    def test_broken_local_link(self):
        (self.plan / "spec.md").write_text("[missing](missing.md)", encoding="utf-8")
        self.assertIn("Broken local link", " ".join(self.errors()))

    def test_path_escape_rejected(self):
        self.manifest["tasks"][0]["reads"] = ["../outside.md"]
        self.assertIn("escapes repository", " ".join(self.errors()))

    def test_required_section_is_checked(self):
        (self.plan / "T00.md").write_text("# Incomplete", encoding="utf-8")
        self.assertIn("missing section", " ".join(self.errors()))

    def test_coverage_mismatch(self):
        self.manifest["tasks"][0]["requirements"] = ["R99"]
        self.assertIn("Coverage requirements differ", " ".join(self.errors()))

    def test_log_tampering_rejected(self):
        self.make_receipt()
        (self.receipt_path.parent / "output.log").write_text("changed", encoding="utf-8")
        self.assertIn("log hash mismatch", " ".join(self.errors()))

    def test_zero_test_report_rejected(self):
        self.make_receipt("<testsuite/>")
        self.receipt["checks"][0]["tests_total"] = 0
        self.save_receipt()
        self.assertIn("insufficient", " ".join(self.errors()))

    def test_skipped_test_rejected(self):
        self.make_receipt("<testsuite><testcase name='one'><skipped/></testcase></testsuite>")
        self.receipt["checks"][0]["tests_skipped"] = 1
        self.save_receipt()
        self.assertIn("skipped tests", " ".join(self.errors()))

    def test_failed_test_rejected(self):
        self.make_receipt("<testsuite><testcase name='one'><failure/></testcase></testsuite>")
        self.receipt["checks"][0]["tests_failed"] = 1
        self.save_receipt()
        self.assertIn("failed or skipped", " ".join(self.errors()))

    def test_changed_command_rejected(self):
        self.make_receipt()
        self.receipt["checks"][0]["argv"] = ["true"]
        self.save_receipt()
        self.assertIn("command differs", " ".join(self.errors()))

    def test_changed_mode_rejected(self):
        self.make_receipt()
        self.receipt["checks"][0]["mode"] = "live"
        self.save_receipt()
        self.assertIn("mode differs", " ".join(self.errors()))

    def test_missing_required_check_rejected(self):
        self.make_receipt()
        self.receipt["checks"] = []
        self.save_receipt()
        self.assertIn("required check missing", " ".join(self.errors()))

    def test_missing_report_hash_rejected(self):
        self.make_receipt()
        self.receipt["artifacts"] = []
        self.save_receipt()
        self.assertIn("missing hashed evidence", " ".join(self.errors()))

    def test_blocked_task_not_selected_automatically(self):
        self.state["tasks"]["T00"]["status"] = "blocked"
        self.assertEqual(guard.eligible(self.manifest, self.state), [])


if __name__ == "__main__":
    unittest.main()
