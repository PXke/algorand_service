# Brick: Article translations

## Goal

Serve every article in the reader's chosen UI language without a separate
per-language editorial pipeline.

## Status

`done`

## Features (should do)

- 8-language local translation pipeline (`fa`, `ps`, `ar`, `ru`, `zh`, `hi`, `es`, `fr` — every non-English UI locale), replacing the earlier Mistral-based one (2026-08-01): two CPU-only local models instead of an API call per language — `xiaomi-research/MiLMMT-46-4B-v0.1` for everything except Pashto, `facebook/seamless-m4t-v2-large` for Pashto (the one language with no fluent Mistral-era alternative). See `workers/app/modules/ai/local_translate.py`.
- Enqueued at publish as ONE Celery task per article (`translate_article_batch`, not one task per language) — loads whichever engine(s) the missing languages need, translates everything routed to that engine, unloads, moves to the next; never both models resident in memory at once. Dedicated `algorand-platform-celery-translate` worker (`-Q translate --concurrency=1`), isolated from the shared worker pool. Not part of the publish transaction.
- List/table markdown structure: MiLMMT handles both correctly fed as a whole block; SeamlessM4T does not (confirmed to destroy tables outright, not just reformat them) — its path splits list items / table cells into isolated per-item/per-cell calls and reassembles the markdown itself. See `docs/architecture/translation-model-survey.md` for the evidence.
- Stored in a `translations` map column on the article row (not a separate table)
- Backfill run for pre-existing articles (2026-07-04, under the old Mistral pipeline)
- Re-enqueued (old translations cleared) on `recompose_published` auto-apply

## Good to have

- n/a — now covers all 8 non-English UI languages

## Future improvements

- Social distribution (`distribute_article`) always posts the original-language fields only — never reads `article.translations`
- Farsi and Pashto are the two languages with the least confidence behind them (see the survey doc's conclusions) — Farsi is where the original agent/agency term-consistency defect happened, Pashto has no license-clean, quality-proven alternative to the currently-accepted CC-BY-NC SeamlessM4T exception. Neither is fully resolved.
- No per-language quality signal in production yet — the promising-ranking eval harness (`workers/app/modules/ai/translation_eval.py` + `workers/scripts/eval_translate_*`) is offline/manual only, not wired into the publish pipeline.

## Standards & RFCs

n/a.

## Depends on

- `article-compose`; no external API dependency for translation itself anymore (both engines run locally)

## Code map

- `workers/app/modules/ai/local_translate.py` (the two engines, batching, list/table handling)
- `workers/app/modules/ai/local_translate_lock.py` (cross-worker mutex — only one translation batch runs at a time)
- `workers/app/modules/ai/translation_eval.py` + `workers/scripts/eval_translate_*` (offline candidate-model comparison harness, not part of the serving path)
- `workers/app/modules/newspaper/` (translation enqueue on publish)
- `backend/app/schemas.py` (`translations` field on the article struct)
