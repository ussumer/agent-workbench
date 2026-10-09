"""Temporary: final usage tally for the stopped attempt."""
import json
import sys
from pathlib import Path

output = Path(sys.argv[1])
requests = 0
input_tokens = 0
output_tokens = 0
cached = 0
for line in (output / "model_calls.jsonl").open():
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        continue
    usage = record.get("usage") or {}
    prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if not isinstance(prompt, int):
        continue
    requests += 1
    input_tokens += prompt
    output_tokens += completion
    cached += (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
estimate = (input_tokens * 9 + output_tokens * 27) / 1_000_000
print("shared-gateway requests:", requests)
print("input tokens:", input_tokens, "cached:", cached)
print("output tokens:", output_tokens)
print("conservative estimate CNY:", round(estimate, 2))
runaway_in = runaway_calls = 0
for name in ("kits-train-01", "kits-train-04"):
    path = output / "validation" / "skills" / name / "result.json"
    row = json.loads(path.read_text())
    runaway_in += row["metrics"]["input_tokens"]
    runaway_calls += row["metrics"]["episode_model_calls"]
print("runaway two episodes: calls", runaway_calls, "input", runaway_in,
      "share of input", round(runaway_in / max(1, input_tokens) * 100, 1), "%")
