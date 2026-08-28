#!/usr/bin/env bash
# Build a release tree and optionally a versioned tarball.
#
# Default (PACKAGE_OUTPUT=stage): assemble deploy/build/stage/ only — deploy.sh
# rsyncs this directly and skips the slow tar compress/decompress round-trip.
# PACKAGE_OUTPUT=archive|both: also emit a .tar.xz (CI release candidates).
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
OUT_DIR="$SCRIPT_DIR/build"
STAGE_DIR="$OUT_DIR/stage"
mkdir -p "$OUT_DIR"

PACKAGE_OUTPUT="${PACKAGE_OUTPUT:-stage}"
SKIP_FRONTEND_BUILD="${SKIP_FRONTEND_BUILD:-0}"
# Precompress .gz/.br on the build machine (parallel); deploy skips remote pass.
PACKAGE_PRECOMPRESS="${PACKAGE_PRECOMPRESS:-1}"
PACKAGE_PRECOMPRESS_JOBS="${PACKAGE_PRECOMPRESS_JOBS:-$(nproc 2>/dev/null || echo 4)}"
PACKAGE_BROTLI_QUALITY="${PACKAGE_BROTLI_QUALITY:-${DEPLOY_BROTLI_QUALITY:-9}}"
# xz level for archives only; -9e is ~8× slower than -3 for little extra gain on
# an already-compressed payload (JS/CSS). CI can override.
PACKAGE_XZ_LEVEL="${PACKAGE_XZ_LEVEL:-3}"

# Frontend Vite env (exported by deploy.sh): empty API base = same-origin.
FRONTEND_API_BASE_URL="${FRONTEND_API_BASE_URL:-}"
FRONTEND_AUTH_DOMAIN="${FRONTEND_AUTH_DOMAIN:-localhost}"
FRONTEND_ADMIN_WALLETS="${FRONTEND_ADMIN_WALLETS:-}"
FRONTEND_ALGOD_API_URL="${FRONTEND_ALGOD_API_URL:-}"
FRONTEND_WALLET_CHAIN_ID="${FRONTEND_WALLET_CHAIN_ID:-416002}"
FRONTEND_EXPLORER_BASE_URL="${FRONTEND_EXPLORER_BASE_URL:-https://testnet.explorer.perawallet.app}"
FRONTEND_WALLET_CONNECT_BRIDGE="${FRONTEND_WALLET_CONNECT_BRIDGE:-https://wallet-connect-a.perawallet.app}"
FRONTEND_BUGSNAG_API_KEY="${FRONTEND_BUGSNAG_API_KEY:-}"

STAMP=$(date -u +%Y%m%d-%H%M%S)
GIT_SHA=$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)
GIT_BRANCH=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)
ARCHIVE="$OUT_DIR/algorand-platform-${STAMP}-${GIT_SHA}.tar.xz"
LATEST_LINK="$OUT_DIR/algorand-platform-latest.tar.xz"
BUILD_INFO="$OUT_DIR/BUILD_INFO-${STAMP}-${GIT_SHA}.txt"

_frontend_build_hash() {
  {
    printf 'VITE_API_BASE_URL=%s\n' "$FRONTEND_API_BASE_URL"
    printf 'VITE_AUTH_DOMAIN=%s\n' "$FRONTEND_AUTH_DOMAIN"
    printf 'VITE_ADMIN_WALLETS=%s\n' "$FRONTEND_ADMIN_WALLETS"
    printf 'VITE_ALGOD_API_URL=%s\n' "$FRONTEND_ALGOD_API_URL"
    printf 'VITE_WALLET_CHAIN_ID=%s\n' "$FRONTEND_WALLET_CHAIN_ID"
    printf 'VITE_EXPLORER_BASE_URL=%s\n' "$FRONTEND_EXPLORER_BASE_URL"
    printf 'VITE_SUGGESTIONS_ENABLED=%s\n' "${FRONTEND_SUGGESTIONS_ENABLED:-false}"
    printf 'VITE_BUGSNAG_API_KEY=%s\n' "$FRONTEND_BUGSNAG_API_KEY"
    printf 'VITE_APP_VERSION=%s\n' "$GIT_SHA"
    find "$REPO_ROOT/frontend/src" "$REPO_ROOT/frontend/public" -type f 2>/dev/null | sort | xargs -r sha256sum
    sha256sum "$REPO_ROOT/frontend/index.html" "$REPO_ROOT/frontend/vite.config.ts" \
      "$REPO_ROOT/frontend/package.json" "$REPO_ROOT/frontend/package-lock.json"
    [[ -f "$REPO_ROOT/frontend/svelte.config.js" ]] && sha256sum "$REPO_ROOT/frontend/svelte.config.js"
  } | sha256sum | awk '{print $1}'
}

_write_vite_env() {
  # GIT_SHA isn't exported (only FRONTEND_* build-input vars are); pass it
  # through explicitly so write_vite_env.sh can stamp VITE_APP_VERSION for
  # correlating Bugsnag events with a deploy.
  GIT_SHA="$GIT_SHA" bash "$SCRIPT_DIR/scripts/write_vite_env.sh" "$REPO_ROOT/frontend/.env.production.local"
}

_maybe_build_frontend() {
  local stamp="$OUT_DIR/.frontend-build.sha256"
  local lock_stamp="$OUT_DIR/.npm-lock.sha256"
  local hash lock_hash
  local web="$REPO_ROOT/frontend/dist/index.html"

  if [[ "$SKIP_FRONTEND_BUILD" == "1" ]]; then
    [[ -f "$web" ]] || {
      echo "error: frontend skipped but no build at frontend/dist (deploy frontend once first)" >&2
      exit 1
    }
    echo ">>> Skipping Vite frontend build (no frontend changes)" >&2
    return 0
  fi

  hash=$(_frontend_build_hash)
  if [[ -f "$stamp" && "$(cat "$stamp")" == "$hash" && -f "$web" ]]; then
    echo ">>> Frontend inputs unchanged — skipping Vite build" >&2
    return 0
  fi

  command -v npm >/dev/null 2>&1 || {
    echo "error: npm not found (or set SKIP_FRONTEND_BUILD=1)" >&2
    exit 1
  }
  _write_vite_env

  lock_hash=$(sha256sum "$REPO_ROOT/frontend/package-lock.json" | awk '{print $1}')
  if [[ ! -f "$lock_stamp" || "$(cat "$lock_stamp")" != "$lock_hash" || ! -d "$REPO_ROOT/frontend/node_modules" ]]; then
    echo ">>> npm ci (package-lock changed or node_modules missing)" >&2
    (cd "$REPO_ROOT/frontend" && npm ci >&2)
    echo "$lock_hash" >"$lock_stamp"
  fi

  echo ">>> Building Vite SPA (AUTH_DOMAIN=${FRONTEND_AUTH_DOMAIN})" >&2
  (cd "$REPO_ROOT/frontend" && npm run build >&2)
  echo "$hash" >"$stamp"
}

_prune_web_build() {
  local web="$REPO_ROOT/frontend/dist"
  find "$web" -name '*.map' -delete 2>/dev/null || true
}

_web_tree_fingerprint() {
  local root="$1"
  find "$root" -type f \
    ! -name '*.gz' ! -name '*.br' ! -name '.precompress.sha256' ! -name '.fingerprint' \
    -print0 2>/dev/null | sort -z | xargs -0 sha256sum 2>/dev/null | sha256sum | awk '{print $1}'
}

_assemble_stage() {
  echo ">>> Assembling release stage" >&2
  rm -rf "$STAGE_DIR"
  mkdir -p "$STAGE_DIR/conduit"
  rsync -a \
    --exclude='.venv' --exclude='__pycache__' --exclude='.pytest_cache' \
    --exclude='.ruff_cache' --exclude='*.egg-info' \
    "$REPO_ROOT/backend" \
    "$REPO_ROOT/workers" \
    "$REPO_ROOT/shared" \
    "$REPO_ROOT/schema" \
    "$STAGE_DIR/"
  rsync -a "$REPO_ROOT/conduit/schema/" "$STAGE_DIR/conduit/schema/"
  rsync -a --exclude='build' "$REPO_ROOT/deploy/" "$STAGE_DIR/deploy/"

  local web_cache="$OUT_DIR/frontend_web_cache"
  local web_fp
  web_fp=$(_web_tree_fingerprint "$REPO_ROOT/frontend/dist")
  if [[ -f "$web_cache/.fingerprint" && "$(cat "$web_cache/.fingerprint")" == "$web_fp" ]]; then
    echo ">>> Reusing cached precompressed frontend_web" >&2
    rsync -a "$web_cache/" "$STAGE_DIR/frontend_web/"
  else
    rsync -a "$REPO_ROOT/frontend/dist/" "$STAGE_DIR/frontend_web/"
    if [[ "$PACKAGE_PRECOMPRESS" == "1" ]]; then
      bash "$REPO_ROOT/deploy/scripts/precompress_web.sh" \
        "$STAGE_DIR/frontend_web" "$PACKAGE_BROTLI_QUALITY" "$PACKAGE_PRECOMPRESS_JOBS"
    fi
    rm -rf "$web_cache"
    mkdir -p "$web_cache"
    rsync -a "$STAGE_DIR/frontend_web/" "$web_cache/"
    echo "$web_fp" >"$web_cache/.fingerprint"
  fi
}

_precompress_stage_web() {
  : # compression handled in _assemble_stage (or via frontend_web_cache)
}

_write_build_info() {
  cat >"$BUILD_INFO" <<EOF
stamp=${STAMP}
git_sha=${GIT_SHA}
git_branch=${GIT_BRANCH}
built_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
ci_run_id=${GITHUB_RUN_ID:-local}
EOF
  cp "$BUILD_INFO" "$OUT_DIR/BUILD_INFO-latest.txt"
  cp "$BUILD_INFO" "$STAGE_DIR/BUILD_INFO.txt"
}

_create_archive() {
  echo ">>> Compressing archive (xz -${PACKAGE_XZ_LEVEL})" >&2
  XZ_OPT="-${PACKAGE_XZ_LEVEL} -T0" tar caf "$ARCHIVE" \
    --exclude='deploy/build' \
    -C "$STAGE_DIR" \
    backend workers shared schema conduit deploy frontend_web BUILD_INFO.txt
  (
    cd "$OUT_DIR"
    sha256sum "$(basename "$ARCHIVE")" >"$(basename "$ARCHIVE").sha256"
  )
  ln -sf "$(basename "$ARCHIVE")" "$LATEST_LINK"
  ln -sf "$(basename "$ARCHIVE").sha256" "${LATEST_LINK}.sha256"
  _prune_old_archives
}

# Archive mode ran unpruned for months (2026-06 through 2026-07): 268
# numbered .tar.gz/.tar.xz archives accumulated to 3.1G in deploy/build with
# no retention at all -- nothing reads an old numbered archive once a newer
# one exists (rollback.sh works off the releases/current+previous symlink
# swap on the deploy target, never off these local build artifacts), so
# keeping more than a handful is pure disk waste. Retain the
# PACKAGE_ARCHIVE_RETAIN (default 5) most recent archives; older ones (and
# their .sha256 companions) are deleted. The "latest" symlink pair is
# excluded from the count/deletion by construction (glob below only matches
# timestamped names, never the plain "-latest" one).
_prune_old_archives() {
  local retain="${PACKAGE_ARCHIVE_RETAIN:-5}"
  local ext="${ARCHIVE##*.}"
  find "$OUT_DIR" -maxdepth 1 -type f -name "algorand-platform-*.${ext}" \
    ! -name "algorand-platform-latest.${ext}" -print0 \
    | xargs -0 -r ls -t \
    | tail -n "+$((retain + 1))" \
    | while IFS= read -r stale; do
        rm -f "$stale" "${stale}.sha256"
      done
}

_maybe_build_frontend
[[ -f "$REPO_ROOT/frontend/dist/index.html" ]] \
  || { echo "error: no Vite build at frontend/dist" >&2; exit 1; }

if [[ -n "${INDEXNOW_KEY:-}" ]]; then
  printf '%s' "$INDEXNOW_KEY" >"$REPO_ROOT/frontend/dist/${INDEXNOW_KEY}.txt"
  echo ">>> Wrote IndexNow key file: ${INDEXNOW_KEY}.txt" >&2
else
  echo ">>> warning: INDEXNOW_KEY unset — skipping IndexNow key file" >&2
fi

_prune_web_build
_assemble_stage
_precompress_stage_web
_write_build_info

case "$PACKAGE_OUTPUT" in
  stage)
    echo "$STAGE_DIR"
    ;;
  archive)
    _create_archive
    echo "$ARCHIVE"
    ;;
  both)
    _create_archive
    echo "$STAGE_DIR"
    ;;
  *)
    echo "error: PACKAGE_OUTPUT must be stage, archive, or both (got: ${PACKAGE_OUTPUT})" >&2
    exit 1
    ;;
esac
