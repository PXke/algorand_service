#!/usr/bin/env bash
# Start Docker app stack in the background, then run the Vite SPA (foreground).
#
# Default: API + workers + Cassandra/Redis/Typesense; chain reads use **public TestNet**
# (https://testnet-api.algonode.cloud) — no local algod container.
#
#   ./docker/bin/dev-ui.sh
#   ./docker/bin/dev-ui.sh --localnet   # private algod on host :4001 (Sandbox-style)
#
# Options:
#   --localnet       Also start compose profile `localnet` (algod :4001/:4002)
#   --docker-only    Start/wait for API only; do not launch the SPA
#   --stop-docker    Stop app (+ localnet) containers when this script exits
#
# Env: API_URL (health check, default http://127.0.0.1:8080)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

LOCALNET=0
STOP_DOCKER=0
DOCKER_ONLY=0
API_URL="${API_URL:-http://127.0.0.1:8080}"

usage() {
  sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --localnet) LOCALNET=1 ;;
    --stop-docker) STOP_DOCKER=1 ;;
    --docker-only) DOCKER_ONLY=1 ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
  shift
done

stop_stack() {
  local profiles=(--profile app)
  if [[ "$LOCALNET" == "1" ]]; then
    profiles+=(--profile localnet)
  fi
  docker compose "${profiles[@]}" down
}

cleanup() {
  local code=$?
  if [[ "$STOP_DOCKER" == "1" ]]; then
    echo "Stopping Docker stack..."
    stop_stack || true
  fi
  exit "$code"
}
trap cleanup EXIT INT TERM

start_stack() {
  echo "==> Building shared platform image..."
  docker compose build migrate

  if [[ "$LOCALNET" == "1" ]]; then
    echo "==> Docker: app + private localnet (algod http://127.0.0.1:4001)"
    docker compose --env-file docker/localnet/.env.example \
      --profile app --profile localnet up -d --build
  else
    echo "==> Docker: app (TestNet algod via Algonode)"
    docker compose --profile app up -d --build
  fi
}

wait_api() {
  echo "==> Waiting for API at ${API_URL} ..."
  local i
  for i in $(seq 1 60); do
    if curl -sf "${API_URL}/health/ready" >/dev/null 2>&1 \
      || curl -sf "${API_URL}/health" >/dev/null 2>&1; then
      echo "==> API ready"
      return 0
    fi
    sleep 2
  done
  echo "error: API did not become ready at ${API_URL}" >&2
  exit 1
}

run_spa() {
  command -v npm >/dev/null 2>&1 || {
    echo "error: npm not in PATH (install Node 22+ or use --docker-only)" >&2
    exit 1
  }

  echo "==> Vite SPA on :5173 (Ctrl+C stops UI; Docker keeps running)"
  cd "$ROOT/frontend"
  if [[ ! -d node_modules ]]; then
    npm ci
  fi
  export VITE_API_BASE_URL="${API_URL}"
  export VITE_AUTH_DOMAIN=localhost
  if [[ -n "${ADMIN_WALLET_ADDRESSES:-}" ]]; then
    export VITE_ADMIN_WALLET_ADDRESSES="${ADMIN_WALLET_ADDRESSES}"
  fi
  if [[ "$LOCALNET" == "1" ]]; then
    export VITE_ALGOD_API_URL=http://127.0.0.1:4001
    echo "==> VITE_ALGOD_API_URL=http://127.0.0.1:4001"
  fi
  exec npm run dev -- --host 127.0.0.1 --port 5173
}

start_stack
wait_api

if [[ "$DOCKER_ONLY" == "1" ]]; then
  echo "==> Docker running. Open ${API_URL} or run the SPA (see frontend/README.md)."
  trap - EXIT INT TERM
  exit 0
fi

run_spa
