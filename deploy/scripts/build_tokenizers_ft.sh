#!/usr/bin/env bash
# Build HuggingFace tokenizers for free-threaded CPython (python3.XXt).
#
# PyPI wheels enable PyO3 abi3, which free-threaded interpreters reject
# (SystemError: invalid PyModuleDef). HuggingFace's recipe is:
#   maturin build --no-default-features --features ext-module
#
# transformers<=5.14 still pins tokenizers<=0.23.0, so we stamp the wheel
# as 0.23.0 while building from tokenizers main (free-threaded support).
#
# Usage (on the host, with the platform venv active or VENV set):
#   ./deploy/scripts/build_tokenizers_ft.sh [/path/to/venv]
set -euo pipefail

VENV="${1:-${VENV:-}}"
if [[ -z "$VENV" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
  VENV="$(cd "$SCRIPT_DIR/../.." && pwd)/venv"
fi
PYTHON="${VENV}/bin/python"
PIP="${VENV}/bin/pip"
[[ -x "$PYTHON" ]] || { echo "error: no python at $PYTHON" >&2; exit 1; }

if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if "free-threading" in sys.version else 1)'; then
  echo "error: $PYTHON is not a free-threading build; use stock pip for tokenizers" >&2
  exit 1
fi

export PATH="${VENV}/bin:${HOME}/.cargo/bin:${PATH}"
export TMPDIR="${TMPDIR:-${HOME}/tmp}"
export TEMP="$TMPDIR" TMP="$TMPDIR"
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-${TMPDIR}/cargo-target}"
export PYTHON_GIL=0
export UNSAFE_PYO3_SKIP_VERSION_CHECK=1
mkdir -p "$TMPDIR" "$CARGO_TARGET_DIR"

"$PIP" install -q -U "maturin>=1.9.4"

SRC="${TMPDIR}/tokenizers-src"
WORKDIR="${TMPDIR}/tokenizers-ft-build"
if [[ ! -d "$SRC/.git" ]]; then
  git clone --depth 1 https://github.com/huggingface/tokenizers.git "$SRC"
else
  git -C "$SRC" fetch --depth 1 origin main
  git -C "$SRC" reset --hard origin/main
fi

rm -rf "$WORKDIR"
cp -a "$SRC" "$WORKDIR"
cd "$WORKDIR/bindings/python"

python3 - <<'PY'
import re
from pathlib import Path

for rel in ("pyproject.toml", "Cargo.toml"):
    path = Path(rel)
    text = path.read_text()
    patched = re.sub(r'(?m)^version = "0\.23\.[^"]+"', 'version = "0.23.0"', text, count=1)
    if patched == text:
        patched = re.sub(r'(?m)^version = "[^"]+"', 'version = "0.23.0"', text, count=1)
    path.write_text(patched)
    print(f"stamped {rel} -> 0.23.0")
PY

maturin build --release --interpreter "$PYTHON" \
  --no-default-features --features ext-module \
  -o "${WORKDIR}/wheels"

WHEEL="$(ls "${WORKDIR}/wheels"/tokenizers-*.whl | head -1)"
"$PIP" install --force-reinstall --no-deps "$WHEEL"
"$PIP" install -U --upgrade-strategy only-if-needed "transformers>=4.48.0"

PYTHON_GIL=0 "$PYTHON" -c 'import tokenizers, transformers, torch; print("ok", tokenizers.__version__, transformers.__version__, torch.__version__)'
echo ">>> free-threaded tokenizers + transformers ready in $VENV" >&2
