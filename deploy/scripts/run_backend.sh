#!/usr/bin/env bash
# Start the backend API under Gunicorn (gthread workers).
#
# Invoked by systemd (see deploy/systemd/algorand-platform-backend.service).
# Expects backend/.env (symlink to shared/backend.env) to be loaded by systemd.
set -euo pipefail

RELEASE_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VENV_BIN="$(cd "${RELEASE_ROOT}/../.." && pwd)/venv/bin"
cd "${RELEASE_ROOT}/backend"

# PYTHON_GIL=0 is only valid on free-threaded builds (python3.15t). On a
# normal GIL build it fatals at startup ("Disabling the GIL is not supported").
unset PYTHON_GIL
if "${VENV_BIN}/python" -c 'import sys; raise SystemExit(0 if "free-threading" in sys.version else 1)'; then
  export PYTHON_GIL=0
fi

WORKERS="${GUNICORN_WORKERS:-${APP_PROCESSES:-1}}"
# Threads default 1:1 with CPU cores (nproc). Override via GUNICORN_THREADS /
# APP_THREADS / APP_WORKERS when set.
_CPU_COUNT="$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)"
if [[ -n "${GUNICORN_THREADS:-}" ]]; then
  THREADS="$GUNICORN_THREADS"
elif [[ -n "${APP_THREADS:-}" ]]; then
  THREADS="$APP_THREADS"
elif [[ -n "${APP_WORKERS:-}" ]]; then
  THREADS="$APP_WORKERS"
else
  THREADS="$_CPU_COUNT"
fi
HOST="${APP_HOST:-127.0.0.1}"
PORT="${APP_PORT:-8080}"
TIMEOUT="${GUNICORN_TIMEOUT:-120}"

echo "gunicorn workers=${WORKERS} threads=${THREADS} (cpus=${_CPU_COUNT}, 1 thread↔1 core)" >&2

exec "${VENV_BIN}/gunicorn" app.falcon_main:app \
  --bind "${HOST}:${PORT}" \
  -k app.core.gunicorn_affinity.AffinityThreadWorker \
  --workers "${WORKERS}" \
  --threads "${THREADS}" \
  --timeout "${TIMEOUT}" \
  --graceful-timeout 30 \
  --access-logfile - \
  --error-logfile -
