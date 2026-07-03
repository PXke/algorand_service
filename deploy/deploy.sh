#!/usr/bin/env bash
# Deployment entrypoint — packages, provisions, and ships the platform.
#
# Two-user model:
#   ROOT_USER    — apt packages, systemd units, nginx (privileged, default root)
#   SERVICE_USER — owns ${TARGET_PATH}, runs the services (unprivileged)
#
# Usage:
#   ./deploy/deploy.sh provision   # one-time host setup (apt, dirs, nginx)
#   ./deploy/deploy.sh deploy      # package + upload + install + restart (default)
#   ./deploy/deploy.sh status      # remote units + health snapshot
#
# Config comes from deploy/deploy.conf (sourced if present), overridable by env.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)

[[ -f "$SCRIPT_DIR/deploy.conf" ]] && source "$SCRIPT_DIR/deploy.conf"

TARGET_HOST="${TARGET_HOST:-}"
SERVICE_USER="${SERVICE_USER:-${TARGET_USER:-}}"
ROOT_USER="${ROOT_USER:-root}"
TARGET_PATH="${TARGET_PATH:-/srv/algorand-platform}"
SITE_DOMAIN="${SITE_DOMAIN:-}"
API_DOMAIN="${API_DOMAIN:-}"
CERTBOT_EMAIL="${CERTBOT_EMAIL:-}"
APP_PORT="${APP_PORT:-9080}"
DEPLOY_CQL_TIER="${DEPLOY_CQL_TIER:-all}"
DEPLOY_SKIP_MIGRATE="${DEPLOY_SKIP_MIGRATE:-0}"
DEPLOY_HEALTH_URL="${DEPLOY_HEALTH_URL:-http://127.0.0.1:${APP_PORT}/health/ready}"
DEPLOY_HEALTH_TIMEOUT_SEC="${DEPLOY_HEALTH_TIMEOUT_SEC:-120}"
DEPLOY_CONFIRM="${DEPLOY_CONFIRM:-0}"
# Algorand network: mainnet or testnet. Drives the frontend dart-defines
# (wallet login chain id, algod API, explorer). Backend/workers follow via
# ALGOD_URL in the shared env files on the host.
ALGORAND_NETWORK="${ALGORAND_NETWORK:-testnet}"
case "$ALGORAND_NETWORK" in
  mainnet)
    _ALGOD_URL_DEFAULT="https://mainnet-api.algonode.cloud"
    _CHAIN_ID_DEFAULT=416001
    _EXPLORER_DEFAULT="https://explorer.perawallet.app"
    ;;
  testnet)
    _ALGOD_URL_DEFAULT="https://testnet-api.algonode.cloud"
    _CHAIN_ID_DEFAULT=416002
    _EXPLORER_DEFAULT="https://testnet.explorer.perawallet.app"
    ;;
  *) echo "error: ALGORAND_NETWORK must be mainnet or testnet (got: ${ALGORAND_NETWORK})" >&2; exit 1 ;;
esac

# Frontend build inputs (consumed by package.sh)
export FRONTEND_API_BASE_URL="${FRONTEND_API_BASE_URL:-https://${API_DOMAIN}}"
export FRONTEND_AUTH_DOMAIN="${FRONTEND_AUTH_DOMAIN:-$SITE_DOMAIN}"
export FRONTEND_ADMIN_WALLETS="${FRONTEND_ADMIN_WALLETS:-}"
# IndexNow key file is emitted into the web build by package.sh (served static).
export INDEXNOW_KEY="${INDEXNOW_KEY:-}"
export FRONTEND_ALGOD_API_URL="${FRONTEND_ALGOD_API_URL:-$_ALGOD_URL_DEFAULT}"
export FRONTEND_WALLET_CHAIN_ID="${FRONTEND_WALLET_CHAIN_ID:-$_CHAIN_ID_DEFAULT}"
export FRONTEND_EXPLORER_BASE_URL="${FRONTEND_EXPLORER_BASE_URL:-$_EXPLORER_DEFAULT}"

UNITS=(algorand-platform-backend algorand-platform-celery algorand-platform-celery-beat)

SSH_SVC="${SERVICE_USER}@${TARGET_HOST}"
SSH_ROOT="${ROOT_USER}@${TARGET_HOST}"

die() { echo "error: $*" >&2; exit 1; }

require_config() {
  [[ -n "$TARGET_HOST" ]] || die "TARGET_HOST is required (set it in deploy/deploy.conf)"
  [[ -n "$SERVICE_USER" ]] || die "SERVICE_USER is required (set it in deploy/deploy.conf)"
  [[ -n "$SITE_DOMAIN" ]] || die "SITE_DOMAIN is required (set it in deploy/deploy.conf)"
  [[ -n "$API_DOMAIN" ]] || die "API_DOMAIN is required (set it in deploy/deploy.conf)"
  [[ "$TARGET_PATH" =~ ^/ ]] || die "TARGET_PATH must be absolute (got: ${TARGET_PATH})"
  command -v rsync >/dev/null 2>&1 || die "rsync is required"
}

confirm_gate() {
  if [[ "$DEPLOY_CONFIRM" != "1" ]]; then
    echo "Target: service=${SSH_SVC} root=${SSH_ROOT} path=${TARGET_PATH}"
    echo "Set DEPLOY_CONFIRM=1 to proceed."
    exit 1
  fi
}

render() { # render <file> — substitute deployment placeholders to stdout
  sed -e "s|@SERVICE_USER@|${SERVICE_USER}|g" \
      -e "s|@TARGET_PATH@|${TARGET_PATH}|g" \
      -e "s|@TARGET_HOST@|${TARGET_HOST}|g" \
      -e "s|@SITE_DOMAIN@|${SITE_DOMAIN}|g" \
      -e "s|@API_DOMAIN@|${API_DOMAIN}|g" \
      -e "s|@APP_PORT@|${APP_PORT}|g" \
      "$1"
}

# Adds our site file only — the host runs other vhosts that we never touch.
install_nginx_site() {
  # Stage the rendered config to a temp file first so the install logic (below)
  # has stdin free to run as its own script.
  render "$SCRIPT_DIR/nginx/algorand-platform.conf" \
    | ssh "$SSH_ROOT" "cat > /tmp/algorand-platform.conf.staged"
  ssh "$SSH_ROOT" bash -s <<'EOF'
set -euo pipefail
src=/tmp/algorand-platform.conf.staged
dest=/etc/nginx/sites-available/algorand-platform.conf
link=/etc/nginx/sites-enabled/algorand-platform.conf
# Degrade optional features this nginx may lack so they can't fail nginx -t and
# abort the deploy: HTTP/3 needs the QUIC build; brotli_static needs ngx_brotli.
if ! nginx -V 2>&1 | grep -q http_v3; then
  sed -i -e '/listen 443 quic/d' -e '/http3 on;/d' -e "/add_header Alt-Svc/d" "$src"
  echo "note: nginx has no HTTP/3 (http_v3) module — installed HTTP/2-only site" >&2
fi
# nginx -T dumps the FULL effective config (all includes + load_module lines),
# so it catches ngx_brotli whether it's loaded from nginx.conf, modules-enabled,
# or conf.d — unlike `nginx -V` (compile-time only) or grepping one dir.
if ! nginx -T 2>/dev/null | grep -qiE 'load_module[^;]*brotli'; then
  sed -i -e '/brotli_static on;/d' "$src"
  echo "note: nginx has no ngx_brotli module — serving gzip_static only" >&2
fi
# Install with rollback: keep the prior config and restore it if nginx -t fails,
# so a bad render can never leave the host with a broken (unreloadable) nginx.
backup=$(mktemp)
[ -f "$dest" ] && cp -a "$dest" "$backup"
mv "$src" "$dest"
ln -sf "$dest" "$link"
if nginx -t; then
  systemctl reload nginx
else
  echo "error: nginx -t failed — restoring previous site config" >&2
  if [ -s "$backup" ]; then cp -a "$backup" "$dest"; else rm -f "$dest" "$link"; fi
  rm -f "$backup"; exit 1
fi
rm -f "$backup"
EOF
}

# ---------------------------------------------------------------------------
cmd_provision() {
  confirm_gate
  echo ">>> [root] Installing apt packages (build deps for the venv)"
  ssh "$SSH_ROOT" bash -s <<'EOF'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# xz-utils: decompress the .tar.xz release. libnginx-mod-http-brotli: enables
# brotli_static in our nginx site (auto-loaded via /etc/nginx/modules-enabled).
apt-get install -y -qq python3 python3-venv python3-dev build-essential rsync curl xz-utils brotli
apt-get install -y -qq libnginx-mod-http-brotli \
  || echo "warn: libnginx-mod-http-brotli unavailable — install ngx_brotli before enabling brotli_static" >&2
EOF

  echo ">>> [root] Checking host services (this host already runs nginx/redis/cassandra)"
  ssh "$SSH_ROOT" 'systemctl is-active --quiet nginx redis-server cassandra' \
    || die "expected nginx, redis-server and cassandra active on the host"
  ssh "$SSH_ROOT" '(exec 3<>/dev/tcp/127.0.0.1/9042) 2>/dev/null' \
    || die "Cassandra CQL port 9042 not reachable"

  echo ">>> [root] Creating ${TARGET_PATH} owned by ${SERVICE_USER}"
  ssh "$SSH_ROOT" "mkdir -p '${TARGET_PATH}/releases' '${TARGET_PATH}/shared' && chown -R '${SERVICE_USER}:' '${TARGET_PATH}'"

  echo ">>> [root] Obtaining TLS certificate (${SITE_DOMAIN} + ${API_DOMAIN})"
  ssh "$SSH_ROOT" bash -s <<EOF
set -euo pipefail
if [[ ! -d "/etc/letsencrypt/live/${SITE_DOMAIN}" ]]; then
  # --account: the host has multiple LE accounts; pick the newest one.
  account=\$(ls -1t /etc/letsencrypt/accounts/acme-v02.api.letsencrypt.org/directory/ | head -1)
  certbot certonly --nginx --non-interactive --agree-tos \
    --account "\$account" --cert-name '${SITE_DOMAIN}' \
    -d '${SITE_DOMAIN}' -d '${API_DOMAIN}'
fi
EOF

  echo ">>> [root] Installing nginx site"
  install_nginx_site

  echo "Provisioning complete. Next: ./deploy/deploy.sh deploy"
}

# ---------------------------------------------------------------------------
cmd_deploy() {
  confirm_gate

  echo ">>> Packaging release"
  ARCHIVE=$("$SCRIPT_DIR/package.sh")
  CHECKSUM="${ARCHIVE}.sha256"
  [[ -f "$CHECKSUM" ]] || die "missing checksum file ${CHECKSUM}"
  # The .sha256 references the archive by basename — verify from its directory.
  (cd "$(dirname "$ARCHIVE")" && sha256sum -c "$(basename "$CHECKSUM")")

  ARCHIVE_NAME=$(basename "$ARCHIVE")
  RELEASES="${TARGET_PATH}/releases"
  CURRENT="${RELEASES}/current"
  PREVIOUS="${RELEASES}/previous"
  SHARED="${TARGET_PATH}/shared"
  VENV="${TARGET_PATH}/venv"

  echo ">>> Staging release locally"
  STAGE_LOCAL=$(mktemp -d)
  trap 'rm -rf "$STAGE_LOCAL"' EXIT
  tar -xaf "$ARCHIVE" -C "$STAGE_LOCAL"

  echo ">>> [${SERVICE_USER}] Syncing release (incremental — only changed files cross the wire)"
  # rsync the unpacked tree against the previous release (--link-dest): unchanged
  # files are hardlinked on the host and never transferred, so a redeploy ships
  # only the diff (often a few hundred KB instead of the whole archive). --partial
  # keeps progress across drops and the retry loop resumes on a flaky link; rsync
  # checksums every file, so the per-archive sha256 is no longer needed.
  # --chmod=D755: force directories world-traversable. The local stage is a
  # mktemp dir (mode 0700) and -a would otherwise propagate that to the release
  # root, leaving nginx (www-data) unable to stat() the files → 404s.
  attempt=0
  until rsync --archive --delete --partial --progress --chmod=D755 \
      --link-dest="$CURRENT" \
      "$STAGE_LOCAL/" "${SSH_SVC}:${RELEASES}/staging/"; do
    attempt=$((attempt + 1))
    (( attempt >= 5 )) && die "sync failed after ${attempt} attempts"
    echo ">>> sync interrupted — resuming (attempt $((attempt + 1)))"
    sleep 3
  done

  echo ">>> [${SERVICE_USER}] Installing release + venv + shared env"
  ssh "$SSH_SVC" bash -s <<EOF
set -euo pipefail
mkdir -p '${RELEASES}' '${SHARED}'

# Atomic swap: the just-synced staging tree becomes current; the prior current
# is kept as previous (rollback target). Hardlinks from --link-dest mean current
# and previous share unchanged files on disk.
[[ -d '${RELEASES}/staging' ]] || { echo 'error: staging dir missing after sync'; exit 1; }
rm -rf '${PREVIOUS}'
[[ -e '${CURRENT}' ]] && mv '${CURRENT}' '${PREVIOUS}'
mv '${RELEASES}/staging' '${CURRENT}'

# Precompress web assets so nginx serves .br/.gz siblings (brotli_static/
# gzip_static) instead of compressing multi-MB WASM/JS per request — the win
# for visitors on slow links. Done here (not in the archive) to keep the
# transfer small. brotli is optional; gzip always runs.
have_brotli=0; command -v brotli >/dev/null 2>&1 && have_brotli=1
find '${CURRENT}/frontend_web' -type f \
  \( -name '*.js' -o -name '*.wasm' -o -name '*.json' -o -name '*.html' \
     -o -name '*.css' -o -name '*.svg' -o -name '*.otf' -o -name '*.ttf' \) \
  -size +1k -print0 \
| while IFS= read -r -d '' f; do
    gzip -9 -kf "\$f"
    if [[ "\$have_brotli" == 1 ]]; then brotli -q 11 -f "\$f" -o "\$f.br"; fi
  done

# Python venv shared across releases; deps are the pinned union of backend +
# workers (requirements.lock.txt, regenerated via deploy/scripts/lock_requirements.sh
# and committed to the repo — keeps prod on the exact versions tested in CI).
if [[ ! -x '${VENV}/bin/python' ]]; then python3 -m venv '${VENV}'; fi
# Reinstalling deps on every deploy is slow (and pointless when they're
# unchanged). Hash the lock file and skip pip when it matches.
REQ_HASH=\$(sha256sum '${CURRENT}/requirements.lock.txt' | cut -d' ' -f1)
if [[ "\$(cat '${SHARED}/.requirements.sha256' 2>/dev/null)" == "\$REQ_HASH" ]]; then
  echo "deps unchanged — skipping pip install"
else
  '${VENV}/bin/pip' install --quiet --upgrade pip setuptools wheel
  '${VENV}/bin/pip' install --quiet -r '${CURRENT}/requirements.lock.txt'
  echo "\$REQ_HASH" > '${SHARED}/.requirements.sha256'
fi

# Playwright's browser lives in ~/.cache/ms-playwright and is versioned to the
# playwright package, so a lock-file bump silently strands the old binary and
# every SPA-fallback scrape starts erroring (only visible as per-source errors
# in the diff-beat results). Idempotent + ~1s when already installed. Fail-soft:
# HTTP scraping still works without it, so a download hiccup must not abort the
# deploy — but say so loudly.
if ! '${VENV}/bin/python' -m playwright install chromium; then
  echo "WARN: playwright browser install FAILED — SPA scraping degraded until fixed"
fi

# Shared env files survive releases; bootstrap from templates on first deploy
for side in backend workers; do
  if [[ ! -f "${SHARED}/\${side}.env" ]]; then
    sed -e "s|@TARGET_HOST@|${TARGET_HOST}|g" \
        -e "s|@SITE_DOMAIN@|${SITE_DOMAIN}|g" \
        -e "s|@API_DOMAIN@|${API_DOMAIN}|g" \
        -e "s|@APP_PORT@|${APP_PORT}|g" \
        -e "s|@INGEST_KEY@|\$(head -c24 /dev/urandom | od -An -tx1 | tr -d ' \n')|g" \
        "${CURRENT}/deploy/env/\${side}.env.example" > "${SHARED}/\${side}.env"
    chmod 600 "${SHARED}/\${side}.env"
    echo "NOTE: created ${SHARED}/\${side}.env from template — review secrets"
  fi
done
ln -sf '${SHARED}/backend.env' '${CURRENT}/backend/.env'
ln -sf '${SHARED}/workers.env' '${CURRENT}/workers/.env'

# GeoIP country DB (DB-IP Lite — no account/key) for country-level analytics.
# Privacy-safe: only the resolved country is counted, the IP is never stored.
# Idempotent — refreshed at most once per calendar month, and fail-soft.
GEOIP_DIR='${SHARED}/geoip'
mkdir -p "\$GEOIP_DIR"
MMDB="\$GEOIP_DIR/country.mmdb"
MONTH="\$(date -u +%Y-%m)"
if [[ ! -s "\$MMDB" || "\$(cat "\$GEOIP_DIR/.month" 2>/dev/null)" != "\$MONTH" ]]; then
  URL="https://download.db-ip.com/free/dbip-country-lite-\${MONTH}.mmdb.gz"
  if curl -fsSL "\$URL" -o "\$MMDB.gz" && gunzip -f "\$MMDB.gz"; then
    echo "\$MONTH" > "\$GEOIP_DIR/.month"
    echo "NOTE: fetched DB-IP country database (\$MONTH)"
  else
    rm -f "\$MMDB.gz"
    echo "warn: DB-IP country db fetch failed — geography stays empty until next deploy"
  fi
fi
grep -q '^GEOIP_DB_PATH=' '${SHARED}/backend.env' \
  || echo "GEOIP_DB_PATH=\$MMDB" >> '${SHARED}/backend.env'
EOF

  if [[ "$DEPLOY_SKIP_MIGRATE" != "1" ]]; then
    echo ">>> [${SERVICE_USER}] Applying CQL migrations (tier=${DEPLOY_CQL_TIER})"
    # Keyspace + role are one-time admin setup (deploy/README.md "Cassandra
    # role"); migrations run with the app credentials from shared/backend.env.
    ssh "$SSH_SVC" bash -s <<EOF
set -euo pipefail
set -a; source '${SHARED}/backend.env'; set +a
if [[ -z "\${CASSANDRA_PASSWORD:-}" ]]; then
  echo "error: CASSANDRA_PASSWORD is empty in ${SHARED}/backend.env"
  echo "hint: create the 'algorand' role + keyspace first (deploy/README.md)"
  exit 1
fi
cd '${CURRENT}'
'${VENV}/bin/python' deploy/scripts/cql_migrate.py apply --tier '${DEPLOY_CQL_TIER}'
EOF
  else
    echo ">>> Skipping CQL migrations (DEPLOY_SKIP_MIGRATE=1)"
  fi

  echo ">>> [root] Installing systemd units + nginx site, restarting services"
  for unit in "${UNITS[@]}"; do
    render "$SCRIPT_DIR/systemd/${unit}.service" \
      | ssh "$SSH_ROOT" "cat > /etc/systemd/system/${unit}.service"
  done
  install_nginx_site
  ssh "$SSH_ROOT" "systemctl daemon-reload && systemctl enable ${UNITS[*]} && systemctl restart ${UNITS[*]}"

  echo ">>> Checking units are active"
  for unit in "${UNITS[@]}"; do
    ssh "$SSH_SVC" "systemctl is-active --quiet ${unit}.service" \
      || die "${unit} is not active (logs: ssh ${SSH_ROOT} journalctl -u ${unit} -n 50)"
  done

  echo ">>> Waiting for readiness (${DEPLOY_HEALTH_URL})"
  deadline=$((SECONDS + DEPLOY_HEALTH_TIMEOUT_SEC))
  until ssh "$SSH_SVC" "curl -fsS '${DEPLOY_HEALTH_URL}' >/dev/null"; do
    if (( SECONDS >= deadline )); then
      echo "error: readiness check timed out after ${DEPLOY_HEALTH_TIMEOUT_SEC}s"
      echo "hint: rollback with DEPLOY_CONFIRM=1 ./deploy/rollback.sh"
      exit 1
    fi
    sleep 2
  done
  ssh "$SSH_SVC" "curl -fsS '${DEPLOY_HEALTH_URL}'" && echo

  echo ">>> Recent backend logs"
  ssh "$SSH_ROOT" "journalctl -u algorand-platform-backend.service -n 20 --no-pager" || true

  echo "Done — deployed ${ARCHIVE_NAME} → http://${TARGET_HOST}/"
}

# ---------------------------------------------------------------------------
cmd_status() {
  for unit in "${UNITS[@]}" nginx redis-server cassandra; do
    state=$(ssh "$SSH_SVC" "systemctl is-active ${unit} 2>/dev/null" || true)
    printf '%-36s %s\n' "$unit" "${state:-unknown}"
  done
  echo
  ssh "$SSH_SVC" "curl -fsS '${DEPLOY_HEALTH_URL}'" && echo || echo "(health endpoint unreachable)"
}

# ---------------------------------------------------------------------------
require_config
case "${1:-deploy}" in
  provision) cmd_provision ;;
  deploy)    cmd_deploy ;;
  status)    cmd_status ;;
  *) echo "usage: $0 [provision|deploy|status]"; exit 1 ;;
esac
