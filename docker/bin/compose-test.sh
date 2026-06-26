#!/usr/bin/env bash
# Start platform deps (and optional profiles), then run lint + pytest in the test container.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PROFILES="${COMPOSE_PROFILES:-}"
WAIT="${COMPOSE_WAIT:-1}"

global=()
if [[ -n "$PROFILES" ]]; then
  for p in ${PROFILES//,/ }; do
    global+=(--profile "$p")
  done
fi

echo "Building shared platform image once (tag: ${PLATFORM_TAG:-local})..."
docker compose "${global[@]}" build migrate

echo "Starting compose (docker compose ${global[*]} up -d)..."
up_cmd=(up -d)
if [[ "$WAIT" == "1" ]]; then
  up_cmd+=(--wait)
fi
docker compose "${global[@]}" "${up_cmd[@]}"

echo "Running lint + backend pytest (in test container)..."
docker compose --profile test run --rm test

echo "Done."
