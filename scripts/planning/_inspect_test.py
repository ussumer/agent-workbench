"""Temporary inspector: test rows status + loop content sample."""
import json
import sys
from pathlib import Path

output = Path(sys.argv[1])
for result_path in sorted((output / "test").rglob("result.json")):
    row = json.loads(result_path.read_text())
    print(row["arm"], row["task_id"], row["status"], "score", row["grade"]["score"],
          "calls", row["metrics"].get("episode_model_calls"),
          "in", row["metrics"]["input_tokens"], row.get("errors", [""])[0])

trace_path = output / "validation" / "skills" / "kits-train-04" / "compute-trace.json"
trace = json.loads(trace_path.read_text())
messages = trace.get("actor_messages", [])
print("kits-train-04 messages:", len(messages), "executions:", len(trace.get("executions", [])))
tool_names = [m.get("name") for m in messages if m.get("type") == "tool"]
from collections import Counter
print("tool histogram:", Counter(tool_names).most_common())
ai = [m for m in messages if m.get("type") == "ai"]
if ai:
    sample = ai[len(ai) // 2]
    print("mid ai content:", json.dumps(sample.get("content", ""), ensure_ascii=False)[:500])
    calls = sample.get("tool_calls") or []
    if calls:
        print("mid tool call args:", json.dumps(calls[0].get("args", {}), ensure_ascii=False)[:400])
last_tool = [m for m in messages if m.get("type") == "tool"]
if last_tool:
    print("last tool result:", json.dumps(last_tool[-1].get("content", ""), ensure_ascii=False)[:400])
