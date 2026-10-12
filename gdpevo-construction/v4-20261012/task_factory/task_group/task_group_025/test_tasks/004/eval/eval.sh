#!/usr/bin/env bash
set -eu
if [ "$#" -ne 1 ]; then
  printf '%s\n' '{"error":"parse_or_control_failure","details":"usage: eval.sh ABSOLUTE_ANSWER_PATH"}'
  exit 2
fi
script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec python3 "$script_dir/evaluate.py" "$1"
