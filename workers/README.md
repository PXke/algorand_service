# Workers (Celery)

## Features implemented in this brick

- Source scraping task (HTTP + HTML extraction)
- Snapshot diff task (unified diff)
- Suspicious transaction group inspection task
- Newspaper publish from chain events, Mistral-on-diff beat
- **Weekly digest** — market + feed highlights ([weekly-price-analysis.md](../docs/modules/weekly-price-analysis.md))
- **Price metrics** — CoinGecko poll + Mistral brief ([price-metrics-mistral.md](../docs/modules/price-metrics-mistral.md))

## Local run

```bash
cd workers
python -m venv .venv
source .venv/bin/activate
pip install -e .
celery -A app.celery_app worker --loglevel=INFO -Q default,scrape,pipeline,security
celery -A app.celery_app beat --loglevel=INFO   # separate terminal for schedules
```

Prefer **Docker** for integration tests (Python 3.14, Cassandra, Redis): `make docker-test` · [docker/README.md](../docker/README.md).

## Module-first worker structure

- `app/modules/scraper/`: fetch/extract/hash brick
- `app/modules/pipeline/`: diff and transformation brick
- `app/modules/security/`: suspicious transaction inspection brick
- `app/modules/newspaper/`: articles, weekly digest, price analysis
- `app/modules/metrics/`: price samples + Mistral context brief
- `app/modules/ai/`: Mistral client + compose helpers
- `app/tasks/`: thin Celery task entrypoints (`newspaper`, `metrics`, …)
