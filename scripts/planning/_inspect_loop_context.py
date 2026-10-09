"""Temporary: compare arms on the looped tasks and dump the kits skill bodies."""
import json
import sys
from pathlib import Path

output = Path(sys.argv[1])
for task_id in ("kits-train-01", "kits-train-04"):
    for arm in ("fixed", "fewshot", "skills", "dynamic"):
        base = output / "validation" / arm / task_id
        path = base / "actor" / "result.json" if arm == "dynamic" else base / "result.json"
        if not path.is_file():
            continue
        row = json.loads(path.read_text())
        print(task_id, arm, row["status"], "score", round(row["grade"]["score"], 3),
              "calls", row["metrics"].get("episode_model_calls"),
              "exec", row.get("trace", {}).get("completed_executions"))
bank = json.loads((output / "skills.json").read_text())
for skill in bank["kits"]:
    print("=== kits skill:", skill["skill_id"])
    print(skill["body"][:600])
