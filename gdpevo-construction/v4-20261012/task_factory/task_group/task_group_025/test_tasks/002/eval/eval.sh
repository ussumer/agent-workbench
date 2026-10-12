#!/usr/bin/env bash
set -euo pipefail
if [ "$#" -ne 1 ]; then
  printf '%s\n' '{"error":"parse_or_control_failure","details":"Usage: eval.sh ABSOLUTE_ANSWER_JSON"}'
  exit 2
fi
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if command -v python3 >/dev/null 2>&1; then
  exec python3 "$SCRIPT_DIR/evaluate.py" "$1"
else
  exec python "$SCRIPT_DIR/evaluate.py" "$1"
fi
