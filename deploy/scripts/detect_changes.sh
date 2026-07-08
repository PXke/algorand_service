#!/usr/bin/env bash
# Infer deploy scope from git changes since the last successful production deploy.
# Prints human-readable plan to stderr and machine-readable exports to stdout:
#   eval "$(deploy/scripts/detect_changes.sh)"
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT="${REPO_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
BUILD_DIR="$REPO_ROOT/deploy/build"

DEPLOY_FORCE_FULL="${DEPLOY_FORCE_FULL:-0}"

log() { echo ">>> $*" >&2; }

_export() {
  printf 'export %s=%q\n' "$1" "$2"
}

_get_baseline_sha() {
  local remote=""
  if [[ -n "${SSH_SVC:-}" && -n "${TARGET_PATH:-}" ]]; then
    remote=$(
      ssh -o ConnectTimeout=8 -o BatchMode=yes "$SSH_SVC" \
        "grep '^git_sha=' '${TARGET_PATH}/releases/current/BUILD_INFO.txt' 2>/dev/null | cut -d= -f2" \
        2>/dev/null || true
    )
  fi
  if [[ -n "$remote" ]]; then
    echo "$remote"
    return
  fi
  if [[ -f "$BUILD_DIR/.last-deploy-sha" ]]; then
    cat "$BUILD_DIR/.last-deploy-sha"
  fi
}

_collect_changed_files() {
  local base="$1"
  if [[ -z "$base" ]]; then
    echo "__FULL_DEPLOY__"
    return
  fi
  if ! git -C "$REPO_ROOT" cat-file -e "${base}^{commit}" 2>/dev/null; then
    echo "__FULL_DEPLOY__"
    return
  fi
  if ! git -C "$REPO_ROOT" merge-base --is-ancestor "$base" HEAD 2>/dev/null; then
    echo "__FULL_DEPLOY__"
    return
  fi
  git -C "$REPO_ROOT" diff --name-only "$base"
}

_any_match() {
  local pat="$1"
  shift
  local f
  for f in "$@"; do
    [[ "$f" == __FULL_DEPLOY__ ]] && return 0
    [[ "$f" == $pat ]] && return 0
  done
  return 1
}

main() {
  mkdir -p "$BUILD_DIR"
  local base files=() full=0
  base=$(_get_baseline_sha)

  if [[ "$DEPLOY_FORCE_FULL" == "1" ]]; then
    log "Deploy scope: FULL (DEPLOY_FORCE_FULL=1)"
    full=1
  elif [[ -z "$base" ]]; then
    log "Deploy scope: FULL (no previous deploy baseline)"
    full=1
  else
    log "Deploy scope: changes since ${base}"
    while IFS= read -r line; do
      [[ -n "$line" ]] && files+=("$line")
    done < <(_collect_changed_files "$base")
    if _any_match "__FULL_DEPLOY__" "${files[@]}"; then
      log "Deploy scope: FULL (baseline not an ancestor)"
      full=1
    elif [[ ${#files[@]} -eq 0 ]]; then
      log "Deploy scope: no file changes since ${base} — shipping current tree as-is"
    fi
  fi

  local ch_front=0 ch_back=0 ch_workers=0 ch_schema=0 ch_pyproj=0 ch_pubspec=0 ch_deploy=0 ch_pylock=0

  if (( full )); then
    ch_front=1 ch_back=1 ch_workers=1 ch_schema=1 ch_pyproj=1 ch_deploy=1
  else
    _any_match "frontend_flutter/*" "${files[@]}" && ch_front=1
    _any_match "backend/*" "${files[@]}" && ch_back=1
    _any_match "workers/*" "${files[@]}" && ch_workers=1
    _any_match "schema/*" "${files[@]}" && ch_schema=1
    _any_match "conduit/schema/*" "${files[@]}" && ch_schema=1
    _any_match "backend/pyproject.toml" "${files[@]}" && ch_pyproj=1
    _any_match "workers/pyproject.toml" "${files[@]}" && ch_pyproj=1
    _any_match "requirements.lock.txt" "${files[@]}" && ch_pylock=1
    _any_match "frontend_flutter/pubspec.yaml" "${files[@]}" && ch_pubspec=1
    _any_match "frontend_flutter/pubspec.lock" "${files[@]}" && ch_pubspec=1
    _any_match "deploy/nginx/*" "${files[@]}" && ch_deploy=1
    _any_match "deploy/systemd/*" "${files[@]}" && ch_deploy=1
    _any_match "deploy/deploy.sh" "${files[@]}" && ch_deploy=1
    _any_match "deploy/package.sh" "${files[@]}" && ch_deploy=1
  fi

  # pyproject edits should refresh the lock before package; lock-only change skips compile.
  local sync_flutter=0 sync_python=0
  [[ "$ch_pubspec" == 1 ]] && _any_match "frontend_flutter/pubspec.yaml" "${files[@]}" && sync_flutter=1
  if [[ "$ch_pyproj" == 1 ]]; then
    sync_python=1
  fi

  local skip_front=0 precompress=0 skip_migrate=0
  if [[ "$ch_front" == 1 ]]; then
    skip_front=0
    precompress=1
  else
    skip_front=1
    precompress=0
  fi
  [[ "$ch_schema" == 1 || "$full" == 1 ]] && skip_migrate=0 || skip_migrate=1

  local restart_backend=0 restart_workers=0
  [[ "$ch_back" == 1 || "$ch_pylock" == 1 || "$full" == 1 ]] && restart_backend=1
  [[ "$ch_workers" == 1 || "$ch_pylock" == 1 || "$full" == 1 ]] && restart_workers=1

  # Show plan
  _yn() { [[ "$1" == 1 ]] && echo yes || echo skip; }
  if (( full )); then
    log "  frontend:  build + ship"
    log "  backend:   ship + restart"
    log "  workers:   ship + restart"
    log "  schema:    migrate"
  else
    if [[ ${#files[@]} -gt 0 && ${#files[@]} -le 12 ]]; then
      for f in "${files[@]}"; do log "  ~ $f"; done
    elif [[ ${#files[@]} -gt 12 ]]; then
      log "  ${#files[@]} paths changed"
    fi
    log "  frontend:  $(_yn "$ch_front") (flutter build + precompress)"
    log "  backend:   $(_yn "$ch_back") (ship$([[ "$restart_backend" == 1 ]] && echo ' + restart' || true))"
    log "  workers:   $(_yn "$ch_workers") (ship$([[ "$restart_workers" == 1 ]] && echo ' + restart' || true))"
    log "  schema:    $([[ "$skip_migrate" == 1 ]] && echo skip || echo migrate)"
    log "  nginx:     $(_yn "$ch_deploy")"
    if [[ "$sync_flutter" == 1 ]]; then log "  deps:      flutter pub upgrade (pubspec.yaml changed)"; fi
    if [[ "$sync_python" == 1 ]]; then log "  deps:      refresh requirements.lock.txt (pyproject changed)"; fi
    if [[ "$ch_front" == 1 && "$restart_backend" != 1 && "$restart_workers" != 1 ]]; then
      log "  restart:   none (static web only)"
    fi
  fi

  _export DEPLOY_BASELINE_SHA "${base:-}"
  _export DEPLOY_CHANGED_FRONTEND "$ch_front"
  _export DEPLOY_CHANGED_BACKEND "$ch_back"
  _export DEPLOY_CHANGED_WORKERS "$ch_workers"
  _export DEPLOY_CHANGED_SCHEMA "$ch_schema"
  _export DEPLOY_CHANGED_DEPLOY_CONFIG "$ch_deploy"
  _export DEPLOY_SYNC_FLUTTER "$sync_flutter"
  _export DEPLOY_SYNC_PYTHON "$sync_python"
  _export SKIP_FRONTEND_BUILD "$skip_front"
  _export PACKAGE_PRECOMPRESS "$precompress"
  _export DEPLOY_SKIP_MIGRATE "$skip_migrate"
  _export DEPLOY_RESTART_BACKEND "$restart_backend"
  _export DEPLOY_RESTART_WORKERS "$restart_workers"
}

main
