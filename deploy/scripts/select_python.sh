#!/usr/bin/env bash
# Print the best available Python for the shared deploy venv.
# Prefers free-threaded builds (python3.15t) when installed on the host.
set -euo pipefail

if [[ -n "${PYTHON_BIN:-}" ]]; then
  echo "${PYTHON_BIN}"
  exit 0
fi

for candidate in python3.15t python3.15 python3.13t python3.13 python3; do
  if command -v "${candidate}" >/dev/null 2>&1; then
    echo "${candidate}"
    exit 0
  fi
done

echo python3
