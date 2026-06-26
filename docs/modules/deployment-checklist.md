# Deployment checklist — Algorand Platform (TestNet / production)

Step-by-step gate for shipping the platform (newspaper + suggestions + search) to a
real host. Mechanics live in [deployment.md](deployment.md) and
[deploy/README.md](../../deploy/README.md); this page is the ordered checklist.

## 1. Pre-flight (workstation)

- [x] `make docker-test` green (canonical lint + pytest in container)
- [x] `flutter analyze` clean in `frontend_flutter/`
- [x] CQL migration ledger up to date: `python deploy/scripts/cql_migrate.py status`
  ```
  (new tables this cycle: `019_official_channels.cql`)
  ```
- [x] Version tag created (release cadence: [release-cadence.md](../architecture/release-cadence.md))
- [x] `deploy/package.sh` builds tarball + `sha256`

## 2. Host prerequisites

- [ ] Linux host with systemd, `rsync`, SSH for `SERVICE_USER` (+ `ROOT_USER` for apt/certbot/nginx/units)
- [ ] Shared Python venv at `$TARGET_PATH/venv` (created by `deploy.sh deploy`; prod: `/home/guillaume/algorand-platform/venv`)
- [ ] Cassandra 4.1+ reachable (`CASSANDRA_HOSTS`), keyspace `algorand_platform` + `algorand` role created (auth required; see deploy/README.md "Cassandra role")
- [ ] Redis reachable (prod host: DB 10 app, DB 11 broker, DB 12 results — 0/1 belong to other apps)
- [ ] Typesense reachable (optional but recommended — search falls back to feed scan)
- [ ] Conduit binary built and deployed (`algorand-platform-conduit.service`), pointed
  ```
  at TestNet algod; verify `conduit_meta.last_ingested_round` advances
  ```
- [ ] Disk: ≥ 50 GiB free for Cassandra (commitlog corruption risk when full —
  ```
  `disk_failure_policy=stop` halts the node)
  ```

## 3. Algorand chain administration

The platform is **administered through the Algorand blockchain** — no
username/password admin accounts:

- [ ] **Admin wallets** — set `ADMIN_WALLET_ADDRESSES` (comma-separated Algorand
  ```
  addresses, backend env). Holders of these wallets can:
  - edit articles (`PATCH /api/v1/admin/articles/:id`, header `X-Admin-Wallet`)
  - manage editorial briefs (`/api/v1/admin/briefs`)
  - manage the official channel allowlist (`/api/v1/admin/official-channels`,
    kinds `discord` | `telegram` | `mail_domain`)
  - review classifier feedback (`/api/v1/admin/classifier-feedback`)
  ```
- [ ] **Platform treasury** — set `PLATFORM_TREASURY_ADDRESS` to a wallet you control.
  ```
  Suggestions require an on-chain `pay` of ≥ `SUGGESTION_MIN_MICROALGOS`
  (default 10 000 µALGO = 0.01 ALGO) to this address; the API verifies the
  indexed txn (sender = session wallet, receiver = treasury, type = pay) and
  rejects duplicate txids.
  ```
- [ ] **Wallet auth (SIWA)** — align `AUTH_DOMAIN` / `AUTH_URI` with the deployed
  ```
  Flutter web origin; `AUTH_CAIP2_CHAIN_ID` matches the target network
  (TestNet: `algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDe`)
  ```
- [ ] **Node access** — `ALGOD_URL` (e.g. `https://testnet-api.algonode.cloud`),
  ```
  `ALGOD_TOKEN` if the node requires one
  ```
- [ ] Verify end to end after deploy: wallet connect → session; treasury payment →
  ```
  suggestion accepted; admin wallet header accepted on an admin endpoint;
  non-admin wallet rejected (403)
  ```

## 4. Mistral AI key (LLM article generation)

1. Create an API key in the [Mistral console](https://console.mistral.ai/) (API Keys).
2. On the host, add to the **workers** env (systemd unit env file or `.env`):
  ```bash
   MISTRAL_ENABLED=1
   MISTRAL_API_KEY=<your-key>          # keep out of git; chmod 600 the env file
   # optional overrides
   MISTRAL_MODEL=mistral-small-latest          # scrape articles
   MISTRAL_MODEL_BREAKING=mistral-small-latest # breaking credibility JSON
   MISTRAL_MODEL_DIGEST=mistral-small-latest   # weekly digest
   MISTRAL_MODEL_PREMIUM=mistral-medium-latest # transcript recaps
   MISTRAL_FALLBACK_TEMPLATE=1                 # template fallback on API errors
  ```
3. Restart workers, then verify: trigger a publish and check the Celery result for
  `"composer": "mistral"` (template fallback shows `"template"`).
4. Optional gates that use the key: `BREAKING_MISTRAL_CREDIBILITY=1` (Mistral verdict
  before breaking publishes — link evidence is fetched when `BREAKING_FOLLOW_LINKS=1`).

Leave `MISTRAL_ENABLED=0` to run fully template-based (no external AI calls).

## 5. Backend env (API)

- [ ] `APP_ENV=prod`, `APP_HOST`, `APP_PORT`
- [ ] `NEWS_STORE=cassandra`, `SUGGESTION_STORE=cassandra`, `UPVOTE_STORE=cassandra`
- [ ] `CORS_ALLOWED_ORIGINS` = deployed Flutter web origin(s) only
- [ ] `INGEST_API_KEY` set (push ingest auth, header `X-Ingest-Key`)
- [ ] Typesense: `TYPESENSE_HOST/PORT/PROTOCOL/API_KEY` (change `changeme`)

## 6. Workers env (Celery)

- [ ] Crawler lanes per source plan: `CRAWLER_HTTP_ENABLED`, `CRAWLER_CHAIN_ENABLED`
  ```
  (defaults on); enable `CRAWLER_DISCORD/REDDIT/TELEGRAM/MAIL_ENABLED` as configured
  ```
- [ ] Publish policy: `NEWS_MAX_ARTICLES_PER_DAY` (≤7), `NEWS_MAX_BREAKING_PER_DAY` (2),
  ```
  `NEWS_STANDARD_INTERVAL_HOURS` (3)
  ```
- [ ] Queue maintenance: `PUBLISH_DEFER_PRIORITY_THRESHOLD` (45),
  ```
  `PUBLISH_ANNOUNCE_EXPIRE_HOURS` (72) — beat task `expire_stale_queue_items`
  ```
- [ ] Official source boosts: seed `OFFICIAL_MAIL_FROM_DOMAINS`,
  ```
  `OFFICIAL_DISCORD_CHANNEL_IDS`, `OFFICIAL_TELEGRAM_CHAT_IDS` — or manage at
  runtime via `/api/v1/admin/official-channels`
  ```
- [ ] Mail lane (optional): `MAIL_IMAP_HOST/USER/PASSWORD`
- [ ] Telegram lane (optional): `TELEGRAM_BOT_TOKEN`
- [ ] SPA scraping (optional): `CRAWLER_WEB_SPA_ENABLED=1` + Playwright Chromium
  ```
  installed in the venv (`playwright install chromium`)
  ```

## 7. Deploy

```bash
# target/users/domains come from deploy/deploy.conf
DEPLOY_CONFIRM=1 ./deploy/deploy.sh provision   # first time only
DEPLOY_CONFIRM=1 ./deploy/deploy.sh deploy
# production: DEPLOY_CQL_TIER=prod
```

The script: uploads + verifies checksum → backs up `releases/current` →
applies CQL via the migration ledger → restarts `algorand-platform-backend`,
`-celery`, `-celery-beat` → waits for `/health/ready`.

## 8. Post-deploy smoke

- [ ] `curl https://host/health/ready` → all checks green (redis, cassandra,
  ```
  typesense, conduit index)
  ```
- [ ] `systemctl is-active algorand-platform-{backend,celery,celery-beat,conduit}`
- [ ] `GET /api/v1/news/feed` returns 200 with `ETag`; repeat with `If-None-Match`
  ```
  → 304
  ```
- [ ] `GET /api/v1/news/stats` article count sane
- [ ] `GET /api/v1/search?q=algorand` returns `engine: typesense` (or `feed_scan`)
- [ ] Celery beat firing: `chain-tail-process-rounds`, `drain-standard-publish-queue`,
  ```
  `drain-breaking-publish-queue`, `expire-stale-queue-items` in beat log
  ```
- [ ] Chain tail advancing: Redis key `chain_tail:last_processed_round` grows
- [ ] At least one registered TestNet service produces an article end-to-end
  ```
  (freeze gate, see [freeze-v0.x.md](../architecture/freeze-v0.x.md))
  ```
- [ ] Flutter web: feed renders, article detail opens, wallet connect works

## 9. Rollback

```bash
DEPLOY_CONFIRM=1 ./deploy/rollback.sh   # reads deploy/deploy.conf   # restores releases/previous and restarts units
```

CQL migrations are additive-only; no schema rollback is required for v0.x.