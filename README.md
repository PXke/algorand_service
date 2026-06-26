# Algorand Platform

Monorepo for an Algorand-focused platform built with Robyn (backend), Celery workers, and Flutter web frontend.

## Workspace Layout

- `backend/`: Robyn API service
- `conduit/`: Conduit + Cassandra exporter (algod → on-chain tables)
- `workers/`: Celery asynchronous workers
- `frontend_flutter/`: Flutter web client
- `deploy/`: deployment scripts and systemd templates
- `docs/prd/`: product requirement documents
- `docs/adr/`: architecture decision records
- `opensource/`: open-source components (including Flutter wallet auth)

## Quick Start

1. **Local UI dev:** `make dev-ui` — Docker API + workers, then Flutter in Chrome ([docker/README.md](docker/README.md))
2. **Local testing (Docker):** `make docker-test` · `make lint` · [docker/README.md](docker/README.md) — deps, pytest, ruff, vulture (not production)
3. Backend: see `backend/README.md`
4. Workers: see `workers/README.md`
5. Frontend: see `frontend_flutter/README.md`
6. Deployment: see `deploy/README.md`

## Initial Milestones

- **Done:** Product 0 — wallet auth (`wallet_auth_flutter`, ARC-0025 / ARC-0060 / SIWA, Robyn verify + session)
- **Done:** Monorepo scaffold + deploy scripts
- **Done:** Conduit → Cassandra exporter, Newspaper pipeline, Suggestions, Search API (Typesense + fallback)
- **Done:** CORS, `/health/ready`, Flutter shell (News | Suggestions | Search), CI workflows
- **TestNet:** run `cql-migrate`, Conduit, Celery worker + beat, seed `service_registry`
- **Client target:** Flutter **web first** (WalletConnect QR from browser)

## Recent worker features (documented)

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
