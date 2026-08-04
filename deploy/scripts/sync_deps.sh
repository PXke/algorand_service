#!/usr/bin/env bash
# Refresh npm locks when deploy scope says package.json changed.
# Python deps are not locked — deploy installs live from pyproject.toml.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT="${REPO_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
BUILD_DIR="$REPO_ROOT/deploy/build"

DEPLOY_SYNC_NPM="${DEPLOY_SYNC_NPM:-0}"
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

main() {
  if [[ "$DEPLOY_SYNC_NPM" != "1" ]]; then
    exit 0
  fi
  _sync_npm
}

main
