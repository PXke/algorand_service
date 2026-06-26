# Brick: Article compose (template)

## Goal

Turn scrape result + chain context into a **markdown article** without LLM in v1.

## Status

`partial` (template default; optional Mistral via [ai-mistral-connector.md](ai-mistral-connector.md))

## Example: compose in tests

The same template used at publish time is exercised in `backend/tests/test_article_compose.py`:

```python
from app.modules.news.services.article_compose import compose_scrape_article

article = compose_scrape_article(
    service_name="Example DAO",
    source_url="https://example.com",
    title="Page title",
    text="Body text from scraper.",
    trigger_txid="T" * 52,
    trigger_round=12345,
    diff_block="--- previous\n+++ current\n@@ ...",
)
```

Workers call the equivalent under `workers/app/modules/newspaper/article_compose.py` during `run_publish_pipeline`.

## Features (should do)

- Generate title, summary, markdown body
- Include service name, source URL, trigger `txid`, round
- Include diff block when content changed (not first snapshot)
- Sanitize body (strip `<script>`, event handlers) before persist
- Same template logic in API module (tests) and workers (publish)

## Good to have

- Configurable max diff lines in article
- Short summary length limit for feed cards

## Future improvements

- Richer prompts per service category (extend Mistral connector)
- Human review queue before publish
- Multi-language articles
- Template variants per service category
- Embed ASA / app id metadata in article front matter

## Standards & RFCs

| Reference | Use |
|-----------|-----|
| [CommonMark](https://spec.commonmark.org/) (informative) | Article body |
| OWASP XSS guidance | Sanitize before persist |

[standards-and-rfcs.md](../architecture/standards-and-rfcs.md#article-compose).

## Depends on

- `worker-scraper`, `worker-pipeline`

## Code map

- `backend/app/modules/news/services/article_compose.py`
- `workers/app/modules/newspaper/article_compose.py` — scrape template
- `workers/app/modules/newspaper/article_composer.py` — facade: scrape, `compose_weekly_price`, `compose_weekly_digest` (template + Mistral fallback)
- `workers/app/modules/newspaper/weekly_digest.py` — digest template + context
- `workers/app/modules/ai/mistral_compose.py` — Mistral prompts ([ai-mistral-connector.md](ai-mistral-connector.md))
- `backend/app/core/sanitize.py`
