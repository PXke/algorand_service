# Docker (testing only)

One compose file for **local dependencies**, **unit tests**, optional **API + workers**, and optional **Algorand localnet** (private `algod`). Not used for production — see [deploy/README.md](../deploy/README.md).

**Python runtime:** test image and GitHub Actions both use **`python:3.14-slim-bookworm`** / Python 3.14 ([Dockerfile](Dockerfile), [quality-ci.md](../docs/modules/quality-ci.md)).

## Full-stack dev (Flutter + Docker)

| Goal | Command |
|------|---------|
| **API in Docker + Flutter web** (public TestNet for chain) | `make dev-ui` or `./docker/bin/dev-ui.sh` |
| Same + **private localnet** (`algod` on :4001) | `make dev-ui-localnet` or `./docker/bin/dev-ui.sh --localnet` |
| Docker only (no Flutter) | `./docker/bin/dev-ui.sh --docker-only` |
| Tear down stack when script exits | add `--stop-docker` |

**Does this start TestNet locally?** No — by default the backend talks to **public Algorand TestNet** over HTTPS (`testnet-api.algonode.cloud`). Nothing runs `algod` on your machine unless you use `--localnet`, which starts a **private dev network** (Sandbox-style node on ports 4001/4002), not the public TestNet.

Flutter defaults match: `API_BASE_URL=http://127.0.0.1:8080`, wallet/algod via TestNet unless `--localnet` sets `ALGOD_API_URL=http://127.0.0.1:4001`.

## Quick reference

| Goal | Command |
|------|---------|
| Deps only (Cassandra, Redis, Typesense, CQL migrate + seed) | `make docker-up` or `docker compose up -d --wait` |
| **Unit tests + lint** (container, shared image built once) | `make docker-test` |
| **Lint only** (same scripts as CI, inside platform image) | `make lint` |
| API + Celery (TestNet algod default) | `make docker-app` |
| P1 feed smoke (scrape + article in feed) | `make docker-smoke` (after `docker-app`) |
| API + Celery + **localnet** | `make docker-localnet` |
| Deps + app + tests | `make docker-app-test` |
| Reset volumes | `make docker-reset` |
| Clear stale buildx cache dir (if you used an older compose) | `make docker-clean-cache` |

## Unit tests

```bash
make docker-test
# or
docker compose up -d --wait
docker compose --profile test run --rm test
```

Tests use real Cassandra/Redis in compose; chain reads are mocked in pytest (no localnet required).

Skip ARC-0060 vector test:

```bash
SKIP_ARC0060_TESTS=1 make docker-test
```

Skip lint in the test container (pytest only):

```bash
SKIP_LINT=1 make docker-test
```

## Full app stack

```bash
make docker-app
curl -s http://localhost:8080/health/ready | jq
```

### P1 newspaper smoke (no Conduit required)

Enqueues one `publish_from_chain_event` (scrapes `algorand.org`, writes to Cassandra):

```bash
make docker-smoke
```

Localnet registry seed (optional):

```bash
SEED_FILE=deploy/seeds/localnet_services.toml docker compose run --rm migrate \
  /bin/bash -c 'SEED_FILE=deploy/seeds/localnet_services.toml python3 deploy/scripts/seed_service_registry.py'
```

Default `ALGOD_URL` is public TestNet. Flutter on the host: `http://localhost:8080`, `AUTH_DOMAIN=localhost`.

Compose runs the Celery worker with **`-c 2`** (not 16) so it fits the test stack memory limit (~1G).

## Algorand localnet (optional)

Private network `algod` + `kmd` (ports **4001** / **4002**), dev token `64 × a` (Algorand Sandbox / AlgoKit LocalNet convention).

**AlgoKit / host LocalNet:** if you already run AlgoKit on the host, its API token may differ from Sandbox’s `64 × a`. Point `ALGOD_TOKEN` / `ALGOD_NETADDR` at your node; do not start profile `localnet` twice on the same ports.

```bash
make docker-localnet
```

From the **host**:

- Algod: `http://localhost:4001`
- Token: `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`

Containers use `ALGOD_URL=http://algod:4001` via [docker/localnet/.env.example](localnet/.env.example).

### Localnet + Conduit → Cassandra

Conduit needs **follower** `algod`. Start localnet and Conduit together:

```bash
cp docker/localnet/.env.example .env
# Edit .env: COMPOSE_PROFILES=app,localnet,chain
docker compose --env-file .env up -d --build --wait
```

If Conduit fails on follower mode, use chain-tail against localnet head only (no indexed txns until Conduit works) or use [AlgoKit LocalNet](https://dev.algorand.co/algokit/cli/localnet/) beside this stack and set `ALGOD_URL` / `ALGOD_NETADDR` to the host.

### Already running AlgoKit / Sandbox on the host

Do not start profile `localnet`. Use:

```bash
# .env
ALGOD_URL=http://host.docker.internal:4001
ALGOD_TOKEN=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
```

```bash
docker compose --profile app up -d
```

## Run pytest on the host

```bash
docker compose up -d --wait
cd backend && source .venv/bin/activate
export CASSANDRA_HOSTS=127.0.0.1 REDIS_URL=redis://127.0.0.1:6379/0
PYTHONPATH=. pytest -q
```

## Weekly digest and price metrics (workers)

With `make docker-app` (worker + beat running) and migration `app/008` applied:

```bash
# Collect CoinGecko sample + rebuild Mistral brief (Cassandra)
docker compose exec worker celery -A app.celery_app call app.tasks.metrics.collect_price_metrics

# Publish this week's digest article (template or Mistral if enabled)
docker compose exec worker celery -A app.celery_app call app.tasks.newspaper.publish_weekly_digest
```

Set `MISTRAL_ENABLED=1` and `MISTRAL_API_KEY` on `worker` / `beat` in `.env` or `docker-compose.yml` for LLM prose. See [weekly-price-analysis.md](../docs/modules/weekly-price-analysis.md) and [price-metrics-mistral.md](../docs/modules/price-metrics-mistral.md).

Verify in the app: `curl -s 'http://localhost:8080/api/v1/news/feed?limit=5' | jq` and open Flutter at `http://localhost:8080` (or your dev port).

## Files

| File | Role |
|------|------|
| [Dockerfile](Dockerfile) | Shared image `algorand-platform-test:$PLATFORM_TAG` (default git short SHA) — Python **3.14-slim-bookworm** |
| [Dockerfile.conduit](Dockerfile.conduit) | Conduit binary (profile `chain`) |
| [bin/dev-ui.sh](bin/dev-ui.sh) | Docker app stack + Flutter web |
| [bin/compose-test.sh](bin/compose-test.sh) | `up` + lint + pytest (test container) |
| [bin/run-tests.sh](bin/run-tests.sh) | lint (optional) + pytest |
| [localnet/.env.example](localnet/.env.example) | Localnet + app env template |
