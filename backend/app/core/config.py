"""Application settings (msgspec.Struct, env-driven).

Replaces pydantic-settings: fields default below, and are overridden from a `.env`
file (dev) then the process environment (prod, injected via the systemd
EnvironmentFile) by the matching UPPER-CASED name. Values are coerced to the
field type. Unknown env vars are ignored.
"""

import os
from pathlib import Path
from typing import get_args

import msgspec


class Settings(msgspec.Struct, kw_only=True):
    """Backend service configuration, populated from environment variables."""

    app_name: str = "algorand-platform-api"
    app_env: str = "dev"
    app_host: str = "0.0.0.0"
    app_port: int = 8080
    # NOTE: Gunicorn process/thread sizing (APP_PROCESSES/APP_THREADS/APP_WORKERS/
    # GUNICORN_WORKERS/GUNICORN_THREADS) is read directly from the shell environment
    # by deploy/scripts/run_backend.sh before this process starts -- no Settings
    # field for it exists here since nothing in Python ever reads one (deleted
    # 2026-08-28: app_processes/app_threads/app_workers were dead struct fields).

    # Public-facing site (used to build absolute canonical / OG / sitemap URLs
    # in the SEO-rendered document routes). Override per-env via PUBLIC_SITE_URL.
    public_site_url: str = "https://algorand.pxke.me"
    # The Algorand Open Registry's own domain (registry SSR canonical/OG
    # URLs and the Host-header check that routes "/" on this domain to
    # render_registry_index instead of the news homepage -- see
    # seo/api/routes.py's home()). Mirrors x402_public_site_url's existing,
    # still-unused-for-SSR pattern (x402 has no SSR yet).
    registry_public_site_url: str = "https://algorand-registry.pxke.me"
    site_name: str = "PXke Algorand"
    # Doubles as the meta description for the front page and RSS channel —
    # written for the SERP snippet (task #39, 2026-07-16: the brand query
    # showed a bare "news, search and tools" line and drew zero clicks).
    site_tagline: str = (
        "Independent daily coverage of the Algorand ecosystem — verified "
        "reporting on wallets, DeFi, NFTs and infrastructure, fact-checked "
        "on-chain before it publishes."
    )
    # Absolute path to the built SPA dir (holds index.html). Empty =
    # auto-detect: <release>/frontend_web (prod) then frontend/dist (dev).
    frontend_dist_dir: str = ""
    # OG/Twitter card image used when an article has no hero image (path or URL).
    seo_default_image: str = "/icons/icon-512.png"
    # Comma-separated official profile URLs (X, Discord, GitHub, …) for the
    # Organization JSON-LD `sameAs`. Empty = omitted. e.g. "https://x.com/...".
    seo_same_as: str = ""
    # Google-News sitemap (sitemap-news.xml). Served + advertised in robots.txt.
    # Harmless until accepted into Google News Publisher Center (nothing reads it
    # before then); kept on so it's ready the day we apply. Set false to hide it.
    seo_news_sitemap_enabled: bool = True
    # IndexNow key — same key the workers use (INDEXNOW_KEY there; the key file
    # is served at /{key}.txt by the deploy). The backend pings on the admin
    # paths that change a public URL: approve-to-feed, patch, delete.
    indexnow_key: str = "63e7ffa13f3ca734700ca375c0581b41"
    # Comma-separated IPs/hosts excluded from first-party analytics — the server's
    # own public IP, office/VPN IPs, etc. Their requests aren't counted and they
    # never appear as referrers. Loopback + private ranges are always excluded.
    analytics_ignore_ips: str = "5.135.131.229"
    # Secret salt mixed into the (ip+ua) hash used for privacy-safe unique-visitor
    # counts (Redis HyperLogLog). No raw IP is ever stored. Set to a random secret
    # in prod — a stable salt keeps a visitor's token consistent so period-unique
    # counts (PFCOUNT over several daily HLLs) dedupe the same person across days.
    analytics_hll_salt: str = "pxke-analytics-uv"
    # Extra hostnames that also serve this same site (e.g. the nginx fallback
    # vhost answers on these too). A Referer from one of them is in-site
    # navigation -> counted as '(internal)', not an external referrer. Exact-host
    # match only, so unrelated sub-domains on the same apex stay external.
    analytics_internal_hosts: str = (
        "pxke.me,wordpress.pxke.me,algosearch.pxke.me,apialgosearch.pxke.me"
    )
    # Path to a local GeoIP country database (DB-IP Lite or MaxMind GeoLite2, in
    # MaxMind .mmdb format) used to resolve a country code from the client IP at
    # record time. Country-level only — the IP itself is never stored. Empty ->
    # geography is silently disabled. Provisioned to shared/geoip by deploy.sh.
    geoip_db_path: str = ""
    # Path to a local GeoIP ASN database (DB-IP ASN Lite, same .mmdb format and
    # no-account download as geoip_db_path) used to flag client IPs that belong
    # to a cloud/hosting provider rather than a residential/mobile ISP — a
    # strong signal for the UA-rotation scrapers that otherwise hide inside
    # human "(direct)" (see analytics_store.is_hosting_ip). Only the boolean
    # classification is counted, never the IP or the ASN itself. Empty ->
    # disabled (fails open, same as geoip_db_path). Provisioned to shared/geoip
    # by deploy.sh.
    geoip_asn_db_path: str = ""

    auth_domain: str = "algorand-platform.local"
    auth_uri: str = "https://algorand-platform.local/sign-in"
    auth_statement: str = "Sign in to the Algorand Platform."
    auth_caip2_chain_id: str = "algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDe"
    auth_wallet_connect_chain_id: int = 416002
    algod_url: str = "https://testnet-api.algonode.cloud"
    algod_token: str = ""
    # If ALGOD_TOKEN is empty, read the node token from this path (world-readable
    # on a typical package install: /var/lib/algorand/algod.token).
    algod_token_file: str = ""
    # /api/v1/algod/* is unauthenticated by design (see algod_proxy.py) --
    # including the long-poll v2/status/wait-for-block-after route, which
    # ties up a proxy connection until algod produces a block. Per-IP budget,
    # same shape as img_proxy_rate_limit_per_hour.
    algod_proxy_rate_limit_per_hour: int = 600

    redis_url: str = "redis://localhost:6379/0"
    # Wallet login session lifetime in Redis (default ~30 days).
    session_ttl_seconds: int = 30 * 24 * 3600
    nonce_ttl_seconds: int = 300

    cassandra_hosts: str = "127.0.0.1"
    cassandra_keyspace: str = "algorand_platform"
    cassandra_local_dc: str = "datacenter1"
    # Required when the cluster runs PasswordAuthenticator (prod host does).
    cassandra_username: str = ""
    cassandra_password: str = ""

    typesense_host: str = "localhost"
    typesense_port: int = 8108
    typesense_protocol: str = "http"
    typesense_api_key: str = "changeme"

    # Cross-subdomain admin/wallet session cookie (2026-09-07: the admin
    # panel disappeared moving between algorand.pxke.me/x402.pxke.me/
    # algorand-registry.pxke.me -- the session token lived only in
    # localStorage, which is per-origin, not per-registrable-domain). Empty
    # by default (feature off): a Domain-scoped cookie only makes sense
    # where all three sites share one parent domain, which isn't true for
    # local/dev origins (localhost:PORT). Set to ".pxke.me" in prod.
    session_cookie_domain: str = ""

    cors_allowed_origins: str = (
        "http://localhost:8080,http://127.0.0.1:8080,"
        "http://localhost:3000,http://127.0.0.1:3000,"
        "http://localhost:5173,http://127.0.0.1:5173"
    )
    # None = permissive in APP_ENV dev/test (any Origin). Set false for strict local CORS tests.
    cors_permissive: bool | None = None
    # Unauthenticated image proxy (GET /api/v1/img). A homepage of cards can
    # fire dozens of requests, so this is higher than the 120/hour x402 free
    # reads; still a hard per-IP cap so the SSRF-guarded fetch path cannot be
    # used as an open relay.
    img_proxy_rate_limit_per_hour: int = 600

    platform_treasury_address: str = ""
    # Suggestions product (P2) is paused; routes are not registered while false.
    suggestions_enabled: bool = False
    suggestion_min_microalgos: int = 10_000
    suggestion_store: str = "memory"
    upvote_store: str = "memory"

    # Algorand Open Registry (roadmap item 26, CLAUDE.md section 9.1; design
    # pass docs/awesome-algorand-directory-design.md). A NEW, separate
    # concern from the paid x402 directory -- free, anonymous,
    # human-reviewed ecosystem-project listings, glossary-shaped. See
    # app/modules/ecosystem/.
    ecosystem_enabled: bool = True
    ecosystem_store: str = "memory"
    # Free-endpoint abuse gate (CLAUDE.md section 2.9), own key and own
    # setting per the shared incr_with_expiry primitive's own convention
    # (app/core/rate_limit.py). Low default: submission volume is expected
    # to be "tens per month, not thousands" (design doc section 3.2) -- the
    # risk here is under-submission, not flood, so this exists only to stop
    # a script, not to ration real builders.
    ecosystem_submit_rate_limit_per_hour: int = 5
    ecosystem_read_rate_limit_per_hour: int = 120
    # Bounds each hop's connect+read-headers time for the submit-time (and
    # periodic re-check) liveness probe -- see
    # app/modules/ecosystem/services/liveness.py.
    ecosystem_liveness_timeout_seconds: float = 5.0
    # Hard cap on a list/search page — no unbounded listings (CLAUDE.md
    # section 4).
    ecosystem_list_max_results: int = 100
    # Cap on how many entries one POST /admin/ecosystem/seed call creates in
    # a single request (design doc section 6.3) -- the source scan itself
    # (domain_tracking) is a small, fully-enumerable table (a few hundred
    # rows), but this bounds what gets WRITTEN per invocation regardless.
    ecosystem_seed_max_entries: int = 500

    price_metrics_asset_id: str = "algorand"

    news_store: str = "memory"
    news_feed_bucket: str = "main"
    # Mirror of the worker's PAUSE_INTAKE_ON_FEED_BACKLOG (off by default) — read
    # here only so the admin "pull top topic" action can report the same gate
    # the worker itself checks, instead of guessing.
    pause_intake_on_feed_backlog: bool = False
    # Mirror of the worker's NEWS_MAX_ARTICLES_PER_DAY (default 3, same env var
    # name) — was stuck at the old default of 7 here, so an admin's immediate-
    # publish approval could think there was cap room the worker pipeline
    # didn't agree with (root-caused 2026-07-14 alongside the release-pacing
    # unification, see AdminCassandraStore._is_standard_publish_due).
    news_max_articles_per_day: int = 3
    news_feed_limit: int = 50
    news_placement_slot: str = "news_feed_inline"
    news_placement_limit: int = 5
    # After an admin rejects a review, suppress re-enqueueing that URL in the
    # worker pipeline for this long (seconds). Mirror of the worker's
    # URL_REJECT_COOLDOWN_TTL — both must point redis_url at the same Redis DB.
    url_reject_cooldown_ttl: int = 7 * 24 * 3600

    celery_broker_url: str = "redis://localhost:6379/1"
    # Same result backend the workers' Celery app uses (celery_app.py) — lets
    # admin actions that fire a task wait briefly for its real result instead
    # of guessing. Must point at the same Redis DB as workers' REDIS_RESULT_URL.
    redis_result_url: str = "redis://localhost:6379/2"
    ingest_api_key: str = ""
    admin_wallet_addresses: str = ""
    # Owner-supplied article sources (2026-09-02, docs/newspaper-article-
    # sources-design.md, Phase 1) -- bounds ONE pasted source's content on
    # attach (POST /api/v1/admin/articles/:article_id/sources rejects
    # oversize with a 400, never truncates silently). Same name/default as
    # workers' own ADMIN_SOURCE_MAX_CHARS (app/core/config.py there) -- each
    # service reads its own copy (CLAUDE.md section 3: config has one owner
    # PER SERVICE), but they're kept at the same value since both bound the
    # same underlying content.
    admin_source_max_chars: int = 100_000

    # x402 paid-endpoint plumbing (Algorand Global x402 Challenge). Off by
    # default until a facilitator/pay_to address is actually configured.
    x402_enabled: bool = False
    x402_facilitator_url: str = "https://facilitator.goplausible.xyz/"
    # TestNet CAIP-2 id by default — flip to the mainnet genesis hash for the
    # real contest submission, not before.
    x402_network: str = "algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI="
    # Public address only — no private key is held by this module.
    x402_pay_to_address: str = ""
    # Backend of the settlement ledger (x402_settlements/x402_settlements_by_tx,
    # modules/x402/settlement.py) -- the one store EVERY paid route writes to,
    # so it has no per-product gate. "memory" is dev/test only: a paid route
    # must never run against a per-process ledger that vanishes on restart
    # (CLAUDE.md section 9: every settlement logged), and falcon_main refuses
    # to register the paid routes outside app_env == "dev" while this is
    # still "memory". Env: X402_SETTLEMENT_STORE=cassandra in prod.
    x402_settlement_store: str = "memory"
    # Public absolute base of this API as agents reach it. Every 402 offer's
    # resource.url is this base + the route path: the facilitator's Bazaar
    # catalogs resources by that URL, so it must be the real public origin,
    # never the internal bind address.
    x402_public_api_base: str = "https://algorand-api.pxke.me"
    # Public absolute base of the marketplace's own human-facing site
    # (docs/x402-marketplace-product-redesign.md §3.1, row 5) — the
    # frontend-only `x402.pxke.me` subdomain, NOT the API host above. Used
    # wherever the marketplace needs to advertise or link to itself (the
    # catalog's `website` field, the merchant landing redirect target, a
    # marketplace SSR canonical/OG base once those exist) — never as a
    # replacement for x402_public_api_base, which the Bazaar already has
    # every existing listing's `resource.url` keyed on. Distinct setting,
    # same shape, same "real public origin, never an internal bind address"
    # rule as x402_public_api_base's own comment.
    x402_public_site_url: str = "https://x402.pxke.me"
    # ── Probe / self wallets, excluded from every ranking in code ──────────
    # Comma-separated Algorand addresses of OUR payers (the probe beat's hot
    # wallet and anything else we pay our own endpoints from). CLAUDE.md
    # section 9: probe traffic is the one allowed exception to "no wash
    # volume" and must be excluded from every ranking. Read through
    # modules/x402/probe_payers.py; the settlement ledger labels these
    # wallets so the public settlements feed (and any future ranking) never
    # counts their payments as customer volume. Empty = nothing is excluded.
    x402_probe_payers: str = ""
    # ── end probe / self wallets ────────────────────────────────────────────

    # ── Auto-refund on our own product-write failure ────────────────────────
    # A route can opt into modules/x402/paid_request.run_with_refund: if the
    # product write raises after payment already settled, the full amount is
    # sent back to the payer from THIS dedicated wallet, never from
    # x402_pay_to_address (receive-only, no key held — see above). Empty =
    # refunds are skipped (logged, the route still returns an honest "failed,
    # refund pending" response) until configured — "empty is inert".
    x402_refund_mnemonic: str = ""
    # Circuit breaker (owner requirement 2026-09-02): every refund costs a
    # real Algorand transaction fee on top of the refunded amount, so a bug
    # or an adversary deliberately triggering failures could cheaply drain
    # this wallet one failed call + one refund-tx-fee at a time. A resource
    # that crosses this many refund-triggering failures within the window
    # trips and is refused BEFORE the payment gate (modules/x402/
    # circuit_breaker.py) until an admin resets it. 5 failures / 10 minutes:
    # comfortably above a real transient blip (a single flaky downstream
    # call) but low enough that a drain attempt costs an attacker very
    # little before being cut off.
    x402_refund_breaker_max_failures: int = 5
    x402_refund_breaker_window_seconds: int = 600
    # Marketplace-wide daily refund ceiling, tracked PER ASSET (asset_id) but
    # denominated in one true USD-equivalent unit shared by every asset:
    # atomic units at USDC's OWN 6 decimals. Each refund's raw atomic amount is
    # converted to this unit via refund.py's own normalizer (decimals-only
    # for a coingecko_id-less, already-USD asset like USDC; price_oracle for
    # everything else) BEFORE it is compared/accumulated against this number
    # -- not a cross-asset sum, still tracked per asset_id, just finally
    # commensurate across assets (found-in-audit gap 2026-09-02: the
    # per-resource breaker alone has no ceiling on TOTAL exposure across
    # every refund-wired resource combined).
    #
    # Found-in-audit BUG, fixed 2026-09-06: this was previously named
    # x402_refund_daily_budget_atomic and compared directly against each
    # refund's raw atomic amount with no decimals/price normalization at
    # all. That silently worked only because every asset accepted so far
    # (USDC, EURQ, USDQ) happens to be a 6-decimal, ~1-USD-pegged
    # stablecoin. Adding goBTC (8 decimals, BTC-backed, NOT a stablecoin --
    # see assets.py) broke both assumptions at once: the same atomic number
    # capped goBTC's daily refund exposure at 1.0 goBTC (10**8 atomic /
    # 10**8 decimals), worth on the order of $70,000-$100,000+ at a
    # realistic goBTC price -- roughly 1000x the intended ~$100/day ceiling
    # every other asset actually got. Renamed and re-scoped so a config
    # author never has to hand-compute a new atomic number for the next
    # asset added; see refund.py's normalizer docstring for why this goes
    # through price_oracle rather than decimals alone.
    #
    # 100 USDC/day-equivalent (100_000_000 atomic at USDC's 6 decimals) is a
    # generous multiple of any single resource's own per-window cap at
    # current prices -- tune down once real refund volume is observed. Past
    # this, a refund is skipped (not sent, no funds move) and the route
    # falls into the same honest "refund pending, reconcile by hand"
    # response an actual send failure produces -- fails CLOSED, same as the
    # circuit breaker, because this guards money leaving the wallet. An
    # asset whose USD price cannot be priced right now (price_oracle has no
    # rate) also fails CLOSED here -- an unpriceable refund must not sail
    # through an unenforceable budget.
    x402_refund_daily_budget_usd_atomic: int = 100_000_000
    # ── end auto-refund ──────────────────────────────────────────────────────

    # ── x402 News Engine pay-per-call (GET /x402/news, GET /x402/news/tags
    # and GET /x402/news/articles/:id all free, GET /x402/news/search paid). See
    # app/modules/x402_news/. Roadmap item 1: the newspaper's own live
    # articles resold per call. No store setting of its own -- the module
    # reads through the news module's store, so route registration is gated
    # on news_store != "memory" instead.
    # Fee for one ranked search. Money strings, parsed by the tagged money
    # parser in modules/x402/client.py (which is also what attaches the
    # challenge tag). Kept deliberately near-zero: the article content is
    # already free on the public site, so search is the only thing here an
    # agent can't already get for nothing, and it should stay a trivial call.
    x402_news_search_price: str = "$0.001"
    # Free-endpoint abuse gate (CLAUDE.md section 9: rate limit every free
    # endpoint per IP), one hourly budget shared by the three free reads
    # (headline list, article read, tag discovery), each counted under its
    # own key prefix, separate from the other x402 products' budgets.
    x402_news_rate_limit_per_hour: int = 120
    # Hard cap on a headline page and on paid search hits -- no unbounded
    # listings (CLAUDE.md section 4).
    x402_news_max_results: int = 50
    # Hard cap on the free tag-discovery list (GET /x402/news/tags) -- its
    # own cap, not x402_news_max_results: the tag universe is larger than a
    # headline page and truncating the taxonomy at 50 would hide real
    # sections. Tags are served sorted by coverage, so the cap keeps the
    # head of the taxonomy.
    x402_news_max_tags: int = 200
    # ── end x402 News Engine ──

    # ── x402 sandboxed file/tarball scan (roadmap item 18b). See
    # app/modules/x402_scan/. Prototype/v0: static analysis only (ClamAV,
    # file-type, entropy, indicator extraction, zip/tar-bomb-safe listing) in
    # a hardened --network none Docker container, never executes the input.
    # Disabled by default -- this is a design prototype, not a live product;
    # flip on only after the host-isolation decision in the module docstring
    # is made explicitly (which box this container engine runs on).
    x402_scan_enabled: bool = False
    x402_scan_price: str = "$0.01"
    # Hard cap on the bounded download, now streamed straight to disk rather
    # than buffered in memory (CLAUDE.md section 4: stream remote fetches,
    # abort past the cap) -- see scan_service._fetch_bounded_to_disk. 1GB,
    # not "a few GB" as first asked for: going further needs real design
    # work this prototype hasn't done yet -- per-request disk-quota
    # accounting under concurrent paid traffic, and benchmarking how
    # ClamAV's own scan time scales at that size against
    # x402_scan_sandbox_timeout_s below. Flagged as a follow-up, not
    # guessed at. Deliberately above the sandbox's own archive
    # MAX_EXTRACT_BYTES (200MB) -- a bigger download whose declared archive
    # contents exceed that just trips the zip/tar-bomb guard and reports
    # unverified, which is the safe default, not a bug.
    x402_scan_max_download_bytes: int = 1024 * 1024 * 1024
    x402_scan_download_timeout_s: int = 60
    x402_scan_sandbox_timeout_s: int = 90
    x402_scan_rate_limit_per_hour: int = 30
    # ── end x402 file/tarball scan ──

    # ── x402 agent backup storage (roadmap item 12: pay-per-MB storage; owner
    # design decision made 2026-09-03, this is the "starts local-disk-only"
    # first cut -- a second cloud connector (Wasabi) is a documented future
    # addition, not built here). See app/modules/x402_storage/.
    x402_storage_meta_store: str = "memory"
    # Storage connector root directory on local disk. Empty = not configured
    # -- same "empty path = disabled" convention as geoip_db_path above. The
    # product does not register at all (see falcon_main.py) unless this AND
    # x402_storage_meta_store are both set, so a durable metadata store with
    # no connector root never registers and then 503s every request.
    x402_storage_local_root: str = ""
    # Which connector backends/factory.py resolves for a NEW upload. Existing
    # rows always use whatever connector they were written with (the row's
    # own `connector` column), so flipping this only affects future writes.
    # Only "local" resolves today -- "wasabi" is a documented future addition.
    x402_storage_backend: str = "local"
    # Money string per KB per full x402_storage_term_days term, parsed by the
    # tagged money parser in modules/x402/client.py. Cut from a flat $0.02/MB
    # -- rounded up to whole MB, always billed the full 90-day term
    # regardless of how long the caller actually wanted -- on 2026-09-06
    # against a same-night competitive study: commodity per-GB storage
    # (Backblaze B2, S3, IPFS pinning) and the closest real x402-native
    # comparables (Pinata's x402 pay-to-pin, x402.storage) all price
    # 100-1000x below the old rate, and whole-MB rounding overcharged a small
    # file by up to 1024x. Owner-approved fix: a 10x rate cut ($0.02/MB ->
    # $0.002/MB per 90 days), billed by the actual KB used (see
    # BackupService.kb_units()) and scaled linearly against the caller's
    # chosen `retention_days` (1..x402_storage_term_days) -- see
    # compute_price()'s own docstring for the exact formula. Derivation:
    # $0.002/MB / 1024 KB-per-MB = $0.000001953125/KB -- exact, no rounding,
    # since 1024 is a power of two -- which is also exactly what makes the
    # owner-confirmed example (10 MiB for the full 90-day term = 10*1024 KiB
    # * this rate = $0.02) come out even.
    x402_storage_price_per_kb_per_90d: str = "$0.000001953125"
    # Floor on any single compute_price() result (same 2026-09-06 study and
    # owner approval as the rate cut above) -- without it, a tiny file at a
    # short retention would price out to an unsettleable fraction of a cent.
    x402_storage_price_floor: str = "$0.001"
    # Hard cap on one backup, regardless of what declared_size_bytes claims.
    # Checked on the free initial request, before the payment gate -- a
    # cheap early rejection nobody pays for. nginx client_max_body_size must
    # stay above this after JSON+base64 inflation (~4/3) -- see
    # deploy/nginx/algorand-platform.conf.
    x402_storage_max_backup_mb: int = 10
    # Global local-disk ceiling across every stored backup on this connector.
    # Checked at write time via the connector's own usage_bytes(). Unlike the
    # declared/actual size-mismatch refusal (the payer's fault, payment kept,
    # never refunded), hitting this ceiling is OUR capacity problem, not the
    # payer's -- backup_service.create() raises StorageCapacityUnavailable
    # (deliberately not a StorageError/PlatformError) so
    # modules/x402/paid_request.run_with_refund's generic-exception path
    # refunds the payer and counts the failure against the resource's
    # circuit breaker (see StorageCapacityUnavailable's own docstring).
    x402_storage_local_max_total_mb: int = 5000
    # How long a renewal (always a full fresh term) tries to add, in days,
    # AND the upper bound + default for a create/add_version's own caller-
    # chosen `retention_days` (1..this value) -- see compute_price() and
    # compute_expiry_epoch()'s own docstrings. The remaining window is ALSO
    # capped by x402_storage_max_remaining_days (renewing early, or choosing
    # a long retention_days on an already-long-lived backup, cannot stack
    # past that ceiling).
    x402_storage_term_days: int = 90
    # Hard cap on remaining retrievable life, from "now" at the moment of
    # create/renew. A backup may never be extended past this many days of
    # remaining storage -- renewing a still-live backup refreshes up to this
    # ceiling rather than stacking another full term on top of what is left.
    x402_storage_max_remaining_days: int = 90
    # After expires_at, GET/list hide the backup, but renew still works for
    # this many extra days. Past that, the reaper deletes connector bytes
    # and marks the row deleted. Two days is enough to notice an expiry and
    # pay to renew without the blob already being gone.
    x402_storage_reaper_grace_days: int = 2
    # How far back (in calendar days) one reaper tick scans the expiry
    # projection. A tick that was down for a week still catches up without
    # an unbounded partition scan.
    x402_storage_reaper_lookback_days: int = 14
    # Per-expiry-day LIMIT on one reaper tick. Further due rows wait for
    # the next hourly beat rather than one unbounded query.
    x402_storage_reaper_batch: int = 100
    # Shared secret for POST /api/v1/internal/x402/storage/reap (the Celery
    # beat hits this, because the local-disk connector lives on the API
    # host, not in the worker process). Empty = the route 404s and the beat
    # is not registered -- same "empty = disabled" convention as
    # x402_storage_local_root.
    x402_storage_reaper_token: str = ""
    # Redis lock TTL covering one reap tick so two overlapping calls
    # (beat + a manual trigger) do not double-walk the same due rows.
    x402_storage_reaper_lock_seconds: int = 600
    # Free-endpoint abuse gate (CLAUDE.md section 9: rate limit every free
    # endpoint per IP), counted under its own key prefix. Covers the free
    # challenge-issuance, list, detail and delete routes.
    x402_storage_rate_limit_per_hour: int = 120
    # Hard cap on a backup listing page -- no unbounded listings (CLAUDE.md
    # section 4).
    x402_storage_max_results: int = 100
    # ── end x402 agent backup storage ──

    # x402 catalog (GET /x402, free): the machine-readable index of every
    # x402 product route currently registered. See app/modules/x402_catalog/.
    # No store and no price of its own -- it only reads the other products'
    # settings. Free-endpoint abuse gate (CLAUDE.md section 9), counted under
    # its own key prefix, separate from the products' own budgets.
    x402_catalog_rate_limit_per_hour: int = 120
    # Free proof-of-volume feed (GET /api/v1/x402/settlements/recent): real,
    # non-operator settlements across every product. Own key prefix and own
    # budget, separate from the catalog and every product's own limits.
    x402_settlements_rate_limit_per_hour: int = 120

    # Replay window for an already-spent payment header. Must be >= 2x the
    # facilitator's own HTTP timeout (FacilitatorConfig.timeout defaults to
    # 30s in x402-avm==2.0.2) so a header can never be re-presented while the
    # first settle of it is still in flight.
    x402_replay_ttl_seconds: int = 900

    # ── x402 shared payment-gate extensions: preview mode + promo codes ──────
    # See app/modules/x402/preview.py and app/modules/x402/promo.py. Both are
    # opt-in per route (require_paid_request's preview / promo_code kwargs
    # default to off), so a route that does not pass them is unaffected.
    #
    # Preview (`?preview=true`, checked by the route, see x402_scan): bypasses
    # the payment gate entirely -- no facilitator call, nothing settled,
    # nothing on the ledger -- so it needs its own free-endpoint abuse gate
    # (CLAUDE.md section 9), own key prefix, fails open like every other
    # free-read rate limit in this codebase (a Redis blip must not take a
    # preview surface offline).
    x402_preview_rate_limit_per_hour: int = 60
    # Promo-code storage (x402_promo_codes admin table + x402_promo_redemptions
    # audit log/abuse-cap log, migration 100). "memory" for dev/test, same
    # StoreFactory[T] + Protocol shape as every other x402 store (CLAUDE.md
    # section 9).
    x402_promo_store: str = "memory"
    # Per-IP budget on redemption ATTEMPTS (not on the code's own remaining
    # count, which is the separate Redis DECR guard in promo.py) -- a second,
    # complementary abuse layer alongside the per-(code, wallet) Cassandra LWT
    # cap. Fails open: an IP-abuse check failing must not block the fallback
    # to the normal paid gate, which still works either way.
    x402_promo_rate_limit_per_hour: int = 30
    # ── end x402 preview + promo ──────────────────────────────────────────────

    @property
    def cors_origins(self) -> list[str]:
        """Parse the comma-separated CORS origins setting into a list."""
        raw = self.cors_allowed_origins.strip()
        if not raw:
            return []
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    @property
    def x402_storage_registered(self) -> bool:
        """Both x402_storage_meta_store is durable AND a local connector root is configured.

        Read-only derived flag so falcon_main.py's own registration check
        (`if settings.x402_storage_registered:`) stays a single-condition
        `if`, not an `and` -- purely to keep _register_x402_routes's cyclomatic
        complexity from crossing ruff's C901 threshold on this one extra
        product. catalog.py's "storage" Product independently re-derives the
        identical condition from the same two raw settings via its own
        generic store_setting/nonempty_string_setting mechanism (Product.
        enabled()'s own docstring: "the same condition falcon_main.py
        registers this product under" -- every other product's gate is
        already re-evaluated this same way in two places, this is not new
        duplication).
        """
        return self.x402_storage_meta_store != "memory" and bool(
            self.x402_storage_local_root.strip()
        )


_TRUTHY = {"1", "true", "yes", "on"}


def _parse_dotenv(path: str) -> dict[str, str]:
    """Minimal KEY=VALUE .env reader (comments/blank lines skipped, quotes stripped)."""
    out: dict[str, str] = {}
    try:
        with Path(path).open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                out[key.strip()] = value.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return out


def _coerce(value: str, typ: object) -> bool | int | float | str:
    """Coerce an env string to a Struct field's type (bool/int/float/str)."""
    candidates = set(get_args(typ)) | {typ}
    if bool in candidates:  # checked first — bool is a subclass of int
        return value.strip().lower() in _TRUTHY
    if int in candidates:
        return int(value)
    if float in candidates:
        return float(value)
    return value


def _load() -> Settings:
    dotenv = _parse_dotenv(".env")
    overrides: dict[str, object] = {}
    for fld in msgspec.structs.fields(Settings):
        env_name = fld.name.upper()
        if env_name in os.environ:
            raw = os.environ[env_name]
        elif env_name in dotenv:
            raw = dotenv[env_name]
        else:
            continue
        overrides[fld.name] = _coerce(raw, fld.type)
    return Settings(**overrides)


def _apply_algod_token_file(s: Settings) -> Settings:
    """Fill algod_token from ALGOD_TOKEN_FILE when the env token is blank."""
    if s.algod_token.strip() or not s.algod_token_file.strip():
        return s
    try:
        token = Path(s.algod_token_file).read_text(encoding="utf-8").strip()
    except OSError:
        return s
    if not token:
        return s
    s.algod_token = token
    return s


settings = _apply_algod_token_file(_load())


# --------------------------------------------------------------------------- #
# Module-level mirrors of workers' env-driven constants (workers/app/core/
# config.py is UPPER_CASE module constants, not `Settings` fields -- see that
# file's own docstring). Needed because `algorand_shared.to_compose_selection`
# / `algorand_shared.artifact_priority` (moved from workers 2026-08-26 so
# backend's admin to-compose/artifact routes can call them directly instead
# of a Celery round-trip) read these via `from app.core import config as cfg`,
# so they behave identically regardless of which service imports them. Same
# env var names/defaults as workers/app/core/config.py -- keep both in sync.
# --------------------------------------------------------------------------- #
def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw not in (None, "") else default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw not in (None, "") else default


# Mirror of the worker's NEWS_MAX_ARTICLES_PER_DAY -- see also this module's
# own `news_max_articles_per_day` Settings field (the pre-existing admin-
# facing mirror); `to_compose_selection` needs the plain module constant.
NEWS_MAX_ARTICLES_PER_DAY = min(max(1, _env_int("NEWS_MAX_ARTICLES_PER_DAY", 3)), 7)
ARTIFACT_WORD_COUNT_CAP = _env_int("ARTIFACT_WORD_COUNT_CAP", 1200)
ARTIFACT_WORD_COUNT_MAX_SCORE = _env_float("ARTIFACT_WORD_COUNT_MAX_SCORE", 10.0)
ARTIFACT_TIMELINESS_MAX_SCORE = _env_float("ARTIFACT_TIMELINESS_MAX_SCORE", 10.0)
ARTIFACT_TIMELINESS_FLOOR = _env_float("ARTIFACT_TIMELINESS_FLOOR", 1.0)
ARTIFACT_TIMELINESS_HALF_LIFE_DAYS = _env_float("ARTIFACT_TIMELINESS_HALF_LIFE_DAYS", 21.0)
ARTIFACT_ECOSYSTEM_LISTED_BOOST = _env_float("ARTIFACT_ECOSYSTEM_LISTED_BOOST", 5.0)
ARTIFACT_NEW_SERVICE_MIN_SHARE = _env_float("ARTIFACT_NEW_SERVICE_MIN_SHARE", 0.5)
ARTIFACT_SKIP_COUNT_CAP = _env_int("ARTIFACT_SKIP_COUNT_CAP", 10)
# Row TTL (seconds) bound to url_queue / url_queue_by_url / url_queue_pending
# writes (USING TTL ?) — backend only writes these on the frontier-approval
# seed insert (_seed_domain_crawl). 0 = disabled (CQL treats TTL 0 as "no
# TTL"). Mirror of workers/app/core/config.py's URL_QUEUE_ROW_TTL_SECONDS —
# same env var name/default, keep both in sync.
URL_QUEUE_ROW_TTL_SECONDS = _env_int("URL_QUEUE_ROW_TTL_SECONDS", 0)
ARTIFACT_SKIP_COUNT_MAX_SCORE = _env_float("ARTIFACT_SKIP_COUNT_MAX_SCORE", 6.0)
