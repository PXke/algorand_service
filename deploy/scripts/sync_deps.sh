#!/usr/bin/env bash
# Refresh npm/Python locks when deploy scope says manifests changed.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT="${REPO_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
BUILD_DIR="$REPO_ROOT/deploy/build"

DEPLOY_SYNC_NPM="${DEPLOY_SYNC_NPM:-0}"
DEPLOY_SYNC_PYTHON="${DEPLOY_SYNC_PYTHON:-0}"
# Back-compat if an old shell still exports the Flutter flag.
DEPLOY_SYNC_FLUTTER="${DEPLOY_SYNC_FLUTTER:-0}"
[[ "$DEPLOY_SYNC_FLUTTER" == "1" ]] && DEPLOY_SYNC_NPM=1

log() { echo ">>> $*" >&2; }

_sync_npm() {
  command -v npm >/dev/null 2>&1 || {
    echo "error: npm not found" >&2
    exit 1
  }
  local lock="$REPO_ROOT/frontend/package-lock.json"
  local before="" after=""
  [[ -f "$lock" ]] && before=$(sha256sum "$lock" | awk '{print $1}')
  log "npm install (package.json changed)"
  (cd "$REPO_ROOT/frontend" && npm install >&2)
  [[ -f "$lock" ]] && after=$(sha256sum "$lock" | awk '{print $1}')
  if [[ "$before" != "$after" ]]; then
    log "npm lock updated — commit package-lock.json"
    rm -f "$BUILD_DIR/.frontend-build.sha256" "$BUILD_DIR/frontend_web_cache/.fingerprint"
    export DEPLOY_CHANGED_FRONTEND=1
    export SKIP_FRONTEND_BUILD=0
    export PACKAGE_PRECOMPRESS=1
  fi
}

_sync_python() {
  command -v uv >/dev/null 2>&1 || {
    echo "warn: uv not found — skipping Python lock refresh" >&2
    return 0
  }
  local lock="$REPO_ROOT/requirements.lock.txt"
  local before="" after=""
  [[ -f "$lock" ]] && before=$(sha256sum "$lock" | awk '{print $1}')
  log "Python lock refresh (pyproject.toml changed)"
  uv pip compile "$REPO_ROOT/backend/pyproject.toml" "$REPO_ROOT/workers/pyproject.toml" \
    --python-version 3.14 --no-header --upgrade \
    -o "$lock" >&2
  [[ -f "$lock" ]] && after=$(sha256sum "$lock" | awk '{print $1}')
  if [[ "$before" != "$after" ]]; then
    log "Python lock updated — commit requirements.lock.txt"
  fi
}

main() {
  if [[ "$DEPLOY_SYNC_NPM" != "1" && "$DEPLOY_SYNC_PYTHON" != "1" ]]; then
    exit 0
  fi
  # Plain `if`, not `[[ ... ]] && _sync`: a false test at the END of this
  # function makes the function (and so the script) exit 1, which deploy.sh's
  # `set -e` reads as a failed step and aborts the whole deploy — silently,
  # since nothing printed an error. That fired whenever exactly one of the two
  # flags was set (an npm-only sync being the common case).
  if [[ "$DEPLOY_SYNC_NPM" == "1" ]]; then _sync_npm; fi
  if [[ "$DEPLOY_SYNC_PYTHON" == "1" ]]; then _sync_python; fi
}

main
