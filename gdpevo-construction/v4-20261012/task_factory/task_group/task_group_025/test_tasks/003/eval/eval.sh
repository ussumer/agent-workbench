#!/usr/bin/env bash
set -eu
if [ "$#" -ne 1 ]; then printf '%s\n' '{"error":"parse_or_control_failure","details":"Expected one absolute submission path"}'; exit 2; fi
case "$1" in /*) ;; *) printf '%s\n' '{"error":"parse_or_control_failure","details":"Submission path must be absolute"}'; exit 2;; esac
exec python3 "$(dirname "$0")/evaluate.py" "$1"
