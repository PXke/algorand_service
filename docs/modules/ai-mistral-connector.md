# Brick: AI — Mistral connector

## Goal

Generate newspaper **title**, **summary**, and **markdown body** via [Mistral Chat Completions](https://docs.mistral.ai/api/) when enabled, with template fallback.

## Status

`partial` (workers connector + publish/price hooks)

## Features

- `MistralClient` — HTTP JSON chat completions (`httpx`)
- Structured JSON output (`title`, `summary`, `body`)
- `compose_scrape_article` — scrape-triggered articles (publish pipeline)
- `compose_weekly_digest` — weekly digest (CoinGecko + feed highlights)
- Stored price brief from [price-metrics-mistral.md](price-metrics-mistral.md) is appended to price/digest prompts when available
- `compose_weekly_price` — price-only legacy helper
- `compose_recap_from_transcript_mistral` — community-call recap from a video transcript (`MISTRAL_MODEL_PREMIUM`)
- Fallback to template when `MISTRAL_FALLBACK_TEMPLATE=1` (default)

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `MISTRAL_ENABLED` | `0` | Master switch |
| `MISTRAL_API_KEY` | *(empty)* | API key from Mistral console |
| `MISTRAL_MODEL` | `mistral-small-latest` | Default model (scrape articles) |
| `MISTRAL_MODEL_BREAKING` | same as `MISTRAL_MODEL` | Breaking credibility JSON |
| `MISTRAL_MODEL_DIGEST` | same as `MISTRAL_MODEL` | Weekly digest |
| `MISTRAL_MODEL_PREMIUM` | `mistral-medium-latest` | Reserved for long recap / transcript |

Model selection guide: [mistral-model-selection.md](mistral-model-selection.md).
| `MISTRAL_API_BASE` | `https://api.mistral.ai/v1` | API base URL |
| `MISTRAL_MAX_TOKENS` | `1024` | Completion token cap |
| `MISTRAL_TIMEOUT_SECONDS` | `60` | HTTP timeout |
| `MISTRAL_FALLBACK_TEMPLATE` | `1` | Use template on API/parse errors |
| `MISTRAL_MAX_SOURCE_CHARS` | `6000` | Scrape text clipped for prompts |

Enable locally:

```bash
export MISTRAL_ENABLED=1
export MISTRAL_API_KEY=your-key
```

Celery publish responses include `"composer": "mistral"` or `"template"`.

## Periodic diff check (Celery beat)

Task `app.tasks.newspaper.check_and_publish_mistral_on_diff`:

1. Loads all enabled `service_registry` rows with a `scrape_url`
2. Scrapes each source and compares content hash to the latest snapshot
3. On **unchanged** — skips (no Mistral call)
4. On **diff** (or first snapshot) — composes with **Mistral only** (`mistral_only=True`)
5. Publishes article + Typesense index when compose succeeds

Beat schedule: `MISTRAL_DIFF_POLL_SECONDS` (default **600**).

Manual run:

```bash
cd workers && PYTHONPATH=. celery -A app.celery_app call app.tasks.newspaper.check_and_publish_mistral_on_diff
```

## Code map

- `workers/app/modules/ai/mistral_client.py`
- `workers/app/modules/ai/mistral_compose.py`
- `workers/app/modules/newspaper/article_composer.py`
- `workers/tests/test_mistral_client.py`
- `workers/tests/test_article_composer.py`

## Depends on

- `article-compose` (template fallback)
- `price-metrics-mistral` (optional stored brief in prompts)
- `worker-scraper`, `publish_from_chain_event`, `weekly-price-analysis`

## Future improvements

- Human review queue before publish
- Per-service prompt overrides in `service_registry`
- Token/cost metrics and rate limits
- Backend admin endpoint to test prompts
