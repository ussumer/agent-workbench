"""Temporary model-call log inspector: per-minute buckets and per-phase episode call sums."""
import json
import sys
from collections import Counter
from pathlib import Path

output = Path(sys.argv[1])
buckets = Counter()
total = 0
for line in (output / "model_calls.jsonl").open():
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        continue
    total += 1
    ts = record.get("started_at") or record.get("call", {}).get("started_at", "")
    buckets[ts[:16]] += 1
print("total calls:", total)
for bucket in sorted(buckets):
    print(bucket, buckets[bucket])
for phase in ("train", "reflect", "validation", "test"):
    base = output / phase
    if not base.is_dir():
        continue
    calls = 0
    episodes = 0
    retries = 0
    for result_path in base.rglob("result.json"):
        row = json.loads(result_path.read_text())
        calls += row.get("metrics", {}).get("episode_model_calls", 0)
        episodes += 1
        if "-retry-" in str(result_path):
            retries += 1
    print(phase, "episodes", episodes, "episode-calls", calls, "retry-dirs", retries)
