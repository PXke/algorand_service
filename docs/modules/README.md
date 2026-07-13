# Module brick registry

Each **brick** is one module: one `docs/modules/<brick-name>.md` file.

Products (what we ship) → [products-and-bricks.md](../architecture/products-and-bricks.md).

---

## Required sections in every brick doc

| Section | Purpose |
|---------|---------|
| **Goal** | One sentence — why this brick exists |
| **Status** | `done` \| `partial` \| `not_started` \| `deferred` |
| **Features (should do)** | Required behaviour for this brick to be considered working |
| **Good to have** | Valuable additions that are not blocking for v1 / freeze |
| **Future improvements** | Larger or later-phase ideas (may become new bricks) |
| **Standards & RFCs** | Normative specs to follow ([index](../architecture/standards-and-rfcs.md)); required before implementation |
| **Depends on** | Other bricks or infrastructure |
| **Code map** | Repo paths |

**Before coding:** [brick-implementation-guide.md](../architecture/brick-implementation-guide.md) + [standards-and-rfcs.md](../architecture/standards-and-rfcs.md).

Optional: mermaid diagrams, ops commands, config tables — after the sections above.

Product overview docs ([newspaper.md](newspaper.md), [suggestions.md](suggestions.md)) link to child bricks; they do not replace per-brick lists.

---

## Platform bricks

| Brick | Doc | Status |
|-------|-----|--------|
| `deployment` | [deployment.md](deployment.md) | done |
| `wallet-auth` | [wallet-auth.md](wallet-auth.md) | done |
| `wallet-auth-flutter` | [wallet-auth-flutter.md](wallet-auth-flutter.md) | done |
| `session-store` | [session-store.md](session-store.md) | done |
| `web-platform` | [web-platform.md](web-platform.md) | done |
| `cassandra-schema-migrations` | [cassandra-schema-migrations.md](cassandra-schema-migrations.md) | done |
| `cassandra-repository` | [cassandra-repository.md](cassandra-repository.md) | partial |
| `health-observability` | [health-observability.md](health-observability.md) | done |
| `celery-redis-queues` | [celery-redis-queues.md](celery-redis-queues.md) | done |
| `quality-ci` | [quality-ci.md](quality-ci.md) | done |
| `seo` | [seo.md](seo.md) | done |
| `admin` | [admin.md](admin.md) | done |
| `media-proxy` | [media-proxy.md](media-proxy.md) | done |
| `placements` | [placements.md](placements.md) | done |
| `contact` | [contact.md](contact.md) | done |

## Chain bricks

| Brick | Doc | Status |
|-------|-----|--------|
| `conduit-cassandra` | [conduit-cassandra.md](conduit-cassandra.md) | done |
| `chain-read` | [chain-read.md](chain-read.md) | done |

## Product 1 — Newspaper

| Brick | Doc | Status |
|-------|-----|--------|
| `service-registry` | [service-registry.md](service-registry.md) | done |
| `chain-tail-watcher` | [chain-tail-watcher.md](chain-tail-watcher.md) | done |
| `worker-scraper` | [worker-scraper.md](worker-scraper.md) | partial |
| `worker-pipeline` | [worker-pipeline.md](worker-pipeline.md) | partial |
| `article-compose` | [article-compose.md](article-compose.md) | partial |
| `article-store` | [article-store.md](article-store.md) | done |
| `news-api` | [news-api.md](news-api.md) | done |
| `frontend-newspaper` | [frontend-newspaper.md](frontend-newspaper.md) | partial |
| `weekly-price-analysis` | [weekly-price-analysis.md](weekly-price-analysis.md) | partial |
| `price-metrics-mistral` | [price-metrics-mistral.md](price-metrics-mistral.md) | done |
| `ai-mistral-connector` | [ai-mistral-connector.md](ai-mistral-connector.md) | partial |
| `worker-scraper-browser` | [worker-scraper-browser.md](worker-scraper-browser.md) | done |
| `gatekeeper` | [gatekeeper.md](gatekeeper.md) | partial |
| `distribution` | [distribution.md](distribution.md) | done |
| `article-translations` | [article-translations.md](article-translations.md) | done |
| Overview | [newspaper.md](newspaper.md) | — |

## Product 2 — Suggestions

| Brick | Doc | Status |
|-------|-----|--------|
| `submission-on-chain` | [submission-on-chain.md](submission-on-chain.md) | done |
| `suggestions-api` | [suggestions-api.md](suggestions-api.md) | done |
| `suggestions-store` | [suggestions-store.md](suggestions-store.md) | done |
| `upvote-offchain` | [upvote-offchain.md](upvote-offchain.md) | done |
| `frontend-suggestions` | [frontend-suggestions.md](frontend-suggestions.md) | done |
| Overview | [suggestions.md](suggestions.md) | — |

## Product 3 — Search

| Brick | Doc | Status |
|-------|-----|--------|
| `typesense-indexer` | [typesense-indexer.md](typesense-indexer.md) | partial |
| `search-api` | [search-api.md](search-api.md) | partial |
| `algorand-page-classifier` | [algorand-page-classifier.md](algorand-page-classifier.md) | partial |
| `frontend-search` | [frontend-search.md](frontend-search.md) | partial |

## Frontend shell (P1+P2)

| Brick | Doc | Status |
|-------|-----|--------|
| `frontend-shell` | [frontend-shell.md](frontend-shell.md) | done |
| `frontend-auth` | [frontend-auth.md](frontend-auth.md) | done |

## Workers (cross-cutting)

| Brick | Doc | Status |
|-------|-----|--------|
| `worker-security` | [worker-security.md](worker-security.md) | partial |

Legacy pointer: [scraper-pipeline.md](scraper-pipeline.md).

## Supporting / reference docs (not full bricks)

Notes, policies, and plans that inform one or more bricks above but don't
follow the Goal/Status/Features template themselves:

- [crawler-types.md](crawler-types.md) — crawler lane table (currently stale, see banner in the doc)
- [crawler-status.md](crawler-status.md), [crawler-toggles.md](crawler-toggles.md) (legacy), [crawler-access-strategy.md](crawler-access-strategy.md), [discovery-mode.md](discovery-mode.md), [url-queue.md](url-queue.md) — `crawler` brick internals
- [editorial-platform-vision.md](editorial-platform-vision.md), [editorial-workflow.md](editorial-workflow.md), [news-pipeline-plan.md](news-pipeline-plan.md) — product direction notes for the newspaper pivot
- [mistral-model-selection.md](mistral-model-selection.md), [publish-classifier.md](publish-classifier.md), [publish-policy.md](publish-policy.md), [writer-enrichment.md](writer-enrichment.md), [content-categorization.md](content-categorization.md) — writer/pipeline internals
- [advertisements.md](advertisements.md) — design notes behind the `placements` brick
- [scam-article-enrichment.md](scam-article-enrichment.md) — planned, not started
- [deployment-checklist.md](deployment-checklist.md) — supplements the `deployment` brick

## Deprecated — do not restore

Discord/Reddit/Telegram scraping + the external-push lane were removed in the
2026-07-06 pipeline simplification. These docs are kept only so old links
resolve; each carries a banner explaining what replaced or removed it:

- [worker-scraper-discord.md](worker-scraper-discord.md)
- [worker-scraper-discord-web.md](worker-scraper-discord-web.md)
- [worker-scraper-reddit.md](worker-scraper-reddit.md)
- [worker-scraper-telegram.md](worker-scraper-telegram.md)
- [push-ingest.md](push-ingest.md)
- [local-browser-bridge.md](local-browser-bridge.md)
- [firefox-channel-sync.md](firefox-channel-sync.md) — the `extensions/algorand-channel-sync/` code is orphaned, not deleted
