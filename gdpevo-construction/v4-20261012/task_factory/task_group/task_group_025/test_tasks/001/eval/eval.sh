#!/usr/bin/env bash
set -eu
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [ "$#" -ne 1 ]; then
  printf '{"error":"parse_or_control_failure","details":"one absolute submission path required"}\n'
  exit 2
fi
exec python3 "$SCRIPT_DIR/evaluate.py" "$1"
