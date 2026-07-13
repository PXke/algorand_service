#!/usr/bin/env bash
# Emit JSON for `flutter build web --dart-define-from-file`.
# Reads FRONTEND_* env vars (set by deploy.sh / deploy.conf).
set -euo pipefail

OUT="${1:?output path}"
mkdir -p "$(dirname "$OUT")"

python3 - "$OUT" <<'PY'
import json
import os
import sys

out = sys.argv[1]
data = {
    "API_BASE_URL": os.environ.get("FRONTEND_API_BASE_URL", ""),
    "AUTH_DOMAIN": os.environ.get("FRONTEND_AUTH_DOMAIN", "localhost"),
    "ADMIN_WALLET_ADDRESSES": os.environ.get("FRONTEND_ADMIN_WALLETS", ""),
    "ALGOD_API_URL": os.environ.get(
        "FRONTEND_ALGOD_API_URL", "https://testnet-api.algonode.cloud"
    ),
    "WALLET_CONNECT_CHAIN_ID": os.environ.get("FRONTEND_WALLET_CHAIN_ID", "416002"),
    "EXPLORER_BASE_URL": os.environ.get(
        "FRONTEND_EXPLORER_BASE_URL", "https://testnet.explorer.perawallet.app"
    ),
    "WALLET_CONNECT_BRIDGE": os.environ.get(
        "FRONTEND_WALLET_CONNECT_BRIDGE", "https://wallet-connect-a.perawallet.app"
    ),
    "BUGSNAG_API_KEY": (
        os.environ.get("FRONTEND_BUGSNAG_API_KEY", "7712dd9a5b49cc654fd24ce23a18d0c3").strip()
        or "7712dd9a5b49cc654fd24ce23a18d0c3"
    ),
    "BUGSNAG_RELEASE_STAGE": os.environ.get(
        "FRONTEND_BUGSNAG_RELEASE_STAGE", "production"
    ),
}
with open(out, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")
PY
