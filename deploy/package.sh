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
PACKAGE_BROTLI_QUALITY="${PACKAGE_BROTLI_QUALITY:-${DEPLOY_BROTLI_QUALITY:-6}}"
# xz level for archives only; -9e is ~8× slower than -3 for little extra gain on
# an already-compressed payload (WASM/JS). CI can override.
PACKAGE_XZ_LEVEL="${PACKAGE_XZ_LEVEL:-3}"

# Frontend dart-defines (exported by deploy.sh): empty API base = same-origin.
FRONTEND_API_BASE_URL="${FRONTEND_API_BASE_URL:-}"
FRONTEND_AUTH_DOMAIN="${FRONTEND_AUTH_DOMAIN:-localhost}"
FRONTEND_ADMIN_WALLETS="${FRONTEND_ADMIN_WALLETS:-}"
FRONTEND_ALGOD_API_URL="${FRONTEND_ALGOD_API_URL:-https://testnet-api.algonode.cloud}"
FRONTEND_WALLET_CHAIN_ID="${FRONTEND_WALLET_CHAIN_ID:-416002}"
FRONTEND_EXPLORER_BASE_URL="${FRONTEND_EXPLORER_BASE_URL:-https://testnet.explorer.perawallet.app}"
FRONTEND_WALLET_CONNECT_BRIDGE="${FRONTEND_WALLET_CONNECT_BRIDGE:-https://wallet-connect-a.perawallet.app}"

STAMP=$(date -u +%Y%m%d-%H%M%S)
GIT_SHA=$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)
GIT_BRANCH=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)
ARCHIVE="$OUT_DIR/algorand-platform-${STAMP}-${GIT_SHA}.tar.xz"
LATEST_LINK="$OUT_DIR/algorand-platform-latest.tar.xz"
BUILD_INFO="$OUT_DIR/BUILD_INFO-${STAMP}-${GIT_SHA}.txt"

_font_inputs_hash() {
  {
    sha256sum "$REPO_ROOT/frontend_flutter/tool/subset_fonts.py"
    find "$REPO_ROOT/frontend_flutter/assets/fonts-src" -type f 2>/dev/null | sort | xargs -r sha256sum
    find "$REPO_ROOT/frontend_flutter/lib/l10n" -name 'app_*.arb' 2>/dev/null | sort | xargs -r sha256sum
  } | sha256sum | awk '{print $1}'
}

_frontend_build_hash() {
  {
    printf 'API_BASE_URL=%s\n' "$FRONTEND_API_BASE_URL"
    printf 'AUTH_DOMAIN=%s\n' "$FRONTEND_AUTH_DOMAIN"
    printf 'ADMIN_WALLETS=%s\n' "$FRONTEND_ADMIN_WALLETS"
    printf 'ALGOD_API_URL=%s\n' "$FRONTEND_ALGOD_API_URL"
    printf 'WALLET_CHAIN_ID=%s\n' "$FRONTEND_WALLET_CHAIN_ID"
    printf 'EXPLORER_BASE_URL=%s\n' "$FRONTEND_EXPLORER_BASE_URL"
    printf 'WALLET_CONNECT_BRIDGE=%s\n' "$FRONTEND_WALLET_CONNECT_BRIDGE"
    # Font subset outputs (assets/fonts/) are derived from fonts-src + l10n —
    # tracked separately via _font_inputs_hash; including them here would
    # force a rebuild after every subset pass.
    find "$REPO_ROOT/frontend_flutter/lib" "$REPO_ROOT/frontend_flutter/web" \
      "$REPO_ROOT/frontend_flutter/assets" -type f ! -path '*/assets/fonts/*' \
      2>/dev/null | sort | xargs -r sha256sum
    sha256sum "$REPO_ROOT/frontend_flutter/pubspec.yaml" "$REPO_ROOT/frontend_flutter/pubspec.lock"
    _font_inputs_hash
  } | sha256sum | awk '{print $1}'
}

_maybe_subset_fonts() {
  local stamp="$OUT_DIR/.font-subset.sha256"
  local hash
  hash=$(_font_inputs_hash)
  if [[ -f "$stamp" && "$(cat "$stamp")" == "$hash" ]]; then
    echo ">>> Font inputs unchanged — skipping subset" >&2
    return 0
  fi
  echo ">>> Subsetting bundled fonts" >&2
  python3 "$REPO_ROOT/frontend_flutter/tool/subset_fonts.py" >&2
  echo "$hash" >"$stamp"
}

_pub_lock_hash() {
  sha256sum "$REPO_ROOT/frontend_flutter/pubspec.lock" | awk '{print $1}'
}

_write_flutter_defines() {
  bash "$SCRIPT_DIR/scripts/write_flutter_defines.sh" "$OUT_DIR/flutter_defines.json"
}

_maybe_build_frontend() {
  local stamp="$OUT_DIR/.frontend-build.sha256"
  local pub_stamp="$OUT_DIR/.pubspec-lock.sha256"
  local hash lock_hash defines="$OUT_DIR/flutter_defines.json"
  local web="$REPO_ROOT/frontend_flutter/build/web/index.html"
  local -a pub_flags=()

  if [[ "$SKIP_FRONTEND_BUILD" == "1" ]]; then
    [[ -f "$web" ]] || {
      echo "error: frontend skipped but no build at frontend_flutter/build/web (deploy frontend once first)" >&2
      exit 1
    }
    echo ">>> Skipping Flutter web build (no frontend changes)" >&2
    return 0
  fi

  hash=$(_frontend_build_hash)
  if [[ -f "$stamp" && "$(cat "$stamp")" == "$hash" && -f "$web" ]]; then
    echo ">>> Flutter inputs unchanged — skipping web build" >&2
    return 0
  fi

  command -v flutter >/dev/null 2>&1 || {
    echo "error: flutter not found (or set SKIP_FRONTEND_BUILD=1)" >&2
    exit 1
  }
  _maybe_subset_fonts
  _write_flutter_defines

  lock_hash=$(_pub_lock_hash)
  if [[ -f "$pub_stamp" && "$(cat "$pub_stamp")" == "$lock_hash" ]]; then
    pub_flags=(--no-pub)
    echo ">>> pubspec.lock unchanged — skipping pub get" >&2
  fi

  echo ">>> Building Flutter web (AUTH_DOMAIN=${FRONTEND_AUTH_DOMAIN})" >&2
  (
    cd "$REPO_ROOT/frontend_flutter"
    # --pwa-strategy=none: deprecated in Flutter 3.44 but still required to avoid
    # registering a caching SW (index.html also purges legacy SW registrations).
    flutter build web --release --wasm --no-web-resources-cdn \
      --pwa-strategy=none \
      -O4 \
      --no-source-maps \
      "${pub_flags[@]}" \
      --dart-define-from-file="$defines" \
      >&2
  )
  echo "$lock_hash" >"$pub_stamp"
  echo "$hash" >"$stamp"
}

_prune_web_build() {
  local ck="$REPO_ROOT/frontend_flutter/build/web/canvaskit"
  local web="$REPO_ROOT/frontend_flutter/build/web"
  if [[ -d "$ck" ]]; then
    rm -rf "$ck/experimental_webparagraph"
    rm -f "$ck"/wimp.js "$ck"/wimp.wasm
    rm -f "$ck"/skwasm_heavy.js "$ck"/skwasm_heavy.wasm
  fi
  find "$web" -name '*.map' -delete 2>/dev/null || true
  find "$web" -name '*.symbols' -delete 2>/dev/null || true
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
    "$REPO_ROOT/requirements.lock.txt" \
    "$REPO_ROOT/backend" \
    "$REPO_ROOT/workers" \
    "$REPO_ROOT/schema" \
    "$STAGE_DIR/"
  rsync -a "$REPO_ROOT/conduit/schema/" "$STAGE_DIR/conduit/schema/"
  rsync -a --exclude='build' "$REPO_ROOT/deploy/" "$STAGE_DIR/deploy/"

  local web_cache="$OUT_DIR/frontend_web_cache"
  local web_fp
  web_fp=$(_web_tree_fingerprint "$REPO_ROOT/frontend_flutter/build/web")
  if [[ -f "$web_cache/.fingerprint" && "$(cat "$web_cache/.fingerprint")" == "$web_fp" ]]; then
    echo ">>> Reusing cached precompressed frontend_web" >&2
    rsync -a "$web_cache/" "$STAGE_DIR/frontend_web/"
  else
    rsync -a "$REPO_ROOT/frontend_flutter/build/web/" "$STAGE_DIR/frontend_web/"
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
    requirements.lock.txt backend workers schema conduit deploy frontend_web BUILD_INFO.txt
  (
    cd "$OUT_DIR"
    sha256sum "$(basename "$ARCHIVE")" >"$(basename "$ARCHIVE").sha256"
  )
  ln -sf "$(basename "$ARCHIVE")" "$LATEST_LINK"
  ln -sf "$(basename "$ARCHIVE").sha256" "${LATEST_LINK}.sha256"
}

_maybe_build_frontend
[[ -f "$REPO_ROOT/frontend_flutter/build/web/index.html" ]] \
  || { echo "error: no Flutter web build at frontend_flutter/build/web" >&2; exit 1; }

if [[ -n "${INDEXNOW_KEY:-}" ]]; then
  printf '%s' "$INDEXNOW_KEY" >"$REPO_ROOT/frontend_flutter/build/web/${INDEXNOW_KEY}.txt"
  echo ">>> Wrote IndexNow key file: ${INDEXNOW_KEY}.txt" >&2
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
