#!/usr/bin/env bash
# Wrapper: run CQL migrations using backend venv when available.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
BACKEND_VENV="$REPO_ROOT/backend/.venv"

if [[ -x "$BACKEND_VENV/bin/python" ]]; then
  exec "$BACKEND_VENV/bin/python" "$SCRIPT_DIR/cql_migrate.py" "$@"
fi

if command -v python3 >/dev/null 2>&1; then
  exec python3 "$SCRIPT_DIR/cql_migrate.py" "$@"
fi

echo "error: python3 not found" >&2
exit 1
