#!/usr/bin/env bash
# Emit frontend/.env.production.local for Vite builds from FRONTEND_* env vars.
set -euo pipefail

OUT="${1:?output path}"
mkdir -p "$(dirname "$OUT")"

cat >"$OUT" <<EOF
VITE_API_BASE_URL=${FRONTEND_API_BASE_URL:-}
VITE_AUTH_DOMAIN=${FRONTEND_AUTH_DOMAIN:-localhost}
VITE_ADMIN_WALLET_ADDRESSES=${FRONTEND_ADMIN_WALLETS:-}
VITE_ALGOD_API_URL=${FRONTEND_ALGOD_API_URL:-}
VITE_WALLET_CONNECT_CHAIN_ID=${FRONTEND_WALLET_CHAIN_ID:-416002}
VITE_EXPLORER_BASE_URL=${FRONTEND_EXPLORER_BASE_URL:-https://testnet.explorer.perawallet.app}
VITE_SUGGESTIONS_ENABLED=${FRONTEND_SUGGESTIONS_ENABLED:-false}
EOF
