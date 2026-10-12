#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 1 || "$1" != /* ]]; then
  printf '%s\n' '{"error":"Provide one absolute submission path."}'
  exit 2
fi
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/evaluate.py" "$1"
