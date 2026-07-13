# Brick: Article translations

## Goal

Serve every article in the reader's chosen UI language without a separate
per-language editorial pipeline.

## Status

`done`

## Features (should do)

- 8-language Mistral translation pipeline (`fa`, `ps`, `ar`, `ru`, `zh`, `hi`, `es`, `fr` — every non-English UI locale), enqueued at publish as separate fire-and-forget Celery tasks (`translate_article`), not part of the publish transaction
- `MISTRAL_MODEL_TRANSLATE=mistral-small-latest` (cheaper tier than compose)
- Stored in a `translations` map column on the article row (not a separate table)
- Backfill run for pre-existing articles (2026-07-04)
- One-off prod runner pattern for backfills (shared `PYTHONPATH`, shared rate limiter)
- Re-enqueued (old translations cleared) on `recompose_published` auto-apply

## Good to have

- n/a — now covers all 8 non-English UI languages

## Future improvements

- Social distribution (`distribute_article`) always posts the original-language fields only — never reads `article.translations`

## Standards & RFCs

n/a.

## Depends on

- `article-compose`, Mistral connector

## Code map

- `workers/app/modules/newspaper/` (translation enqueue on publish)
- `backend/app/schemas.py` (`translations` field on the article struct)
