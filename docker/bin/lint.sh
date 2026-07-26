#!/usr/bin/env bash
# Ruff + Vulture (same as CI / make lint).
set -euo pipefail

if [[ -d /app/backend ]]; then
  ROOT=/app
else
  ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi
cd "$ROOT"

if ! command -v ruff >/dev/null 2>&1; then
  pip install -q ruff vulture
fi

echo "ruff check..."
ruff check backend workers shared deploy/scripts

echo "ruff format --check..."
ruff format --check backend workers shared deploy/scripts

echo "vulture..."
vulture

echo "Lint OK."
