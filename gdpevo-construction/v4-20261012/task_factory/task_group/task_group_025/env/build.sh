#!/bin/sh
set -eu
cd "$(dirname "$0")"
python3 generate_seed.py
(cd erp && sh mvnw -B -Dtest=SourcingApiTest package)
docker build -t rush-harness/sourcing-env:group025-v4 .
