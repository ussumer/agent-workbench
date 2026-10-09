"""Temporary: extract per-turn selections from a dynamic episode trace."""
import json
import sys

trace = json.loads(open(sys.argv[1]).read())
export = trace.get("episode_export", {})
events = export.get("events", export if isinstance(export, list) else [])
for event in events:
    if isinstance(event, dict) and event.get("kind") == "turn_grounded":
        print(event.get("payload", {}).get("selection"))
