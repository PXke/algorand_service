# Advertisements & sponsored placements

Editorial articles and ads are **separate products**. Readers must always see what is news vs what is paid placement.

## Models (phased)

| Phase | Model | Who controls |
|-------|--------|--------------|
| **1 — Sponsored placements** (v1) | First-party cards in the news feed | Admin / wallet-gated API later |
| **2 — Partner campaigns** | Scheduled slots, impression caps | Cassandra `feed_placements` |
| **3 — Ad network** (optional) | AdSense / Carbon / custom SDK | Privacy policy + consent banner (EU) |

We start with **Phase 1**: ecosystem partners (wallets, DEXs, events) as labeled **Sponsored** blocks — not mixed into the article queue.

## Feed rules

- Placements are **not** `publish_queue` items and **do not** count toward 7+2 article caps.
- Insert every N articles (default **5**) on `/news`.
- Clear label: **Sponsored** (l10n `newsSponsoredLabel`).
- `rel="sponsored"` semantics on outbound links (web).
- No placement in **breaking** alert styling (avoid confusion with scam/news).

## Schema (`012_feed_placements.cql`)

| Field | Purpose |
|-------|---------|
| `placement_id` | UUID |
| `slot` | e.g. `news_feed_inline` |
| `sponsor_name` | Display name |
| `headline`, `body`, `image_url`, `target_url` | Creative |
| `priority` | Higher wins when multiple active |
| `active_from`, `active_until` | UTC window |
| `enabled` | Kill switch |

## API

- `GET /api/v1/news/placements?slot=news_feed_inline` — active placements for Flutter

## Admin (future)

- CRUD under `/admin` with `ADMIN_WALLET_ADDRESSES`
- Preview + schedule campaigns
- Separate from `service_registry` news sources

## Compliance

- Algorand ecosystem ads should follow ASA / regional marketing rules (project responsibility).
- Track `placement_id` on click analytics (optional, privacy-minimal).
- Cookie/consent only required when integrating third-party ad networks (Phase 3).

## Code map

- `backend/schema/migrations/app/012_feed_placements.cql`
- `backend/app/modules/placements/`
- `frontend_flutter/lib/modules/newspaper/ui/feed_placement_card.dart`
- `frontend_flutter/lib/modules/newspaper/ui/news_page.dart` (interleave)
