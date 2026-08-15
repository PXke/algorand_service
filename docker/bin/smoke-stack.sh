#!/usr/bin/env bash
# Lightweight stack smoke: API health + feed read + Celery worker ping.
# Requires: docker compose --profile app up (backend + worker at minimum).
# No Mistral, chain, or external scrape — safe for CI after docker-test deps.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

API_URL="${API_URL:-http://localhost:8080}"

require_running() {
  local svc="$1"
  if ! docker compose ps --status running 2>/dev/null | grep -qE "${svc}"; then
    echo "error: ${svc} not running — start app stack first (make docker-app)" >&2
    exit 1
  fi
}

require_running backend
require_running worker

echo "==> GET /health"
curl -sf "${API_URL}/health" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert d.get('status') == 'ok', d
print('ok:', d.get('service'), d.get('env'))
"

echo "==> GET /health/ready"
curl -sf "${API_URL}/health/ready" | python3 -c "
import sys, json
d = json.load(sys.stdin)
checks = {c['name']: c for c in d.get('checks', [])}
assert d.get('status') == 'ok', d
for name in ('redis', 'cassandra'):
    assert checks.get(name, {}).get('ok'), (name, d)
print('ok:', ', '.join(f\"{c['name']}={c['ok']}\" for c in d['checks']))
"

echo "==> GET /api/v1/news/feed (empty feed is fine)"
curl -sf "${API_URL}/api/v1/news/feed?limit=1" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert 'items' in d and isinstance(d['items'], list), d
print('ok: items=', len(d['items']))
"

echo "==> Celery inspect ping"
docker compose exec -T worker celery -A app.celery_app inspect ping --timeout=10 \
  | grep -qi pong

echo "Stack smoke passed."
