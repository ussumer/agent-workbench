"""Temporary inspector: bucket >=20k-token requests per minute and show largest prompts."""
import json
import sys
from collections import Counter
from pathlib import Path

output = Path(sys.argv[1])
buckets = Counter()
largest = []
count = 0
for line in (output / "model_calls.jsonl").open():
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        continue
    usage = record.get("usage") or {}
    prompt = usage.get("prompt_tokens")
    if not isinstance(prompt, int) or prompt < 20000:
        continue
    count += 1
    buckets[record.get("started_at", "")[:16]] += 1
    largest.append((prompt, record.get("started_at", ""), record.get("request_sha256", "")[:12]))
print("huge requests:", count)
for bucket in sorted(buckets):
    print(bucket, buckets[bucket])
print("largest:")
for prompt, ts, sha in sorted(largest, reverse=True)[:8]:
    print("  ", prompt, ts, sha)
