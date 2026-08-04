#!/usr/bin/env bash
# Celery entrypoint with free-threaded PYTHON_GIL handling.
#
# Usage (from systemd):
#   run_celery.sh worker|beat|translate
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RELEASE_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TARGET_ROOT="$(cd "${RELEASE_ROOT}/../.." && pwd)"
VENV="${TARGET_ROOT}/venv"
MODE="${1:?usage: $0 worker|beat|translate}"

# shellcheck source=venv_python_gil.sh
source "${SCRIPT_DIR}/venv_python_gil.sh" "${VENV}"

cd "${RELEASE_ROOT}/workers"
export PATH="${VENV}/bin:${PATH}"

case "$MODE" in
  worker)
    exec "${VENV}/bin/celery" -A app.celery_app worker --loglevel=INFO \
      -Q default,scrape,pipeline,chain,security --concurrency="${CELERY_CONCURRENCY:-4}"
    ;;
  beat)
    exec "${VENV}/bin/celery" -A app.celery_app beat --loglevel=INFO
    ;;
  translate)
    exec "${VENV}/bin/celery" -A app.celery_app worker --loglevel=INFO \
      -Q translate --concurrency=1 --hostname="translate@%h"
    ;;
  *)
    echo "usage: $0 worker|beat|translate" >&2
    exit 1
    ;;
esac
