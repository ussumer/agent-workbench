"""Temporary token ledger comparison: gateway log vs episode metrics."""
import json
import sys
from collections import Counter
from pathlib import Path

output = Path(sys.argv[1])
gateway_in = gateway_out = 0
sizes = Counter()
for line in (output / "model_calls.jsonl").open():
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        continue
    usage = record.get("usage") or {}
    prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if not isinstance(prompt, int):
        continue
    gateway_in += prompt
    gateway_out += completion
    sizes[min(prompt // 2000 * 2, 20)] += 1
print("gateway tokens: in", gateway_in, "out", gateway_out)
print("prompt size histogram (k tokens -> count):", dict(sorted(sizes.items())))
episode_in = episode_out = episode_calls = 0
for phase in ("train", "reflect", "validation", "test"):
    base = output / phase
    if not base.is_dir():
        continue
    for result_path in base.rglob("result.json"):
        row = json.loads(result_path.read_text())
        metrics = row.get("metrics", {})
        episode_in += metrics.get("input_tokens", 0)
        episode_out += metrics.get("output_tokens", 0)
        episode_calls += metrics.get("episode_model_calls", 0)
print("episode tokens: in", episode_in, "out", episode_out, "calls", episode_calls)
print("ratio gateway/episode input:", round(gateway_in / max(1, episode_in), 3))
