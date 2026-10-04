"""Real repeated fixed-vs-curated challenge evaluation.

This is deliberately a thin orchestrator over the existing production live
runner. It never supplies a plan answer to the Actor and never edits old pilot
evidence. The same public cases, model, tool permissions and per-attempt budget
are used for both groups.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVAL = Path("/mnt/c/dev/rsi-eval")
for path in (ROOT / "src", EVAL):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from scripts.planning.evolution_pilot import call_curator, public_case, train_records
from scripts.planning.live_baseline import run


CASES = (
    ("deadline-budget", None),
    ("deadline-budget-missing", "P001"),
    ("business-infeasible", None),
    ("no-partial", None),
)
REPEATS = 2


def _summary(path: Path, *, group: str, case_id: str, repeat: int) -> dict:
    result = json.loads((path / "result.json").read_text(encoding="utf-8"))
    return {
        "group": group,
        "case_id": case_id,
        "repeat": repeat,
        "business_success": bool(result.get("business_success")),
        "terminal_status": result.get("terminal_status"),
        "model_calls": result.get("model_calls", 0),
        "cost_upper_cny": (result.get("metrics") or {}).get("reserved_upper_cny"),
        "errors": result.get("errors", []),
        "judge": result.get("final_judge"),
        "attempt": str(path),
    }


def _write_report(output: Path, groups: dict[str, list[dict]], *, curator: dict | None) -> Path:
    metrics = {}
    for group, rows in groups.items():
        successes = sum(row["business_success"] for row in rows)
        metrics[group] = {
            "attempts": len(rows),
            "successes": successes,
            "success_rate": successes / len(rows) if rows else 0.0,
            "all_repeats_success": all(
                all(row["business_success"] for row in rows if row["case_id"] == case)
                for case, _ in CASES
            ),
            "by_case": {
                case: {
                    "attempts": len([row for row in rows if row["case_id"] == case]),
                    "successes": sum(row["business_success"] for row in rows if row["case_id"] == case),
                }
                for case, _ in CASES
            },
        }
    fixed = metrics.get("fixed-v1", {})
    curated = metrics.get("curated-v1", {})
    report = {
        "schema_version": 1,
        "protocol": {"cases": [case for case, _ in CASES], "repeats": REPEATS,
                     "groups": ["fixed-v1", "curated-v1"],
                     "same_actor_and_budget": True},
        "curator": curator,
        "groups": groups,
        "metrics": metrics,
        "claims": {
            "real_actor_runs": True,
            "real_curator": curator is not None,
            "predeclared_success_improvement": curated.get("success_rate", 0.0) > fixed.get("success_rate", 0.0),
            "proves_learning_gain": False,
            "reason": "small repeated pilot; no statistical significance claim",
        },
    }
    path = output / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {
        p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in output.rglob("*") if p.is_file() and p.name != "evidence-manifest.json"
    }
    (output / "evidence-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path


def run_pilot(output: Path, *, group: str, repeats: int = REPEATS) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    existing = json.loads((output / "report.json").read_text(encoding="utf-8")) if (output / "report.json").exists() else {}
    if group in (existing.get("groups") or {}):
        raise ValueError(f"group already exists in session: {group}")
    curator = existing.get("curator")
    skill = None
    if group == "curated-v1":
        records = train_records()
        (output / "training-records.json").write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n")
        skill, curator = call_curator(records, output)
        curator = curator
    groups = dict(existing.get("groups") or {})
    groups[group] = []
    for case_id, mask_part in CASES:
        for repeat in range(1, repeats + 1):
            attempt = output / group / case_id / f"repeat-{repeat}"
            code = run(attempt, total_cny=50.0, per_attempt_cny=2.0,
                       experiment_group=group, skill_file=skill,
                       case_id=case_id, goal_data=public_case(case_id),
                       mask_source_part=mask_part)
            row = _summary(attempt, group=group, case_id=case_id, repeat=repeat)
            row["exit_code"] = code
            groups[group].append(row)
    return _write_report(output, groups, curator=curator)


def verify(session: Path) -> int:
    report = json.loads((session / "report.json").read_text(encoding="utf-8"))
    if report["protocol"]["cases"] != [case for case, _ in CASES]:
        raise ValueError("challenge case protocol changed")
    if report["protocol"]["repeats"] != REPEATS:
        raise ValueError("challenge repeat protocol changed")
    if set(report["groups"]) != {"fixed-v1", "curated-v1"}:
        raise ValueError("both fixed and curated groups are required")
    for group, rows in report["groups"].items():
        if len(rows) != len(CASES) * REPEATS:
            raise ValueError(f"{group} does not contain all repeated attempts")
        if {row["case_id"] for row in rows} != {case for case, _ in CASES}:
            raise ValueError(f"{group} does not cover all challenge cases")
        for row in rows:
            path = Path(row["attempt"])
            result = json.loads((path / "result.json").read_text(encoding="utf-8"))
            if bool(result.get("business_success")) != row["business_success"]:
                raise ValueError("summary/result business status mismatch")
            if not (path / "runtime-freeze.json").exists() or not (path / "episode_export.json").exists():
                # The live runner embeds Episode export in result; accept that canonical form.
                if "episode_export" not in result:
                    raise ValueError("attempt lacks Episode evidence")
    if not report["curator"] or report["curator"].get("model_calls", 0) < 1:
        raise ValueError("curated group lacks real Curator evidence")
    print(json.dumps({"session": str(session), "metrics": report["metrics"], "claims": report["claims"]}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run_cmd = sub.add_parser("run")
    run_cmd.add_argument("--output", type=Path, required=True)
    run_cmd.add_argument("--group", choices=["fixed-v1", "curated-v1"], required=True)
    run_cmd.add_argument("--repeats", type=int, default=REPEATS)
    verify_cmd = sub.add_parser("verify")
    verify_cmd.add_argument("--session", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "run":
        run_pilot(args.output, group=args.group, repeats=args.repeats)
        return 0
    return verify(args.session)


if __name__ == "__main__":
    raise SystemExit(main())
