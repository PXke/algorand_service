#!/usr/bin/env bash
# Restore the previous release tree and restart services.
# File swap runs as SERVICE_USER; systemctl runs as ROOT_USER.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
[[ -f "$SCRIPT_DIR/deploy.conf" ]] && source "$SCRIPT_DIR/deploy.conf"

TARGET_HOST="${TARGET_HOST:-}"
SERVICE_USER="${SERVICE_USER:-${TARGET_USER:-}}"
ROOT_USER="${ROOT_USER:-root}"
TARGET_PATH="${TARGET_PATH:-/srv/algorand-platform}"
DEPLOY_CONFIRM="${DEPLOY_CONFIRM:-0}"

UNITS=(algorand-platform-backend algorand-platform-celery algorand-platform-celery-beat)

if [[ -z "$TARGET_HOST" || -z "$SERVICE_USER" ]]; then
  echo "error: TARGET_HOST and SERVICE_USER are required"
  exit 1
fi

if [[ "$DEPLOY_CONFIRM" != "1" ]]; then
  echo "Rollback target: ${SERVICE_USER}@${TARGET_HOST}:${TARGET_PATH}"
  echo "Set DEPLOY_CONFIRM=1 to proceed."
  exit 1
fi

RELEASES="${TARGET_PATH}/releases"
CURRENT="${RELEASES}/current"
PREVIOUS="${RELEASES}/previous"

echo ">>> [${SERVICE_USER}] Swapping current with previous"
ssh "${SERVICE_USER}@${TARGET_HOST}" bash -s <<EOF
set -euo pipefail
if [[ ! -d '${PREVIOUS}' ]]; then
  echo "error: no previous release at ${PREVIOUS}"
  exit 1
fi
rm -rf '${CURRENT}.failed'
if [[ -d '${CURRENT}' ]]; then
  mv '${CURRENT}' '${CURRENT}.failed'
fi
cp -a '${PREVIOUS}' '${CURRENT}'
EOF

echo ">>> [root] Restarting services"
ssh "${ROOT_USER}@${TARGET_HOST}" "systemctl restart ${UNITS[*]} && systemctl reload nginx"

for unit in "${UNITS[@]}"; do
  ssh "${SERVICE_USER}@${TARGET_HOST}" "systemctl is-active --quiet ${unit}.service"
done

echo "Rollback complete"
