# Backend (Falcon + Gunicorn)

The public/API + SSR surface of the newspaper platform. All wire schemas are
`msgspec.Struct` (`app/schemas.py`) — pydantic has been fully removed. All
Cassandra queries go through prepared statements centralized in
`app/core/statements.py` (one `*Stmts` class per module, `prepare_cached`) —
never raw `execute()` with inline CQL.

## Local run

```bash
cd backend
python3.15t -m venv .venv   # or python3 / python3.15
source .venv/bin/activate
pip install -e .
cp .env.example .env
gunicorn app.falcon_main:app -k app.core.gunicorn_affinity.AffinityThreadWorker --workers 1 --threads "$(nproc)" --bind 127.0.0.1:8080 --reload
```

Production uses `deploy/scripts/run_backend.sh` under systemd (see `deploy/systemd/`).

## Module-first backend structure

- `app/modules/auth/` — wallet auth (nonce issue, signature verify, session lookup/logout)
- `app/modules/news/` — public news feed + article API
- `app/modules/seo/` — SSR/crawler surface: nginx proxies navigation paths here for real `<title>`/OG/JSON-LD + `#ssr-body`; also serves `robots.txt`, `sitemap*.xml`, `feed.xml`, `llms.txt`, and the analytics pageview beacon
- `app/modules/admin/` — wallet-gated ops/CMS console: article edit/versions, editorial briefs, source curation + domain approve/reject, classifier review/feedback/retrain, gatekeeper anchors/validation, compose sessions, translations backfill, investigations (~35 endpoints, by far the largest module)
- `app/modules/search/` — public full-text search (Typesense, with in-process feed-scan fallback)
- `app/modules/suggestions/` — user-submitted suggestions + on-chain proof-of-payment verification
- `app/modules/registry/` — read-only directory of known Algorand ecosystem services (write path is under `admin`)
- `app/modules/chain/` — internal library (not its own HTTP surface) reading Conduit-indexed transactions from Cassandra and verifying on-chain payment proofs; consumed by `suggestions` and `registry`
- `app/modules/media/` — same-origin, SSRF-guarded image proxy (`GET /api/v1/img`) so Flutter web can load external hero/OG images; downsizes/re-encodes to WebP
- `app/modules/placements/` — sponsored/pinned feed placement slots
- `app/modules/contact/` — public contact form + admin inbox (no outbound email; honeypot + rate limit)
- `app/modules/ingest/` — authenticated webhook (`POST /api/v1/ingest/signal`) that queues external signals onto Redis for workers to consume
- `app/core/` — shared runtime config, Cassandra session/cache, the prepared-statement registry (`statements.py`)
