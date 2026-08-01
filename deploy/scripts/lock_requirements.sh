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

# --extra-index-url pins torch to the CPU-only wheel (no bundled CUDA
# libraries) -- prod and dev are both CPU-only (see local_translate.py's own
# docstring), and the default PyPI torch build bundles multi-GB of unused
# NVIDIA runtime libraries. Must be passed to `pip install` at deploy time
# too (see deploy.sh) or the resolved `==X.Y.Z+cpu` version won't be found.
#
# --index-strategy unsafe-best-match is required alongside it: uv's default
# is to trust only the FIRST index that lists a given package name (a
# dependency-confusion guard), which silently downgraded UNRELATED packages
# (requests 2.34.2->2.28.1, urllib3 2.7.0->1.26.13) the first time this was
# tried without it, because the PyTorch index apparently also lists old
# copies of packages like requests. unsafe-best-match makes uv pick the best
# version across ALL configured indexes per-package instead -- safe here
# since both indexes (PyPI + PyTorch's own) are equally trusted, just not
# safe in general as a default for untrusted/private indexes.
PYTORCH_CPU_INDEX="https://download.pytorch.org/whl/cpu"

uv pip compile backend/pyproject.toml workers/pyproject.toml --extra ml \
  --extra-index-url "$PYTORCH_CPU_INDEX" --index-strategy unsafe-best-match \
  --python-version 3.14 --no-header \
  -o requirements.lock.txt

uv pip compile backend/pyproject.toml workers/pyproject.toml --extra ml --extra dev \
  --extra-index-url "$PYTORCH_CPU_INDEX" --index-strategy unsafe-best-match \
  --python-version 3.14 --no-header \
  -o requirements-dev.lock.txt

echo "wrote requirements.lock.txt and requirements-dev.lock.txt"
