"""Temporary per-episode validation inspector."""
import json
import sys
from pathlib import Path

output = Path(sys.argv[1])
rows = []
for result_path in sorted((output / "validation").rglob("result.json")):
    row = json.loads(result_path.read_text())
    rows.append((row["arm"], row["task_id"], row["status"], row["metrics"].get("episode_model_calls", 0),
                 round(row["grade"]["score"], 3), row.get("elapsed_seconds", 0)))
print("completed episodes:", len(rows))
for arm in ("fixed", "fewshot", "skills", "dynamic"):
    subset = [r for r in rows if r[0] == arm]
    if subset:
        calls = [r[3] for r in subset]
        print(arm, len(subset), "calls avg", round(sum(calls) / len(calls), 1), "max", max(calls),
              "unscored", sum(1 for r in subset if r[2] != "scored"),
              "success", sum(1 for r in subset if r[4] >= 1.0))
print("top-8 longest episodes:")
for row in sorted(rows, key=lambda r: -r[3])[:8]:
    print("  ", *row)
