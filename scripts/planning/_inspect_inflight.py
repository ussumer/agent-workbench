"""Temporary inspector: in-flight episodes (no result.json) and their trace shape."""
import json
import sys
from pathlib import Path

output = Path(sys.argv[1])
for phase in ("validation", "test"):
    base = output / phase
    if not base.is_dir():
        continue
    for trace_path in sorted(base.rglob("compute-trace.json")):
        directory = trace_path.parent
        if (directory / "result.json").is_file():
            continue
        trace = json.loads(trace_path.read_text())
        messages = trace.get("actor_messages", [])
        roles = [m.get("type", "?") for m in messages[-6:]] if messages else []
        tools = [m.get("name") for m in messages if m.get("type") == "tool"][-8:]
        sizes = [len(json.dumps(m, ensure_ascii=False)) for m in messages]
        print(phase, directory.relative_to(output),
              "messages", len(messages), "chars", sum(sizes),
              "executions", len(trace.get("executions", [])))
        print("   last roles:", roles, "last tools:", tools)
        if messages:
            last = json.dumps(messages[-1], ensure_ascii=False)[:400]
            print("   last message:", last)
