#!/usr/bin/env bash
# P1 smoke: enqueue one newspaper publish task and wait for a feed item.
# Requires: docker compose --profile app up (API + worker + beat + migrate done).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

API_URL="${API_URL:-http://localhost:8080}"
SERVICE_ID="${SERVICE_ID:-algorand-foundation}"
SCRAPE_URL="${SCRAPE_URL:-https://example.com}"
DISPLAY_NAME="${DISPLAY_NAME:-Algorand Foundation}"
TXID="${TXID:-$(python3 -c 'print("A"*52)')}"
ROUND_NUM="${ROUND_NUM:-1}"

if ! docker compose ps --status running 2>/dev/null | grep -q worker; then
  echo "error: start the app stack first: make docker-app" >&2
  exit 1
fi

echo "Enqueue publish_from_chain_event (service_id=${SERVICE_ID})..."
docker compose exec -T worker celery -A app.celery_app call app.tasks.newspaper.publish_from_chain_event \
  --kwargs="{
    \"service_id\": \"${SERVICE_ID}\",
    \"display_name\": \"${DISPLAY_NAME}\",
    \"scrape_url\": \"${SCRAPE_URL}\",
    \"match_kind\": \"address\",
    \"match_value\": \"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAY5HFKQ\",
    \"txid\": \"${TXID}\",
    \"round_num\": ${ROUND_NUM}
  }"

echo "Waiting for feed (up to 60s)..."
for _ in $(seq 1 30); do
  count="$(curl -sf "${API_URL}/api/v1/news/feed" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('items',[])))")"
  if [[ "${count}" -gt 0 ]]; then
    echo "Feed has ${count} item(s)."
    curl -sf "${API_URL}/api/v1/news/feed" | python3 -m json.tool | head -40
    echo "Triggering price metrics collection..."
    docker compose exec -T worker celery -A app.celery_app call app.tasks.metrics.collect_price_metrics >/dev/null 2>&1 || true
    sleep 3
    echo "Price metrics:"
    curl -sf "${API_URL}/api/v1/metrics/price" | python3 -m json.tool || true
    exit 0
  fi
  sleep 2
done

echo "error: feed still empty — check worker logs: docker compose logs worker" >&2
exit 1
