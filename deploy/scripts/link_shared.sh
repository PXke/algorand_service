#!/usr/bin/env bash
# Put shared/ on a virtualenv's import path via a .pth file.
#
# backend/ and workers/ are separate trees sharing one venv, so this is how
# `algorand_shared` becomes importable from both. A .pth beats `pip install`
# here: no build backend or package index is needed (the venvs are uv-managed
# and carry no setuptools), and the path stays valid across releases because
# releases/current is a stable directory.
#
# Usage: link_shared.sh <venv-dir> <shared-dir>
set -euo pipefail

VENV="${1:?venv dir}"
SHARED="${2:?shared dir}"

site=$("$VENV/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
printf '%s\n' "$(cd "$SHARED" && pwd)" > "$site/algorand_shared.pth"
"$VENV/bin/python" -c 'import algorand_shared' \
  || { echo "error: algorand_shared still not importable from $VENV" >&2; exit 1; }
echo ">>> shared/ linked into $VENV" >&2
