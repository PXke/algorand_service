#!/usr/bin/env bash
# Start Docker app stack in the background, then run Flutter web (foreground).
#
# Default: API + workers + Cassandra/Redis/Typesense; chain reads use **public TestNet**
# (https://testnet-api.algonode.cloud) — no local algod container.
#
#   ./docker/bin/dev-ui.sh
#   ./docker/bin/dev-ui.sh --localnet   # private algod on host :4001 (Sandbox-style)
#
# Options:
#   --localnet       Also start compose profile `localnet` (algod :4001/:4002)
#   --device NAME    flutter -d (default: chrome)
#   --docker-only    Start/wait for API only; do not launch Flutter
#   --stop-docker    Stop app (+ localnet) containers when this script exits
#
# Env: FLUTTER_DEVICE, API_URL (health check, default http://127.0.0.1:8080)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

LOCALNET=0
STOP_DOCKER=0
DOCKER_ONLY=0
FLUTTER_DEVICE="${FLUTTER_DEVICE:-chrome}"
API_URL="${API_URL:-http://127.0.0.1:8080}"

usage() {
  sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --localnet) LOCALNET=1 ;;
    --stop-docker) STOP_DOCKER=1 ;;
    --device)
      FLUTTER_DEVICE="${2:?--device requires a name}"
      shift
      ;;
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
  # Build the shared platform image once (backend/worker/beat share the same tag).
  echo "==> Building shared platform image..."
  docker compose build migrate

  if [[ "$LOCALNET" == "1" ]]; then
    echo "==> Docker: app + private localnet (algod http://127.0.0.1:4001)"
    docker compose --env-file docker/localnet/.env.example \
      --profile app --profile localnet up -d --wait
  else
    echo "==> Docker: app stack (chain via public TestNet — no local algod)"
    docker compose --profile app up -d --wait
  fi
}

wait_api() {
  local ready="${API_URL%/}/health/ready"
  echo "==> Waiting for API at ${ready} ..."
  for _ in $(seq 1 60); do
    if curl -sf "$ready" >/dev/null 2>&1; then
      echo "==> API ready: ${API_URL}"
      return 0
    fi
    sleep 2
  done
  echo "error: API not ready after 120s" >&2
  docker compose --profile app logs backend --tail 50 >&2 || true
  exit 1
}

run_flutter() {
  if ! command -v flutter >/dev/null 2>&1; then
    echo "error: flutter not in PATH (install SDK or use --docker-only)" >&2
    exit 1
  fi

  local -a defines=(
    "--dart-define=API_BASE_URL=${API_URL}"
    "--dart-define=AUTH_DOMAIN=localhost"
  )
  if [[ -n "${ADMIN_WALLET_ADDRESSES:-}" ]]; then
    defines+=("--dart-define=ADMIN_WALLET_ADDRESSES=${ADMIN_WALLET_ADDRESSES}")
  fi
  if [[ "$LOCALNET" == "1" ]]; then
    defines+=("--dart-define=ALGOD_API_URL=http://127.0.0.1:4001")
    echo "==> Flutter: ALGOD_API_URL=http://127.0.0.1:4001 (wallet chain id may still be TestNet 416002)"
  fi

  echo "==> Flutter: device=${FLUTTER_DEVICE} (Ctrl+C stops Flutter; Docker keeps running)"
  cd "$ROOT/frontend_flutter"
  flutter pub get
  # Port 5173 is listed in docker-compose CORS_ALLOWED_ORIGINS for the API.
  exec flutter run -d "$FLUTTER_DEVICE" --web-port=5173 "${defines[@]}"
}

start_stack
wait_api

if [[ "$DOCKER_ONLY" == "1" ]]; then
  echo "==> Docker running. Open ${API_URL} or run Flutter manually (see frontend_flutter/README.md)."
  # Disable trap stop unless requested — user may want stack to stay up
  trap - EXIT INT TERM
  exit 0
fi

run_flutter
