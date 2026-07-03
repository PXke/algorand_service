#!/usr/bin/env bash
# Regenerate requirements.lock.txt / requirements-dev.lock.txt from
# backend/pyproject.toml + workers/pyproject.toml. Run this after changing
# either pyproject.toml, then commit the updated lock files.
#
# Requires uv (https://docs.astral.sh/uv/). Python version pinned to match
# CI/Docker (python:3.14-slim-bookworm, actions/setup-python 3.14).
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
cd "$REPO_ROOT"

command -v uv >/dev/null 2>&1 || { echo "error: uv not found (https://docs.astral.sh/uv/)" >&2; exit 1; }

uv pip compile backend/pyproject.toml workers/pyproject.toml \
  --python-version 3.14 --no-header \
  -o requirements.lock.txt

uv pip compile backend/pyproject.toml workers/pyproject.toml --extra dev \
  --python-version 3.14 --no-header \
  -o requirements-dev.lock.txt

echo "wrote requirements.lock.txt and requirements-dev.lock.txt"
