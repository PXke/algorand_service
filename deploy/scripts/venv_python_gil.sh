#!/usr/bin/env bash
# Export PYTHON_GIL=0 only when the venv interpreter is a free-threaded build.
# Safe to source from systemd ExecStartPre or wrap Celery/Gunicorn launches.
#
# Usage:
#   source /path/to/venv_python_gil.sh /path/to/venv
#   exec "$VENV/bin/celery" ...
set -euo pipefail

VENV="${1:?venv dir}"
PYTHON="${VENV}/bin/python"
unset PYTHON_GIL
if [[ -x "$PYTHON" ]] && "$PYTHON" -c 'import sys; raise SystemExit(0 if "free-threading" in sys.version else 1)'; then
  export PYTHON_GIL=0
fi
