#!/usr/bin/env bash
# Refresh Flutter/Python locks when deploy scope says manifests changed.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT="${REPO_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
BUILD_DIR="$REPO_ROOT/deploy/build"

DEPLOY_SYNC_FLUTTER="${DEPLOY_SYNC_FLUTTER:-0}"
DEPLOY_SYNC_PYTHON="${DEPLOY_SYNC_PYTHON:-0}"

log() { echo ">>> $*" >&2; }

_sync_flutter() {
  command -v flutter >/dev/null 2>&1 || {
    echo "error: flutter not found" >&2
    exit 1
  }
  local lock="$REPO_ROOT/frontend_flutter/pubspec.lock"
  local before="" after=""
  [[ -f "$lock" ]] && before=$(sha256sum "$lock" | awk '{print $1}')
  log "Flutter pub upgrade (pubspec.yaml changed)"
  (cd "$REPO_ROOT/frontend_flutter" && flutter pub upgrade >&2)
  [[ -f "$lock" ]] && after=$(sha256sum "$lock" | awk '{print $1}')
  if [[ "$before" != "$after" ]]; then
    log "Flutter lock updated — commit pubspec.lock"
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
  if [[ "$DEPLOY_SYNC_FLUTTER" != "1" && "$DEPLOY_SYNC_PYTHON" != "1" ]]; then
    exit 0
  fi
  [[ "$DEPLOY_SYNC_FLUTTER" == "1" ]] && _sync_flutter
  [[ "$DEPLOY_SYNC_PYTHON" == "1" ]] && _sync_python
}

main
