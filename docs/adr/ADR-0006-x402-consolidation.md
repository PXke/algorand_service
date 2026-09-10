# ADR-0006: x402 marketplace consolidated to three products of our own

Date: 2026-09-10. Status: accepted (owner decision, same day).

## Context

By 2026-09-07 the x402 marketplace had grown to ten products and 73 routes
under one `payTo`: the catalog (with a `ping` route), an endpoint
directory (pay to list other people's x402 endpoints, free search, a
worker beat probing every listing every 30 minutes), a visibility board
(paid tiles), a feature-request board (free filing, paid votes/claims/
demand reads), endpoint grading (paid grades with on-chain
proof-of-payment, paid weighted scores), a paid uptime check, a 37-route
agent social network with paid-stake moderation, Know Your Agent identity
(`kya/`, gated off in prod), signed fulfillment receipts, and the three
products that do work for a paying agent: the News Engine, the sandboxed
file/tarball scan, and agent backup storage.

Seven of those products were marketplace mechanics — infrastructure for a
two-sided market whose second side never showed up. A live-data audit on
2026-09-10 against prod Cassandra found:

- 1 directory listing ever stored, and it was our own news search route.
- 3 feature requests ever, 2 of them our own probes.
- 0 board placements.
- 26 social-network agents, every one named `ModeratorAgent*`, `Voter*` or
  `*TestAgent` — test data from our own moderation test runs.
- 0.373 USDC lifetime merchant volume on the facilitator, all of it from
  our own labelled probe wallets.

Meanwhile the facilitator's Bazaar catalogued 7 of our routes, five of
which belonged to the mechanics products, and the facilitator's OpenAPI
(`/docs/openapi.json`, verified 2026-09-10) exposes no delete or
unregister endpoint — the discovery namespace is read-only (`GET
/discovery/*`, `GET /data/*`). Every route we advertise is therefore a
permanent public commitment we cannot retract.

The mechanics also carried most of the code and all of the operational
surface: eight backend modules, a worker beat, eight statement classes,
27 social tables plus ~23 others, a Flutter KYA frontend, five frontend
routes, ~40 settings, and the settlement ledger's store setting reused
from the directory module (`x402_directory_store`), so the ledger's
durability was coupled to a product that was about to go.

The owner's read (session 2026-09-10): cut the marketplace down to the
products that do real work for a paying agent, and stop maintaining a
marketplace for a market that does not exist.

## Decision

1. **Keep**, unchanged paths: `x402_news` (4 routes), `x402_scan`
   (`POST /api/v1/x402/scan/url`), `x402_storage` (9 routes plus its admin
   inspect/remove and the internal reap route), the catalog
   (`GET /api/v1/x402`, `GET /api/v1/x402/settlements` and its `/recent`
   alias, admin promo and refund-breaker routes), and `x402_wellknown`
   (`/.well-known/x402`, `/openapi.json`, minus the
   `x-x402-supports-receipts` field). The catalog keeps exactly two
   sections: `meta` ("Start here") and `services` ("Services").
2. **Remove, code and tables**: backend modules `x402_directory`,
   `x402_board`, `x402_features`, `x402_grading`, `x402_social`,
   `x402_uptime`, `kya`, `x402_receipts`; `modules/x402/receipts.py` and
   `receipt_store.py` and their hook in `paid_request.run_with_refund`; the
   `GET /api/v1/x402/ping` route; the admin handlers that lived in those
   modules plus `admin_x402_social_moderation_remove` and
   `/api/v1/admin/kyc/payouts/retry`; `workers/app/modules/x402_probe/`,
   its task, tests, backfill script and the `x402-probe-listed-endpoints`
   beat; `shared/algorand_shared/x402_statements.py`; `frontend_kyc/`; the
   directory/listing/requests/register frontend routes and their API
   helpers; every setting only those modules read; the statement classes
   `KycStmts`, `X402DirectoryStmts`, `X402BoardStmts`, `X402FeaturesStmts`,
   `X402ReceiptStmts`, `X402GradingStmts`, `X402SocialStmts`,
   `X402UptimeHistoryStmts`.
3. **Settlement ledger gets its own store setting**:
   `x402_settlement_store` (env `X402_SETTLEMENT_STORE`, default `memory`)
   replaces the reuse of `x402_directory_store`. `_register_x402_routes`
   raises at startup when it is `memory` outside `APP_ENV=dev` — a prod
   process must never run paid routes against an in-memory ledger
   (CLAUDE.md §9: every settlement logged).
4. **Helpers kept code still imports are relocated, not rewritten**:
   `x402_social/services/markdown_guard.py` →
   `ecosystem/services/markdown_guard.py` (with its own
   `MarkdownRejected` error instead of `SocialError`);
   `x402_uptime/services/checker.py:check_target` →
   `ecosystem/services/checker.py` (used by the Open Registry's liveness
   check). Their tests move alongside.
5. **Not touched**: `x402_storage_reaper` and `ecosystem_probe` in workers
   (the Algorand Open Registry, roadmap item 26, is a separate free
   product); `modules/x402/probe_payers.py` and `X402_PROBE_PAYERS` (the
   settlement ledger still labels our own operator wallets so they are
   excluded from the public settlements feed); `lib/x402/pay.ts` in the
   frontend (the browser-side payment flow, reusable on the product pages).
6. **No removed item is rebuilt without a new owner decision.** CLAUDE.md
   §9.1 strikes the affected roadmap items in place.

## Consequences

- **Bazaar stale entries.** As of 2026-09-10 the facilitator still lists
  `POST /api/v1/x402/list`, `GET /api/v1/x402/features/demand`,
  `GET /api/v1/x402/grades/score`, `POST /api/v1/x402/grades`,
  `POST /api/v1/x402/board` and `GET /api/v1/x402/ping` for our merchant;
  all six `404` after the next deploy, and only `GET /api/v1/x402/news/search`
  of the catalogued seven survives. There is no unregister call; the
  entries age out on the facilitator's schedule or are purged by
  GoPlausible on request. Corollary for the future: every URL we advertise
  in a 402 offer is a public commitment we cannot retract.
- **Prod env change before deploy**: `X402_SETTLEMENT_STORE=cassandra`
  must be added to the backend env on 5.135.131.229 (documented next to
  the other `X402_*` lines in `deploy/env/backend.env.example`), or the
  backend refuses to start its paid routes. This is deliberate.
- **Migration 126** (`backend/schema/migrations/app/126_drop_removed_x402_products.cql`,
  manifest tier prod, status active) drops every table the removed
  modules owned — the directory, board, feature, grade, probe, receipt,
  uptime and KYC tables and the 27 `x402_social_*` tables. Kept:
  `x402_settlements`, `x402_settlements_by_tx`, `x402_promo_codes`,
  `x402_promo_redemptions_v2` and the four `x402_storage_*` tables. Prod
  data in the dropped tables was verified to be test data only (numbers
  above). Old migration files stay as-is.
- **`x402-client` breaking minor bump** (from 0.6.0): every method for a
  removed product is gone (`ping`, `list_endpoint`, `search`, `board`,
  `features`, `grades`, `probe`, `submit_grade`, `place_on_board`,
  `file_feature_request` and the rest); `scan_url` is added;
  catalog/settlements/news/storage stay.
- **`x402.pxke.me` becomes a per-product storefront**: `/` (the three
  products and how to pay), `/scan`, `/storage`, `/news` (one page per
  product with prices read live from the catalog, curl + SDK snippets,
  limits, free preview where supported), `/developers`. Legacy paths
  (`/directory`, `/listing`, `/requests`, `/list`, `/x402`, `/x402/*`)
  redirect to `/`. The news domain drops its own `/x402` pages (nginx
  already 301s them). The x402 sitemap lists exactly those five paths.
- Docs rewritten to the new surface: `docs/x402-marketplace-api.md`,
  `docs/x402-quickstart.md`, `docs/x402-new-endpoint-checklist.md`; dated
  note in `docs/x402-facilitator.md`; the design/audit docs that only
  described removed products are deleted (`x402-social-design.md`,
  `x402-uptime-check-design.md`, `x402-marketplace-ux-audit.md`,
  `x402-marketplace-product-redesign.md`, `x402-execution-trust-evaluation.md`);
  research docs that discuss removed products keep a dated header note.
- Bookkeeping continuity: the settlement ledger, promo codes, refund leg,
  circuit breaker, replay guard, preview and price oracle are unchanged,
  so every settlement before and after this change reads the same way.

## Removed → where it went

| Removed | Where it went |
|---|---|
| `backend/app/modules/x402_directory/` | deleted (tables dropped by migration 126) |
| `backend/app/modules/x402_board/` | deleted (tables dropped) |
| `backend/app/modules/x402_features/` | deleted (tables dropped) |
| `backend/app/modules/x402_grading/` | deleted (tables dropped) |
| `backend/app/modules/x402_social/` | deleted (27 tables dropped) |
| `x402_social/services/markdown_guard.py` | moved to `backend/app/modules/ecosystem/services/markdown_guard.py` (`MarkdownRejected` replaces `SocialError`); tests moved with it |
| `backend/app/modules/x402_uptime/` | deleted (table dropped) |
| `x402_uptime/services/checker.py:check_target` | moved to `backend/app/modules/ecosystem/services/checker.py`; `ecosystem/services/liveness.py` imports from there; `test_checker.py` moved alongside |
| `backend/app/modules/kya/` (ex `kyc/`) + `frontend_kyc/` | deleted (tables dropped); KYA never ran in prod |
| `backend/app/modules/x402_receipts/`, `x402/receipts.py`, `x402/receipt_store.py` | deleted; `run_with_refund` no longer signs receipts; `x-x402-supports-receipts` dropped from OpenAPI |
| `GET /api/v1/x402/ping` + `_ping_product_write` | deleted; `GET /api/v1/x402/news/search?preview=true` is the free smoke test now |
| `workers/app/modules/x402_probe/`, `tasks/x402_probe.py`, the beat, `scratch/backfill_x402_probe_queue.py` | deleted |
| `shared/algorand_shared/x402_statements.py` | deleted (only the probe used it) |
| `x402_directory_store` (as the ledger's store setting) | replaced by `x402_settlement_store` / `X402_SETTLEMENT_STORE` |
| catalog sections `discover`, `trust`, `network` | dropped; `meta` and `services` remain |
| frontend `Directory.svelte`, `ListingDetail.svelte`, `Requests.svelte`, `Register.svelte`, `X402RegisterForm.svelte`, listing helpers in `lib/api/x402.ts` | deleted; `lib/x402/pay.ts` kept |
| news-domain `routes/X402.svelte`, `X402Endpoints.svelte`, SSR `render_x402` | deleted; one client-side redirect to `config.marketplaceSiteUrl` remains |
| `docs/x402-social-design.md`, `x402-uptime-check-design.md`, `x402-marketplace-ux-audit.md`, `x402-marketplace-product-redesign.md`, `x402-execution-trust-evaluation.md` | deleted; the two protocol facts they held that appeared nowhere else (browser-side partial-group signing; settlement memo fixed at signing time) moved to `docs/x402-facilitator.md` |
