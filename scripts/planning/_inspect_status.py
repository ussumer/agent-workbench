"""Temporary gateway log status inspector."""
import json
import sys
from collections import Counter
from pathlib import Path

output = Path(sys.argv[1])
statuses = Counter()
models = Counter()
errors = Counter()
for line in (output / "model_calls.jsonl").open():
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        continue
    statuses[record.get("http_status")] += 1
    models[record.get("observed_model")] += 1
    if record.get("http_status") != 200:
        errors[str(record.get("error", record.get("body", "")))[:120]] += 1
print("http_status:", dict(statuses))
print("observed_model:", dict(models))
for message, count in errors.most_common(5):
    print("error x", count, ":", message)
