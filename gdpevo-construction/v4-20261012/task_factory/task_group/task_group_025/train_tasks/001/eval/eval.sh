#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 1 ]]; then
  printf '{"error":"usage: bash eval.sh <submission.json>"}\n'
  exit 2
fi
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if command -v python3 >/dev/null 2>&1; then
  exec python3 "$SCRIPT_DIR/evaluate.py" "$1"
fi
exec python "$SCRIPT_DIR/evaluate.py" "$1"
