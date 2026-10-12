#!/bin/sh
set -eu
: "${ERP_SERVICE_TOKEN:?provide a unique per-run service token}"
: "${SOURCING_NETWORK:=gdpevo-group025-v4}"
: "${SOURCING_CONTAINER:=gdpevo-sourcing-025}"
: "${SOURCING_VOLUME:=gdpevo-sourcing-025-data}"
docker network inspect "$SOURCING_NETWORK" >/dev/null 2>&1 || docker network create "$SOURCING_NETWORK" >/dev/null
docker volume inspect "$SOURCING_VOLUME" >/dev/null 2>&1 || docker volume create "$SOURCING_VOLUME" >/dev/null
docker run -d --name "$SOURCING_CONTAINER" --network "$SOURCING_NETWORK" --network-alias task-env --mount "type=volume,src=$SOURCING_VOLUME,dst=/data" --env ERP_SERVICE_TOKEN --cap-drop ALL --security-opt no-new-privileges rush-harness/sourcing-env:group025-v4
