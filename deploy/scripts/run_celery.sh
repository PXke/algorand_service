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
    # --pool=solo, not the default prefork: confirmed live 2026-08-17 that
    # forking a child under this venv's free-threaded (no-GIL) Python build
    # crashes the child near-instantly on every single task (celery reports
    # "Worker exited prematurely"), 100% reproducible, near-zero memory
    # growth before the crash -- a fork()-vs-free-threading interaction bug,
    # same broader class as the from_pretrained ThreadPoolExecutor segfault
    # HF_DEACTIVATE_ASYNC_LOAD already works around (see local_translate.py).
    # Isolated with a controlled A/B: same binary, same code, only the GIL
    # setting changed -- re-enabling it (PYTHON_GIL=1) made prefork work
    # correctly too, but --pool=solo is the more targeted fix: this worker
    # already runs --concurrency=1 (translate_article_batch's own design --
    # only ONE local-model translation at a time, full stop), so prefork's
    # multi-process concurrency was never doing anything for it anyway.
    exec "${VENV}/bin/celery" -A app.celery_app worker --loglevel=INFO \
      -Q translate --concurrency=1 --hostname="translate@%h" --pool=solo
    ;;
  *)
    echo "usage: $0 worker|beat|translate" >&2
    exit 1
    ;;
esac
