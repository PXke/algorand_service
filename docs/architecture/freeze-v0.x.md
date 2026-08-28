# Freeze checklist — v0.x (P1 + P2 joint ship)

> **Historical snapshot.** This checklist reflects the stack at the v0.x
> freeze gate, when the client was the Flutter app and the API ran on Robyn.
> The Flutter frontend has since been fully replaced by the Vite + Svelte
> `frontend/`, and the API migrated to Falcon + gunicorn. Checked items below
> record what was true and verified *then*, not current tech — do not use
> this as a description of the current stack.

Target: end of current **2-week dev window** before TestNet validation. See [release-cadence.md](release-cadence.md).

## Product 0 — Wallet auth

- [x] SIWA / CAIP-122 + ARC-0060 + ARC-0025 fallback
- [x] Flutter `wallet_auth_flutter` + auth panel
- [ ] `AUTH_DOMAIN` / `apiBaseUrl` aligned for deployed Flutter web host

## Product 1 — Newspaper (minimal)

- [x] `GET /api/v1/news/feed` + `GET /api/v1/news/articles/{id}` from Cassandra
- [x] Flutter feed + article detail
- [x] `chain-tail-watcher` → registry match → `publish_from_chain_event`
- [x] Cassandra articles / snapshots / service_events (migrations `003`–`007`)
- [x] Template + diff articles (no LLM)
- [x] Celery beat schedule + `algorand-platform-celery-beat.service`
- [x] Playwright scraper for SPAs (`worker-scraper-browser`, `CRAWLER_WEB_SPA_ENABLED`)
- [x] LLM article generation (Mistral connector; template fallback removed 2026-07-14 — Mistral is now required, no fallback)

## Product 2 — Suggestions + votes

- [x] POST/GET suggestions API
- [x] Treasury `pay` verification (`PLATFORM_TREASURY_ADDRESS`, `SUGGESTION_MIN_MICROALGOS`)
- [x] POST upvote + GET upvote signing message
- [x] In-memory stores (default); Cassandra schema + stores when `SUGGESTION_STORE=cassandra`
- [x] Flutter suggestions board (list, submit txid, upvote with signature)
- [ ] Wallet-native upvote signing in `wallet_auth_flutter` (paste signature OK for TestNet)

## Chain index (Conduit)

- [x] `transactions_by_id` + sender/round tables
- [x] `receiver`, `amount_microalgos`, `transactions_by_receiver` (exporter + CQL)
- [ ] Conduit binary built/deployed on TestNet host
- [ ] CQL applied via `cql-migrate` ledger (not monolith `.cql`); baseline registered on existing TestNet

## Platform

- [x] Robyn `ALLOW_CORS` via `CORS_ALLOWED_ORIGINS`
- [x] Celery `process_new_rounds` + beat schedule
- [x] `/health/ready` (redis, cassandra, typesense, conduit index)
- [x] Search API + Typesense index task (classifier deferred)
- [x] GitHub CI (pytest, ruff, vulture, go test, flutter analyze)
- [ ] Production systemd smoke on TestNet host

## Test plan (freeze gate)

1. Wallet connect → session on Flutter web against API with CORS.
2. Pay ≥ 0.01 ALGO to treasury from session wallet; submit suggestion with txid.
3. Fetch upvote message; sign in wallet; POST upvote; confirm duplicate rejected.
4. Conduit ingests block containing payment; re-submit same txid → 409.
5. Celery worker runs `app.tasks.chain_tail.poll_new_blocks` and advances Redis key.
