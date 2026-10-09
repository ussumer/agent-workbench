"""Temporary progress inspector for the full compute T64 attempt."""
import json
import sys
from pathlib import Path

output = Path(sys.argv[1])
records_path = output / "training-records.json"
if records_path.is_file():
    records = json.loads(records_path.read_text())
    fixed = [r["attempts"][0] for r in records]
    print("train fixed:", sum(1 for a in fixed if a["grade"]["business_success"]), "/",
          len(fixed), "success; status:", {s: sum(1 for a in fixed if a["status"] == s) for s in {a["status"] for a in fixed}})
    print("train repairs:", sum(1 for r in records if len(r["attempts"]) > 1),
          "repaired-success:", sum(1 for r in records if len(r["attempts"]) > 1 and r["attempts"][1]["grade"]["business_success"]))
for name in ("skills.json", "fewshot-skills.json", "validation.json", "candidate-selection.json"):
    print(name, "exists" if (output / name).is_file() else "-")
validation_path = output / "validation.json"
if validation_path.is_file():
    rows = json.loads(validation_path.read_text())
    by_arm = {}
    for row in rows:
        arm = by_arm.setdefault(row["arm"], {"n": 0, "success": 0, "score": 0.0, "failed": 0})
        arm["n"] += 1
        arm["success"] += bool(row["grade"]["business_success"])
        arm["score"] += row["grade"]["score"]
        arm["failed"] += row["status"] != "scored"
    for arm, stat in by_arm.items():
        print("validation", arm, stat["n"], "success", stat["success"],
              "mean", round(stat["score"] / max(1, stat["n"]), 4), "unscored", stat["failed"])
test_path = output / "test-results.json"
if test_path.is_file():
    rows = json.loads(test_path.read_text())
    print("test rows:", len(rows), "repeats:", sorted({r["repeat"] for r in rows}),
          "status:", {s: sum(1 for r in rows if r["status"] == s) for s in {r["status"] for r in rows}})
calls = output / "model_calls.jsonl"
if calls.is_file():
    print("model_calls:", sum(1 for _ in calls.open()))
