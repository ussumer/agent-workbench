"""Temporary inspector: print per-episode row summaries from a compute attempt directory."""
import json
import sys
from pathlib import Path

output = Path(sys.argv[1])
for row in json.loads((output / "rows.json").read_text()):
    print(row["arm"], row["task_id"], row["status"],
          "score", row["grade"]["score"],
          "exec", row.get("trace", {}).get("completed_executions"),
          "calls", row["metrics"].get("episode_model_calls"),
          "in", row["metrics"]["input_tokens"], "out", row["metrics"]["output_tokens"],
          "sel_in", row["metrics"].get("selector_input_tokens"))
    if row.get("errors"):
        print("  errors:", row["errors"][0], row["errors"][1][:400])
