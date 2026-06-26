# News pipeline — implementation plan

Single pipeline for all sources: **ingest → normalize → score → queue → drain → compose**.

## Phases

### Phase 1 — Foundation (done)

- [x] Publish queue (`010_publish_queue.cql`)
- [x] Topic classification (scam, SDK, community, pricing, discovery, update)
- [x] Standard lane: max 7/day, ~3h spacing (`publish_schedule.py`)
- [x] Breaking lane: max 2/day, immediate + credibility gate (`breaking_credibility.py`)
- [x] Editorial compose (discovery, content update, scam)

### Phase 2 — Scoring (done)

- [x] `source_trust` from lane + official allowlists (env)
- [x] `service_weight` from impressiveness heuristic + `service_profiles` table
- [x] Combined `priority` in `publish_score.py` used by `build_publish_intent()`
- [x] Urgency bonus (regex: “in N days”, today/tomorrow + call)

### Phase 3 — Ingestion lanes (done)

- [x] Web / Reddit / Discord (existing)
- [x] Mail IMAP poll → `ingest_publish_signal()` (env-gated)
- [x] Telegram poll (`telegram://`, Bot API getUpdates when configured)
- [x] Shared ingest (`ingest_signal.py`) for all lanes
- [x] Chain wake → scrape with `chain_activity` topic (`round_num > 0` → topic when no stronger match)
- [x] SPA / Playwright web (`worker-scraper-browser` brick; `CRAWLER_WEB_SPA_ENABLED`)

### Phase 4 — Event lifecycle (done)

- [x] `event_id` + `event_phase` (`announce` | `recap`) on queue payload
- [x] `community_recap` topic when video URL detected post-call
- [x] Recap compose template (`community_recap_compose.py`)
- [x] Recap from video transcript (`compose_recap_from_transcript_mistral`, premium model; pass `transcript_text` in ingest payload)

### Phase 5 — Archive & defer (done)

- [x] Queue status `deferred` / `indexed_only` for low-score items (`expire_stale_queue_items` beat task; `PUBLISH_DEFER_PRIORITY_THRESHOLD`, `PUBLISH_DEFER_AFTER_HOURS`)
- [x] Expire stale `announce` rows (`expired` status after `PUBLISH_ANNOUNCE_EXPIRE_HOURS`)

### Phase 6 — Breaking depth (done)

- [x] Follow links in source text before Mistral credibility (`follow_link_evidence`, `BREAKING_FOLLOW_LINKS`)
- [x] Official Discord channel map in admin/registry (`official_channels` table + `/api/v1/admin/official-channels`; union with env allowlists)

### Crawler access (compliance)

See [crawler-access-strategy.md](crawler-access-strategy.md), [crawler-status.md](crawler-status.md), [worker-scraper-telegram.md](worker-scraper-telegram.md). Implemented: `http_retry.py`, `scrape_cooldown.py`, Reddit OAuth optional. **No** proxy/stealth/CAPTCHA pipeline.

### Advertisements

See [advertisements.md](advertisements.md). Implemented: `012_feed_placements`, API, Flutter sponsored cards.

## Priority formula

```
priority = topic_base + source_trust + service_weight + urgency_bonus - noise_penalty
```

Capped 0–200 for queue ordering. Breaking tier still driven by topic (`scam_alert`, `network_incident`), not raw score.

## Environment

| Variable | Purpose |
|----------|---------|
| `NEWS_MAX_ARTICLES_PER_DAY` | Standard cap (7) |
| `NEWS_MAX_BREAKING_PER_DAY` | Breaking cap (2) |
| `NEWS_STANDARD_INTERVAL_HOURS` | 3h between standard posts |
| `OFFICIAL_DISCORD_CHANNEL_IDS` | Comma-separated channel IDs (+20 trust) |
| `OFFICIAL_MAIL_FROM_DOMAINS` | e.g. `algorand.foundation` (+25 trust) |
| `MAIL_IMAP_HOST` | Empty = mail poll skipped |
| `TELEGRAM_BOT_TOKEN` | Empty = telegram poll skipped |
| `BREAKING_FOLLOW_LINKS` | Fetch linked pages as credibility evidence (default on) |
| `BREAKING_FOLLOW_LINKS_MAX` | Max links fetched per assessment (2) |
| `PUBLISH_DEFER_PRIORITY_THRESHOLD` | Below this score items can be deferred (45) |
| `PUBLISH_DEFER_AFTER_HOURS` | Age before low-score items defer (24) |
| `PUBLISH_ANNOUNCE_EXPIRE_HOURS` | Age before stale `announce` rows expire (72) |
| `PUBLISH_QUEUE_MAINTENANCE_SECONDS` | `expire_stale_queue_items` beat interval (3600) |

Official channel allowlists are also managed at runtime via the admin API
(`/api/v1/admin/official-channels`, kinds: `discord`, `telegram`, `mail_domain`) and
merged with the env values.

## Code map

| Module | Role |
|--------|------|
| `publish_policy.py` | Topics, tiers, enqueue gates |
| `publish_score.py` | Full priority integer |
| `source_trust.py` | Lane + official boosts |
| `service_profile.py` | Impressiveness heuristic |
| `service_profile_store.py` | Cassandra persistence |
| `event_lifecycle.py` | announce/recap detection |
| `ingest_signal.py` | Shared enqueue after scrape/mail |
| `breaking_credibility.py` | Breaking publish gate |
| `publish_schedule.py` | Standard 3h spacing |
| `publish_queue_store.py` | Queue CRUD |
| `tasks/publish_tasks.py` | Web/social crawl entry |
| `tasks/mail_poll_tasks.py` | IMAP poll |
| `tasks/telegram_poll_tasks.py` | Telegram poll |
