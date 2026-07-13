# Workers (Celery)

## Features implemented in this brick

- Frontier crawler: discovery, per-URL politeness/cooldown, domain approve/reject state machine, ecosystem-directory sync, mention-based discovery — all on the Celery beat schedule
- Chain-tail watcher: tails new Algorand rounds, matches transactions against the service registry, triggers article generation; hourly xGov proposal poll
- Two-stage writer pipeline: research → gap-fill → write → grade → revise (Mistral), with a deterministic pre-publish gatekeeper (rule-based completeness + numeric-entailment factuality check, live in shadow/non-enforcing mode) — the newer ModernBERT "MTTH" multi-task quality head is built + trainable but **not wired into serving yet**
- Social auto-distribution on publish: Bluesky, Telegram, Mastodon (each gated by presence of its own credentials, fault-isolated per channel)
- Typesense indexing (event-driven, not polled): articles and crawled pages
- Weekly digest — market + feed highlights ([weekly-price-analysis.md](../docs/modules/weekly-price-analysis.md))
- Price metrics — CoinGecko poll + Mistral brief ([price-metrics-mistral.md](../docs/modules/price-metrics-mistral.md))
- 8-language article translation, enqueued at publish as separate fire-and-forget tasks (not part of the publish transaction)

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

- `app/modules/crawler/`: frontier crawler — URL queue, domain tracker/approval, ecosystem-directory sync, mention discovery, publish-classifier retrain
- `app/modules/chain_tail/`: chain round watcher + xGov proposal poll → triggers article generation from chain events
- `app/modules/newspaper/`: writer pipeline (research/compose/grade/revise), service-watch, articles, weekly digest, price analysis
- `app/modules/gatekeeper/`: pre-publish quality/factuality gate — live deterministic rule-based gate (`live.py`), plus the ModernBERT MTTH quality-head model/training/inference (staged, not yet called from serving)
- `app/modules/distribution/`: social auto-posting (`bluesky.py`, `telegram.py`, `mastodon.py`) + dispatcher, called from the real publish paths
- `app/modules/search/`: Typesense indexing tasks (article + crawled-page), distinct from the backend's `search` module
- `app/modules/scraper/`: fetch/extract/hash brick
- `app/modules/pipeline/`: diff and transformation brick
- `app/modules/security/`: suspicious transaction inspection brick
- `app/modules/metrics/`: price samples + Mistral context brief
- `app/modules/ai/`: Mistral client + compose helpers
- `app/tasks/`: thin Celery task entrypoints (`newspaper`, `metrics`, `crawler`, `chain_tail`, `gatekeeper`, …)
