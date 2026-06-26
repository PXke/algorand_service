# Publish policy — queue, priority, and daily cap

The news feed stays readable by **enqueueing** signals from many sources, **scoring** them on one scale, and **draining** at most **7 articles per UTC day** (configurable). A burst of 1,000 first-time discoveries becomes a ranked backlog, not 1,000 same-day posts.

## Target model (from scratch)

Everything is the same pipeline: **ingest → normalize → score → queue → drain → compose**. Sources differ only in how we obtain the `Signal` and which compose template we use.

### Sources (ingestion lanes)

| Lane | What it watches | Typical signals |
|------|-----------------|-----------------|
| **Web** | Registry `scrape_url` (static HTML or SPA via Playwright later) | Site change, launch page, docs update |
| **Reddit** | `reddit://…` poll | Posts, announcements, scam warnings |
| **Discord** | `discord://…` poll | Alerts, calls, release chatter |
| **Mail** | Dedicated inbox (Foundation + partners) | Official community calls, governance, releases |
| **Chain** | Tx match on registry (no URL required) | “This app/asset moved” → optional web follow-up |

Chain does not replace crawling: it **wakes up** a service (scrape or skip if we already have mail/Discord for that service). Mail and official senders are the **fast lane** for Foundation community calls.

### One priority score (not five separate systems)

Each queue item gets a single integer (or float) used for ordering:

```
priority = topic_base
         + source_trust
         + service_weight
         + urgency_bonus
         - noise_penalty
```

| Component | Meaning | Examples |
|-----------|---------|----------|
| **topic_base** | What happened | scam 100, SDK 90, community call 85, pricing 75, discovery 80, generic update 55 |
| **source_trust** | How authoritative the lane is | official mail +25, Foundation Discord +20, partner mail +15, Reddit +5, anonymous web +0 |
| **service_weight** | How “substantial” the service looks | rich site/docs +15, thin placeholder −20 (see below) |
| **urgency_bonus** | Time-sensitive | event in &lt;48h +10, scam +10 (already high topic) |
| **noise_penalty** | Low value | tiny diff, boilerplate footer, 5-line stub page −15 |

**Service impressiveness (heuristic v1):** after first successful scrape, compute `service_weight` from signals only — no ML required initially:

- Text length and section count (headings, nav labels)
- Presence of docs/pricing/github links
- Negative: &lt;500 chars visible text, lorem ipsum, “coming soon” only
- Store on registry row or `service_profiles` table; refresh on each crawl

A five-line HTML stub discovery ranks **below** a 20-page docs site even when both are `new_service`.

### Event lifecycle (two articles for community calls)

Community content is not one article type; it is a **lifecycle**:

1. **Announce** — “Community call on {date}” from mail (preferred) or Discord/web. High priority, publish quickly (inline drain or dedicated fast queue).
2. **Recap** — After the call, a **video URL** appears (YouTube, etc.). Separate queue item, topic `community_recap`, linked to the same `event_id`. Compose from transcript/summary (Mistral or template + manual QA later).

Dedupe keys: `event_id:phase:announce` vs `event_id:phase:recap` so we never collapse the two into one.

### What “archive the rest” means

Not published ≠ deleted. States:

| State | Meaning |
|-------|---------|
| `pending` | In queue, eligible when score × slots allow |
| `deferred` | Valid signal but below cutoff; retry next week or when cap opens |
| `indexed_only` | Worth search/snippets, no feed article (thin discovery, minor footer diff) |
| `done` | Published |
| `expired` | Event passed (announce after call date) → skip or auto-recap-only |

Daily drain publishes top N by score. Remaining `pending` stays ordered. **`deferred`** is for items that scored above noise floor but below the day’s cutoff — optional second table or `priority` band 0–39.

### Source-specific rules (quick reference)

- **Official mail (Algorand Foundation):** community call / governance → topic `community_event`, `source_trust` max, immediate drain.
- **SDK release:** any lane; topic `sdk_release`; high base; prefer mail or GitHub release link in body.
- **Scam:** Discord/Reddit first; always top of queue same day if slots remain.
- **Web SPA vs non-SPA:** same scoring; only ingest differs (Playwright vs HTTP). Classifier gates **search index**, not necessarily feed.
- **Chain-only service:** enqueue `chain_activity` with low base unless followed by rich scrape or mail.

### Implementation map

| Piece | Status |
|-------|--------|
| Queue + standard/breaking caps + 3h spacing | Done |
| Breaking credibility (heuristic + optional Mistral) | Done |
| Full priority score (topic + trust + service + urgency − noise) | Done (`publish_score.py`) |
| Service profiles table | Done (`011_service_profiles.cql`) |
| Mail IMAP poll | Done (`mail_poll_tasks.py`, env-gated) |
| Telegram poll (Bot API stub) | Done (`telegram_poll_tasks.py`, env-gated) |
| `event_id` + announce/recap lifecycle | Done (`event_lifecycle.py`) |
| Recap compose template | Done (`community_recap_compose.py`) |
| Video transcript recap | Planned |
| `deferred` / `indexed_only` states | Planned |
| Link-following breaking case file | Planned |

See [news-pipeline-plan.md](news-pipeline-plan.md) for phased roadmap.

## Article types

| Type | `publish_kind` | When | Daily cap |
|------|----------------|------|-----------|
| **Weekly digest** | `weekly_digest` | Once per ISO week (Monday beat). ~1,500 character recap. | Exempt (1/week) |
| **Service discovery** | `service_discovery` | First profile or launch-style source text. | Counts toward cap |
| **Content update** | `content_update` | Meaningful page diff on a known source. | Counts toward cap |

## Topics and priority (queue order)

Higher `priority` values are published first when the drain task runs.

| Topic | Typical signal | Priority |
|-------|----------------|----------|
| `scam_alert` | scam, phishing, fraud, rug, exploit | 100 |
| `sdk_release` | SDK, changelog, GitHub release, semver | 90 |
| `community_event` | community call, AMA, webinar, “in N days” | 85 |
| `new_service` | `service_discovery` profile | 80 |
| `pricing_change` | pricing, fees, subscription language | 75 |
| `content_update` | generic meaningful diff | 55 |
| `generic` | fallback | 40 |

**Breaking drain:** scam / network incident items enqueue with `tier=breaking` and trigger `drain_breaking_publish_queue` immediately (no 3h wait).

## Strict daily cap (never exceed 7 standard)

Multiple layers enforce **at most 7 standard articles per UTC day** (config cannot raise above 7):

| Layer | Behavior |
|-------|----------|
| Queue drain | At most **1** standard article per drain run; stops when cap hit |
| Policy check | `evaluate_standard_publish` + 3h spacing |
| **Redis guard** | `reserve_publish_slot()` atomically increments daily counter before `insert_article` |
| Crawl pause | When standard cap full, `run_publish_pipeline` skips scrape (`CRAWL_PAUSE_WHEN_PUBLISH_CAP_FULL`) |

`NEWS_STRICT_DAILY_CAP=1` (default). Breaking uses a **separate** cap (`NEWS_MAX_BREAKING_PER_DAY`, default 2). Weekly digest is exempt.

Inline breaking drain on ingest is **off** by default (`BREAKING_INLINE_DRAIN=0`) so crawls do not burst-publish.

## Two lanes: standard vs breaking

| Lane | Daily max | When it publishes | Tags |
|------|-----------|-------------------|------|
| **Standard** | `7` (`NEWS_MAX_ARTICLES_PER_DAY`) | ~every **3 hours** (`NEWS_STANDARD_INTERVAL_HOURS`); fewer if queue empty | `discovery`, `update`, … |
| **Breaking** | `2` (`NEWS_MAX_BREAKING_PER_DAY`) | **Any time** — drain every 2 min + inline on enqueue | `breaking`, `scam-alert`, … |

Breaking items: `scam_alert`, `network_incident` (chain down / outage language). Before publish, `breaking_credibility.py` runs heuristics or Mistral (`BREAKING_MISTRAL_CREDIBILITY=1`) so low-evidence Discord/Telegram noise is not promoted.

Standard and breaking caps are **independent** (up to 7 + 2 articles per UTC day, plus weekly digest).

## Default limits (env)

| Variable | Default | Meaning |
|----------|---------|---------|
| `NEWS_MAX_ARTICLES_PER_DAY` | `7` | Max **standard** articles per UTC day |
| `NEWS_MAX_BREAKING_PER_DAY` | `2` | Max **breaking** articles per UTC day |
| `NEWS_STANDARD_INTERVAL_HOURS` | `3` | Minimum hours between standard publishes |
| `NEWS_MIN_DIFF_LINES` | `3` | Minimum added diff lines to enqueue a content update |
| `PUBLISH_QUEUE_DRAIN_SECONDS` | `900` | Beat interval for `drain_standard_publish_queue` |
| `PUBLISH_BREAKING_DRAIN_SECONDS` | `120` | Beat interval for `drain_breaking_publish_queue` |
| `BREAKING_MISTRAL_CREDIBILITY` | `0` | When `1` and Mistral enabled, gate breaking on LLM verdict |
| `PUBLISH_QUEUE_BATCH_LIMIT` | `50` | Max pending rows considered per drain run |
| `WEEKLY_DIGEST_MAX_BODY_CHARS` | `1500` | Weekly digest body target |

## Crawl / scrape flow

1. Worker polls Reddit, Discord, or web (`scrape_url`).
2. Compare snapshot hash → unchanged → no queue entry.
3. Classify **publish kind** and **topic** (priority).
4. **Enqueue** if the change passes quality gates (significant diff for updates).
5. **Drain** — standard beat respects 3h spacing; breaking beat publishes up to 2/day anytime.
6. Compose with topic-aware templates (scam alert, content update “what changed”, discovery profile).
7. Insert article + search index.

Dedupe: `service_id:topic:content_hash_prefix` — duplicate pending crawls for the same change are not enqueued twice.

## Content updates

Update articles use **before/now** editorial framing (`content_update_compose.py`): pricing shifts, SDK/release lines, community events, or excerpted added diff lines — not pipeline metadata.

## Discord and Reddit

Discord polls use the same pipeline. Scam-language in channel text elevates to `scam_alert` and highest queue priority (tags: `scam-alert`, `discord`).

## Mail / Telegram (planned)

- **Mail:** official Foundation mail → high trust, often `community_event` (standard) or breaking if safety incident.
- **Telegram:** `telegram://` scraper (same queue); breaking scams benefit from Mistral + link extraction in `breaking_credibility.py`.

## Weekly digest

Idempotent per week; does not use the publish queue or daily cap.

## Code

- `workers/app/modules/newspaper/publish_policy.py`
- `workers/app/modules/newspaper/publish_queue_store.py`
- `workers/app/modules/newspaper/content_update_compose.py`
- `workers/app/modules/newspaper/tasks/publish_tasks.py`
- `workers/app/modules/newspaper/tasks/queue_drain_tasks.py`
- `workers/app/modules/newspaper/publish_schedule.py`
- `workers/app/modules/newspaper/breaking_credibility.py`
- `backend/schema/migrations/app/010_publish_queue.cql`
