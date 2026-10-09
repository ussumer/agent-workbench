"""Temporary inspector: sample looping episode executions from trace."""
import json
import sys
from pathlib import Path

trace_path = Path(sys.argv[1]) / "validation" / "skills" / "kits-train-04" / "compute-trace.json"
trace = json.loads(trace_path.read_text())
executions = trace.get("executions", [])
print("executions:", len(executions))
for index in (1, 2, 3, len(executions) // 2, len(executions) - 2, len(executions) - 1):
    if 0 <= index < len(executions):
        execution = executions[index]
        code = (execution.get("code") or "")[:220].replace("\n", " | ")
        print(f"--- #{index} status={execution.get('status')} read={execution.get('read_names')} write={execution.get('write_names')}")
        print("    code:", code)
        stdout = (execution.get("stdout") or execution.get("result") or "")
        print("    out:", str(stdout)[:200])
