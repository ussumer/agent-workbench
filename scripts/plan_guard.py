#!/usr/bin/env python3
"""Read-only plan validation and task selection; not an application test runner."""

import argparse
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
STATUSES = {"pending", "in_progress", "blocked", "done"}
REVIEWS = {"not_reviewed", "accepted", "changes_requested"}
TASK_SECTIONS = (
    "## 目标", "## 前置与必读", "## 文件边界", "## 实现步骤",
    "## 验收条件", "## 检查命令", "## 失败处理", "## 完成证据",
)


def inside(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"Path escapes repository: {name}")
    return path


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def receipt_errors(root, task, receipt_name):
    errors = []
    tid = task["id"]
    if not isinstance(receipt_name, str) or not receipt_name:
        return [f"{tid}: done requires a receipt path"]
    try:
        path = inside(root, receipt_name)
        expected_dir = inside(root, f"artifacts/tasks/{tid}")
        if not path.is_relative_to(expected_dir) or path.name != "receipt.json":
            raise ValueError("receipt must be under its task evidence directory")
        receipt = read_json(path)
        if receipt.get("schema_version") != 1:
            errors.append(f"{tid}: invalid receipt schema_version")
        if receipt.get("task_id") != tid or receipt.get("status") != "passed":
            errors.append(f"{tid}: receipt is not a passed receipt for this task")
        for key in ("started_at", "finished_at", "source_revision"):
            if not receipt.get(key):
                errors.append(f"{tid}: receipt missing {key}")
        results = receipt.get("checks", [])
        if not isinstance(results, list):
            raise ValueError("receipt checks must be a list")
        by_id = {c["id"]: c for c in results}
        if len(by_id) != len(results):
            errors.append(f"{tid}: duplicate check result IDs")
        if set(by_id) != {c["id"] for c in task["checks"]}:
            errors.append(f"{tid}: receipt check set differs from task manifest")
        evidence_dir = path.parent.relative_to(root).as_posix()
        for spec in task["checks"]:
            if not spec.get("required"):
                continue
            cid = spec["id"]
            result = by_id.get(cid)
            if not result:
                errors.append(f"{tid}/{cid}: required check missing")
                continue
            if result.get("status") != "passed" or result.get("exit_code") != 0:
                errors.append(f"{tid}/{cid}: check did not pass")
            argv = [a.replace("{evidence_dir}", evidence_dir) for a in spec["argv"]]
            if result.get("argv") != argv or result.get("cwd") != spec["cwd"]:
                errors.append(f"{tid}/{cid}: executed command differs from manifest")
            if result.get("mode") != spec["mode"]:
                errors.append(f"{tid}/{cid}: check mode differs from manifest")
            log = inside(root, result.get("log", ""))
            if not log.is_relative_to(path.parent) or not log.is_file():
                errors.append(f"{tid}/{cid}: missing log in this attempt")
            elif result.get("log_sha256") != sha256(log):
                errors.append(f"{tid}/{cid}: log hash mismatch")
            if spec["kind"] == "test":
                report_name = spec["report"].replace("{evidence_dir}", evidence_dir)
                if result.get("report") != report_name:
                    errors.append(f"{tid}/{cid}: report path differs from manifest")
                report_path = inside(root, report_name)
                if report_path.stat().st_size > 8 * 1024 * 1024:
                    raise ValueError("JUnit report too large")
                cases = list(ET.parse(report_path).getroot().iter("testcase"))
                failed = sum(c.find("failure") is not None or c.find("error") is not None for c in cases)
                skipped = sum(c.find("skipped") is not None for c in cases)
                actual = (len(cases), failed, skipped)
                declared = tuple(result.get(k) for k in ("tests_total", "tests_failed", "tests_skipped"))
                if actual != declared:
                    errors.append(f"{tid}/{cid}: test counts differ from JUnit")
                if len(cases) < spec["min_tests"] or failed or skipped:
                    errors.append(f"{tid}/{cid}: insufficient, failed or skipped tests")
        artifacts = receipt.get("artifacts", [])
        if not isinstance(artifacts, list) or not artifacts:
            errors.append(f"{tid}: missing hashed evidence artifacts")
        else:
            hashed_paths = set()
            for artifact in artifacts:
                ap = inside(root, artifact["path"])
                if not ap.is_relative_to(path.parent) or not ap.is_file():
                    errors.append(f"{tid}: artifact missing from this attempt")
                elif sha256(ap) != artifact.get("sha256"):
                    errors.append(f"{tid}: artifact hash mismatch: {artifact['path']}")
                hashed_paths.add(artifact["path"])
            for spec in task["checks"]:
                if spec["kind"] == "test":
                    report = spec["report"].replace("{evidence_dir}", evidence_dir)
                    if report not in hashed_paths:
                        errors.append(f"{tid}: JUnit report not included in artifact hashes")
    except (OSError, ValueError, KeyError, TypeError, ET.ParseError) as exc:
        errors.append(f"{tid}: invalid receipt: {exc}")
    return errors


def validate(root):
    errors = []
    try:
        manifest = read_json(root / "docs/plan/tasks.json")
        state = read_json(root / "docs/plan/state.json")
        tasks = manifest["tasks"]
        if manifest.get("schema_version") != 1 or state.get("schema_version") != 1:
            errors.append("Unknown manifest/state schema")
        if manifest.get("plan_version") != state.get("plan_version"):
            errors.append("Manifest and state plan versions differ")
        by_id = {t["id"]: t for t in tasks}
        if len(by_id) != len(tasks):
            errors.append("Duplicate task IDs")
        if set(state["tasks"]) != set(by_id):
            errors.append("State task IDs differ from manifest")
        requirements = set()
        active = []
        for task in tasks:
            tid = task["id"]
            if not re.fullmatch(r"T\d{2}", tid):
                errors.append(f"Invalid task ID: {tid}")
            text = inside(root, task["file"]).read_text(encoding="utf-8")
            for section in TASK_SECTIONS:
                if section not in text:
                    errors.append(f"{tid}: missing section {section}")
            for ref in task["reads"]:
                if not inside(root, ref).is_file():
                    errors.append(f"{tid}: missing required reading {ref}")
            if not task["requirements"]:
                errors.append(f"{tid}: no requirement mapping")
            requirements.update(task["requirements"])
            for dep in task["depends_on"]:
                if dep not in by_id or dep == tid:
                    errors.append(f"{tid}: invalid dependency {dep}")
            if not task["checks"] or not any(c.get("required") for c in task["checks"]):
                errors.append(f"{tid}: no required gate")
            check_ids = [c["id"] for c in task["checks"]]
            if len(set(check_ids)) != len(check_ids):
                errors.append(f"{tid}: duplicate check IDs")
            for check in task["checks"]:
                if not check["argv"] or not all(isinstance(a, str) for a in check["argv"]):
                    errors.append(f"{tid}: invalid argv")
                inside(root, check["cwd"])
                if check["kind"] == "test" and check.get("min_tests", 0) < 1:
                    errors.append(f"{tid}: test count must be positive")
            row = state["tasks"].get(tid, {})
            if row.get("status") not in STATUSES or row.get("review") not in REVIEWS:
                errors.append(f"{tid}: invalid status/review")
            if row.get("status") == "in_progress":
                active.append(tid)
            if row.get("status") in {"done", "in_progress"}:
                for dep in task["depends_on"]:
                    if state["tasks"].get(dep, {}).get("status") != "done":
                        errors.append(f"{tid}: dependency {dep} is not done")
            if row.get("status") == "done":
                errors.extend(receipt_errors(root, task, row.get("receipt")))
        if len(active) > 1:
            errors.append(f"Multiple active tasks: {', '.join(active)}")
        visiting, visited = set(), set()

        def visit(tid):
            if tid in visiting:
                raise ValueError(f"Dependency cycle at {tid}")
            if tid in visited or tid not in by_id:
                return
            visiting.add(tid)
            for dep in by_id[tid]["depends_on"]:
                visit(dep)
            visiting.remove(tid)
            visited.add(tid)

        for tid in by_id:
            visit(tid)
        coverage = (root / "docs/plan/coverage.md").read_text(encoding="utf-8")
        documented = set(re.findall(r"^\| (R\d{2}) \|", coverage, re.M))
        if requirements != documented:
            errors.append("Coverage requirements differ from task mappings")
        for path in (root / "docs/plan").rglob("*.md"):
            content = path.read_text(encoding="utf-8")
            for raw in re.findall(r"\[[^\]\n]+\]\(([^)\n]+)\)", content):
                target = raw.strip("<>")
                if urlsplit(target).scheme or target.startswith("#"):
                    continue
                target = unquote(target.split("#", 1)[0])
                if not target:
                    continue
                resolved = (path.parent / target).resolve()
                if not resolved.is_relative_to(root.resolve()) or not resolved.exists():
                    errors.append(f"Broken local link in {path.relative_to(root)}: {target}")
        return errors, manifest, state
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return errors + [f"Invalid plan: {exc}"], None, None


def eligible(manifest, state):
    active = [t for t in manifest["tasks"] if state["tasks"][t["id"]]["status"] == "in_progress"]
    if active:
        return active
    return [t for t in manifest["tasks"]
            if state["tasks"][t["id"]]["status"] == "pending"
            and all(state["tasks"][d]["status"] == "done" for d in t["depends_on"])]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "next", "packet"])
    parser.add_argument("task", nargs="?")
    args = parser.parse_args(argv)
    errors, manifest, state = validate(ROOT)
    if errors:
        print("PLAN INVALID", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    if args.command == "check":
        done = sum(r["status"] == "done" for r in state["tasks"].values())
        print(f"Plan valid: {len(manifest['tasks'])} tasks; {done} done. Application checks are separate.")
    elif args.command == "next":
        ready = eligible(manifest, state)
        for t in ready:
            print(f"{t['id']}: {t['title']} -> {t['file']}")
        if not ready:
            print("No eligible pending task. Review blocked tasks or final acceptance.")
        blocked = [tid for tid, row in state["tasks"].items() if row["status"] == "blocked"]
        if blocked:
            print("Blocked (explicitly reopen after resolving): " + ", ".join(blocked))
    else:
        task = next((t for t in manifest["tasks"] if t["id"] == args.task), None)
        if task is None:
            parser.error("packet requires a known task ID, for example T00")
        for filename in [task["file"], *task["reads"]]:
            print(f"\n# Source: {filename}\n")
            print(inside(ROOT, filename).read_text(encoding="utf-8"))
        print("\n# Required check specification\n")
        print(json.dumps(task["checks"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
