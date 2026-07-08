#!/usr/bin/env bash
# Emit nginx gzip_static / brotli_static siblings for a Flutter web tree.
# Intended to run on the fast build machine during package.sh; deploy.sh skips
# the remote pass when .precompress.sha256 is present and still valid.
set -euo pipefail

WEB_DIR="${1:?usage: precompress_web.sh WEB_DIR [brotli_q] [jobs]}"
BROTLI_Q="${2:-6}"
JOBS="${3:-$(nproc 2>/dev/null || echo 4)}"
STAMP="$WEB_DIR/.precompress.sha256"

_have_brotli() {
  command -v brotli >/dev/null 2>&1
}

_web_fingerprint() {
  find "$WEB_DIR" -type f \
    ! -name '*.gz' ! -name '*.br' ! -name '.precompress.sha256' -print0 \
    | sort -z | xargs -0 sha256sum 2>/dev/null | sha256sum | awk '{print $1}'
}

# Drop compressed siblings that are older than their source, or whose source
# no longer exists. Critical for deferred chunks: Flutter rebuilds rename/resize
# main.dart.js_N.part.js every build; small files (<1 KiB) are skipped by the
# compress pass, so a stale .gz would otherwise live forever and nginx
# gzip_static would serve it — dart2js then fails with
# "Success callback invoked but part … not loaded" (hash mismatch).
_purge_stale_compressed() {
  local f src
  while IFS= read -r -d '' f; do
    case "$f" in
      *.gz) src="${f%.gz}" ;;
      *.br) src="${f%.br}" ;;
      *) continue ;;
    esac
    if [[ ! -f "$src" ]] || [[ "$f" -ot "$src" ]]; then
      rm -f "$f"
    fi
  done < <(find "$WEB_DIR" -type f \( -name '*.gz' -o -name '*.br' \) -print0)
}

_compress_file() {
  local f="$1" q="$2" brotli_ok="$3"
  gzip -9 -kf "$f"
  if [[ "$brotli_ok" == 1 ]]; then
    brotli -q "$q" -f "$f" -o "$f.br"
  fi
}

main() {
  [[ -d "$WEB_DIR" ]] || { echo "error: not a directory: $WEB_DIR" >&2; exit 1; }
  local brotli_ok=0
  _have_brotli && brotli_ok=1

  # Always drop stale .gz/.br first — even when the fingerprint stamp matches,
  # an interrupted or size-filtered prior run can leave orphans.
  _purge_stale_compressed

  local fp
  fp=$(_web_fingerprint)
  if [[ -f "$STAMP" ]]; then
    local old_fp old_q
    old_fp=$(grep '^fingerprint=' "$STAMP" | cut -d= -f2)
    old_q=$(grep '^brotli_q=' "$STAMP" | cut -d= -f2)
    if [[ "$old_fp" == "$fp" && "$old_q" == "$BROTLI_Q" ]]; then
      echo ">>> frontend_web precompress unchanged — skipping ($WEB_DIR)" >&2
      return 0
    fi
  fi

  echo ">>> Precompressing frontend_web (brotli_q=${BROTLI_Q}, jobs=${JOBS})" >&2
  export -f _compress_file
  # Compress anything >= 200 bytes so tiny deferred .part.js chunks get fresh
  # .gz/.br (and replace any just-purged stale siblings).
  find "$WEB_DIR" -type f \
    \( -name '*.js' -o -name '*.wasm' -o -name '*.json' -o -name '*.html' \
       -o -name '*.css' -o -name '*.svg' -o -name '*.otf' -o -name '*.ttf' \) \
    -size +200c -print0 \
  | xargs -0 -P"$JOBS" -I{} bash -c '_compress_file "$1" "$2" "$3"' _ {} "$BROTLI_Q" "$brotli_ok"

  {
    echo "fingerprint=${fp}"
    echo "brotli_q=${BROTLI_Q}"
    echo "built_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } >"$STAMP"
}

main
