#!/usr/bin/env bash
# Recreate the shared deploy venv with a chosen Python (default: python3.15t).
#
# Usage (on the host, as SERVICE_USER):
#   PYTHON_BIN=python3.15t bash deploy/scripts/rebuild_venv.sh /home/guillaume/algorand-platform
#
# Or from the repo:
#   TARGET_PATH=/home/guillaume/algorand-platform ./deploy/scripts/rebuild_venv.sh
#
# Forces a full pip reinstall from backend/ + workers/[ml] pyprojects (no lock).
set -euo pipefail

TARGET_PATH="${1:-${TARGET_PATH:-}}"
[[ -n "$TARGET_PATH" ]] || { echo "usage: $0 <target-path>" >&2; exit 1; }

VENV="${TARGET_PATH}/venv"
SHARED="${TARGET_PATH}/shared"
CURRENT="${TARGET_PATH}/releases/current"

if [[ ! -d "$CURRENT" ]]; then
  echo "error: no release at ${CURRENT}" >&2
  exit 1
fi

if [[ -n "${PYTHON_BIN:-}" ]]; then
  :
elif [[ -x "${CURRENT}/deploy/scripts/select_python.sh" ]]; then
  PYTHON_BIN="$("${CURRENT}/deploy/scripts/select_python.sh")"
else
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  PYTHON_BIN="$("${SCRIPT_DIR}/select_python.sh")"
fi

command -v "$PYTHON_BIN" >/dev/null 2>&1 || {
  echo "error: ${PYTHON_BIN} not found on PATH" >&2
  exit 1
}

echo "Recreating venv with ${PYTHON_BIN}: $("$PYTHON_BIN" --version 2>&1)"
if "$PYTHON_BIN" -c "import sys; print('free-threaded:', not sys._is_gil_enabled())" 2>/dev/null; then
  :
fi

if [[ -d "$VENV" ]]; then
  echo "Removing ${VENV}"
  rm -rf "$VENV"
fi

"$PYTHON_BIN" -m venv "$VENV"

unset PYTHON_GIL
if "$VENV/bin/python" -c 'import sys; raise SystemExit(0 if "free-threading" in sys.version else 1)'; then
  export PYTHON_GIL=0
fi
export SCIPY_USE_PYTHRAN=0
{
  echo ""
  echo "# Free-threaded: keep GIL disabled (python3.15t)"
  echo "export PYTHON_GIL=\${PYTHON_GIL:-0}"
} >> "${VENV}/bin/activate"

"$VENV/bin/pip" install --quiet --upgrade pip setuptools wheel
"$VENV/bin/pip" install --upgrade --extra-index-url https://download.pytorch.org/whl/cpu \
  -e "${CURRENT}/backend" \
  -e "${CURRENT}/workers[ml]"

REQ_HASH="$(cat "${CURRENT}/backend/pyproject.toml" "${CURRENT}/workers/pyproject.toml" | sha256sum | cut -d' ' -f1)"
echo "$REQ_HASH" > "${SHARED}/.requirements.sha256"

bash "${CURRENT}/deploy/scripts/link_shared.sh" "$VENV" "${CURRENT}/shared"

if ! "$VENV/bin/python" -m playwright install chromium; then
  echo "WARN: playwright browser install FAILED — SPA scraping degraded until fixed"
fi

echo "Done. Venv python: $("$VENV/bin/python" --version 2>&1)"
