# ADR-0004: Newspaper Pipeline Simplification — Three Stages, Two Classifiers

## Status

Accepted (2026-06-26)

## Context

The ingest/publish pipeline grew a single **generic funnel** (`ingest_publish_signal`)
that every source lane was forced through: a heuristic relevance gate, novelty
scoring, a `publish_intent`/`topic`/`event_phase` model, edit-vs-create
resolution, and several dedupe-key schemes. The generality — not the lanes — is
the source of the complexity (~1,400 lines across `ingest_signal`,
`publish_policy`, `publish_classifier`, `article_grader`).

Several lanes are also dead weight: **Discord, Reddit, Telegram** are unusable in
practice (platform restrictions), and **external push** has no producer.

The original intent was much simpler: a few **lanes**, each with a **simple,
explicit trigger**, and **intelligence concentrated in trained classifiers**.

## Decision

Collapse the pipeline to **three stages** with **two trained classifiers** plus a
**novelty/diversity ranker** as the only decision-makers. Everything between the
classifiers is dumb per-lane logic.

```
 STAGE 1 — DISCOVERY        STAGE 2 — LANES            STAGE 3 — PUBLISH
 (find sources)             (cheap triggers)           (decide what runs)

 chain data ─┐
 web crawl ──┼─▶ CLASSIFIER A   email → always         CLASSIFIER B  (quality/factual)
             └─▶ "is this   ──▶ web   → new OR diff ──▶      AND
                 domain worth     youtube→ new video    NOVELTY + COOLING (diversity)
                 monitoring?"          │                       │
                                       ▼                       ▼
                                  publish_queue           feed (published article)
                                  (dumb buffer)
```

### Stage 1 — Discovery (unchanged)
- Web crawl + chain data feed domain discovery. The crawler **scores domains, not
  pages** (`store_discovery_content`) and never enqueues articles.
- **Classifier A (domain worthiness)** — trained; decides which domains get
  promoted to monitored sources. This is what keeps "every crawled site" *out* of
  the publish queue.

### Stage 2 — Lanes (simple per-lane triggers)
Three lanes, each a few lines. No shared generic funnel, no enqueue-time relevance
heuristic (that duplicated Classifier A).

| Lane | Enqueue rule |
|------|--------------|
| **email** | always publishable |
| **web** | first time we see the site/product (`is_first`) **OR** content changed (`diff`) |
| **youtube** | a new video is published → download transcript → enqueue |

### Stage 3 — Publish decision (drain)
Two independent questions, both must pass:
- **Classifier B (quality/factuality)** — *is this article good?* Hand-labeled
  today; **end goal: fully automatic, no human in the loop.**
- **Novelty + cooling** — *is it fresh?* Anti-repetition: if 10 candidates about
  "Pera Wallet" are queued, #1 publishes; #2–10 fail novelty until the cooling
  factor decays. (Already implemented via novelty priority, `order_for_drain`
  per-source interleave, and `_domain_in_cooldown` spacing.)

Today the publish gate is a **1-slot human review** (`classifier_review_pending`,
`MAX_PENDING_REVIEWS=1`). The migration path: Classifier B + novelty auto-publish
above a confidence threshold; the human review stays as a fallback until the
classifier is trusted, then is removed.

## What gets removed

- **Dead lanes:** Discord, Reddit, Telegram (crawlers, scrapers, OAuth, URL
  builders, poll tasks, beat entries, `CrawlerType` members, social collectors).
- **External push:** `ingest_external_signal` / `drain_external_ingest_queue` — no
  producer posts to it.
- **`is_relevant_for_enqueue`:** enqueue-time keyword heuristic that duplicates the
  trained Classifier A.
- **Generic funnel machinery:** `publish_intent` / `topic` / `event_phase`,
  edit-vs-create resolution, multi-scheme dedupe → collapse to per-lane `if`.

## What is kept (the intelligence)

- **Classifier A** — domain worthiness (Stage 1).
- **Classifier B** — article quality/factuality (Stage 3), the hand-labeled model.
- **Novelty + cooling** — publish-time diversity ranker (Stage 3).

## Consequences

- ~1,400-line funnel shrinks to three small lane handlers + two classifier calls.
- Intelligence is concentrated and legible: two places to train, one diversity rule.
- Loss of Discord/Reddit/Telegram coverage — accepted (they were non-functional).
- Auto-publish (Stage 3 endgame) is the only real *build*; the rest is deletion.

## Migration / sequence

1. Delete dead lanes (Discord/Reddit/Telegram) + external push. Pure removal.
2. Collapse `ingest_signal` to per-lane rules; drop `is_relevant_for_enqueue` and
   the intent/topic/event-phase plumbing. Keep novelty + cooling.
3. Wire Classifier B + novelty as the publish gate behind a confidence threshold;
   retire the human review slot once trusted.
