# Algorand Platform

Monorepo for **PXke Algorand**, an Algorand-focused, largely autonomous
newspaper — Robyn backend, Celery workers (frontier crawler, writer
pipeline, gatekeeper, social distribution), and a Vite + Svelte web SPA.

## Workspace Layout

- `backend/`: Robyn API service
- `conduit/`: Conduit + Cassandra exporter (algod → on-chain tables)
- `workers/`: Celery asynchronous workers
- `frontend/`: Vite + Svelte SPA (primary web client)
- `frontend_flutter/`: **deprecated** Flutter web client (see `DEPRECATED.md`)
- `deploy/`: deployment scripts and systemd templates
- `docs/prd/`: product requirement documents
- `docs/adr/`: architecture decision records
- `opensource/`: open-source components (including Flutter wallet auth, used historically)

## Quick Start

1. **Local UI dev:** `make dev-ui` — Docker API + workers, then Vite SPA on :5173 ([docker/README.md](docker/README.md))
2. **Local testing (Docker):** `make docker-test` · `make lint` · [docker/README.md](docker/README.md) — deps, pytest, ruff, vulture (not production)
3. Backend: see `backend/README.md`
4. Workers: see `workers/README.md`
5. Frontend: see `frontend/README.md`
6. Deployment: see `deploy/README.md`

## Initial Milestones

- **Done:** Product 0 — wallet auth (`wallet_auth_flutter`, ARC-0025 / ARC-0060 / SIWA, Robyn verify + session)
- **Done:** Monorepo scaffold + deploy scripts
- **Done:** Conduit → Cassandra exporter, Newspaper pipeline, Suggestions, Search API (Typesense + fallback)
- **Done:** CORS, `/health/ready`, Vite SPA shell, CI workflows
- **TestNet:** run `cql-migrate`, Conduit, Celery worker + beat, seed `service_registry`
- **Client target:** Vite + Svelte **SPA** (wallet sign-in against `/api/v1/auth`)

## Platform today

The product pivoted from a chain-event feed to a fully managed newspaper
(~7 articles/day, quality/depth over speed). Key pieces beyond the initial
milestones above:

- **Frontier crawler** — autonomous domain discovery, per-URL politeness/cooldown, ecosystem-directory sync, admin approve/reject queue (`workers/app/modules/crawler/`)
- **Two-stage writer pipeline** — research → gap-fill → write → grade → revise via Mistral, with a pre-publish gatekeeper (deterministic gate live in shadow mode; ModernBERT MTTH quality head staged, not yet serving) (`workers/app/modules/newspaper/`, `workers/app/modules/gatekeeper/`)
- **SEO crawl surfaces** — sitemaps, RSS, robots, llms.txt, OG share cards; SPA serves app HTML (`backend/app/modules/seo/`, `frontend/`)
- **Admin console** — wallet-gated ops/CMS: article edit, source curation, classifier retrain, gatekeeper tuning, publish queue, analytics (`backend/app/modules/admin/`, SPA `/admin`)
- **Social auto-distribution** — Bluesky, Telegram, Mastodon posting on publish (`workers/app/modules/distribution/`)
- **8-language article translation** (every non-English UI locale), 9-language UI (`frontend/src/lib/i18n/`)
- **msgspec** for all wire schemas (pydantic fully removed); **prepared-statement registry** for all Cassandra queries (`backend/app/core/statements.py`)

- **Weekly digest** — Monday beat + manual task; market + feed highlights → [docs/modules/weekly-price-analysis.md](docs/modules/weekly-price-analysis.md)
- **Price metrics for Mistral** — hourly CoinGecko samples + Cassandra brief → [docs/modules/price-metrics-mistral.md](docs/modules/price-metrics-mistral.md)
- **Mistral connector** — scrape, digest, diff-on-change → [docs/modules/ai-mistral-connector.md](docs/modules/ai-mistral-connector.md)
- **Docker tests** use Python **3.14** → [docker/README.md](docker/README.md)

## Brick tracking

Each feature is a **brick** with its own doc under `docs/modules/` (goal, status, features, **Standards & RFCs**, code map).

- Index: [docs/modules/README.md](docs/modules/README.md)
- Implementing a brick: [docs/architecture/brick-implementation-guide.md](docs/architecture/brick-implementation-guide.md) + [standards-and-rfcs.md](docs/architecture/standards-and-rfcs.md)
- Products → bricks map: [docs/architecture/products-and-bricks.md](docs/architecture/products-and-bricks.md)
- CQL migrations: [docs/architecture/cql-migrations.md](docs/architecture/cql-migrations.md)
- `docs/architecture/release-cadence.md`: **2w dev → freeze → 2w TestNet → release** (4-week cycle).
- `docs/architecture/wallet-auth-protocol.md`: wallet auth flows, ARC-0025 / ARC-0060 coverage, diagrams.
- [docs/architecture/publish-pipeline-workflow.md](docs/architecture/publish-pipeline-workflow.md): compose → gate → publish → translate → distribute, with a diagram.
