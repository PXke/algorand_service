#!/usr/bin/env bash
# Build a versioned release tarball: backend + workers + schema + deploy
# tooling + compiled Flutter web (frontend_web/). Prints the archive path.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
OUT_DIR="$SCRIPT_DIR/build"
mkdir -p "$OUT_DIR"

SKIP_FRONTEND_BUILD="${SKIP_FRONTEND_BUILD:-0}"
# Frontend dart-defines (exported by deploy.sh): empty API base = same-origin.
FRONTEND_API_BASE_URL="${FRONTEND_API_BASE_URL:-}"
FRONTEND_AUTH_DOMAIN="${FRONTEND_AUTH_DOMAIN:-localhost}"
FRONTEND_ADMIN_WALLETS="${FRONTEND_ADMIN_WALLETS:-}"
FRONTEND_ALGOD_API_URL="${FRONTEND_ALGOD_API_URL:-https://testnet-api.algonode.cloud}"
FRONTEND_WALLET_CHAIN_ID="${FRONTEND_WALLET_CHAIN_ID:-416002}"
FRONTEND_EXPLORER_BASE_URL="${FRONTEND_EXPLORER_BASE_URL:-https://testnet.explorer.perawallet.app}"
# Pera's live WC v1 bridge (the official bridge.walletconnect.org is dead)
FRONTEND_WALLET_CONNECT_BRIDGE="${FRONTEND_WALLET_CONNECT_BRIDGE:-https://wallet-connect-a.perawallet.app}"

STAMP=$(date -u +%Y%m%d-%H%M%S)
GIT_SHA=$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)
GIT_BRANCH=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)
ARCHIVE="$OUT_DIR/algorand-platform-${STAMP}-${GIT_SHA}.tar.xz"
LATEST_LINK="$OUT_DIR/algorand-platform-latest.tar.xz"
BUILD_INFO="$OUT_DIR/BUILD_INFO-${STAMP}-${GIT_SHA}.txt"

if [[ "$SKIP_FRONTEND_BUILD" != "1" ]]; then
  command -v flutter >/dev/null 2>&1 || { echo "error: flutter not found (or set SKIP_FRONTEND_BUILD=1)" >&2; exit 1; }
  echo ">>> Building Flutter web (AUTH_DOMAIN=${FRONTEND_AUTH_DOMAIN})" >&2
  (
    cd "$REPO_ROOT/frontend_flutter"
    # --no-web-resources-cdn: bundle + serve CanvasKit from our own host (brotli'd
    # by nginx) instead of fetching it from gstatic — no dependency on Google CDN
    # reachability for visitors on constrained networks.
    flutter build web --release --no-web-resources-cdn \
      --dart-define=API_BASE_URL="$FRONTEND_API_BASE_URL" \
      --dart-define=AUTH_DOMAIN="$FRONTEND_AUTH_DOMAIN" \
      --dart-define=ADMIN_WALLET_ADDRESSES="$FRONTEND_ADMIN_WALLETS" \
      --dart-define=ALGOD_API_URL="$FRONTEND_ALGOD_API_URL" \
      --dart-define=WALLET_CONNECT_CHAIN_ID="$FRONTEND_WALLET_CHAIN_ID" \
      --dart-define=EXPLORER_BASE_URL="$FRONTEND_EXPLORER_BASE_URL" \
      --dart-define=WALLET_CONNECT_BRIDGE="$FRONTEND_WALLET_CONNECT_BRIDGE" \
      >&2
  )
fi
[[ -f "$REPO_ROOT/frontend_flutter/build/web/index.html" ]] \
  || { echo "error: no Flutter web build at frontend_flutter/build/web" >&2; exit 1; }

# IndexNow verification file: a static {key}.txt at the web root holding the key.
# Search engines fetch it to confirm we own the key the workers ping with.
if [[ -n "${INDEXNOW_KEY:-}" ]]; then
  printf '%s' "$INDEXNOW_KEY" > "$REPO_ROOT/frontend_flutter/build/web/${INDEXNOW_KEY}.txt"
  echo ">>> Wrote IndexNow key file: ${INDEXNOW_KEY}.txt" >&2
fi
# NOTE: .br/.gz siblings for nginx (brotli_static/gzip_static) are generated on
# the host after unpack (deploy.sh) — bundling them here would only bloat the
# transferred archive, since already-compressed files don't re-compress.

# Prune CanvasKit renderer variants we never serve. A JS (non---wasm) build uses
# only canvaskit.js/.wasm and the chromium/ variant; skwasm*, wimp and
# experimental_webparagraph belong to the --wasm/skwasm path. ~16 MB of dead
# weight otherwise. (Revisit if we ever switch to `flutter build web --wasm`.)
CK="$REPO_ROOT/frontend_flutter/build/web/canvaskit"
if [[ -d "$CK" ]]; then
  rm -rf "$CK/experimental_webparagraph"
  rm -f "$CK"/skwasm.js "$CK"/skwasm.wasm "$CK"/skwasm_heavy.js "$CK"/skwasm_heavy.wasm \
        "$CK"/wimp.js "$CK"/wimp.wasm
fi

cat >"$BUILD_INFO" <<EOF
stamp=${STAMP}
git_sha=${GIT_SHA}
git_branch=${GIT_BRANCH}
built_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
ci_run_id=${GITHUB_RUN_ID:-local}
EOF

cd "$REPO_ROOT"
# -J/xz with -9e -T0: ~64% smaller than gzip on this payload, decompresses fine
# on the host (tar -xaf auto-detects). *.symbols are WASM debug maps (~8 MB)
# that are never used at runtime — strip them.
XZ_OPT='-9e -T0' tar caf "$ARCHIVE" \
  --exclude='backend/.venv' \
  --exclude='backend/**/__pycache__' \
  --exclude='backend/.pytest_cache' \
  --exclude='backend/.ruff_cache' \
  --exclude='backend/*.egg-info' \
  --exclude='workers/.venv' \
  --exclude='workers/**/__pycache__' \
  --exclude='workers/.pytest_cache' \
  --exclude='workers/.ruff_cache' \
  --exclude='workers/*.egg-info' \
  --exclude='deploy/build' \
  --exclude='*.symbols' \
  --transform='s|^frontend_flutter/build/web|frontend_web|' \
  backend workers schema conduit/schema deploy frontend_flutter/build/web

cp "$BUILD_INFO" "$OUT_DIR/BUILD_INFO-latest.txt"
(
  cd "$OUT_DIR"
  sha256sum "$(basename "$ARCHIVE")" >"$(basename "$ARCHIVE").sha256"
)
ln -sf "$(basename "$ARCHIVE")" "$LATEST_LINK"
ln -sf "$(basename "$ARCHIVE").sha256" "${LATEST_LINK}.sha256"

echo "$ARCHIVE"
