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
    # Public absolute base of this API as agents reach it. Every 402 offer's
    # resource.url is this base + the route path: the facilitator's Bazaar
    # catalogs resources by that URL, so it must be the real public origin,
    # never the internal bind address.
    x402_public_api_base: str = "https://algorand-api.pxke.me"
    # ── Probe / self wallets, excluded from every ranking in code ──────────
    # Comma-separated Algorand addresses of OUR payers (the probe beat's hot
    # wallet and anything else we pay our own endpoints from). CLAUDE.md
    # section 9: probe traffic is the one allowed exception to "no wash
    # volume" and must be excluded from every ranking. Read through
    # modules/x402/probe_payers.py; each product drops these wallets where
    # their payment would otherwise become signal (grades, votes,
    # credibility spend, board tiles). Empty = nothing is excluded.
    x402_probe_payers: str = ""
    # ── end probe / self wallets ────────────────────────────────────────────

    # ── Auto-refund on our own product-write failure ────────────────────────
    # A route can opt into modules/x402/paid_request.run_with_refund: if the
    # product write raises after payment already settled, the full amount is
    # sent back to the payer from THIS dedicated wallet, never from
    # x402_pay_to_address (receive-only, no key held — see above) and never
    # from kyc_payout_mnemonic (a different fund, a revenue-share payout, not
    # a refund). Empty = refunds are skipped (logged, the route still returns
    # an honest "failed, refund pending" response) until configured — same
    # "empty is inert" convention as kyc_payout_mnemonic.
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
    # Marketplace-wide daily refund ceiling, tracked PER ASSET (asset_id) in
    # that asset's own atomic units -- not a cross-asset sum, since summing
    # e.g. USDC and EURQ atomic units directly would be meaningless without
    # a shared price oracle at refund time, which this path deliberately
    # does not add (found-in-audit gap 2026-09-02: the per-resource breaker
    # alone has no ceiling on TOTAL exposure across every refund-wired
    # resource combined). 100 USDC/day (100_000_000 atomic, 6 decimals) is a
    # generous multiple of any single resource's own per-window cap at
    # current prices -- tune down once real refund volume is observed. Past
    # this, a refund is skipped (not sent, no funds move) and the route
    # falls into the same honest "refund pending, reconcile by hand"
    # response an actual send failure produces -- fails CLOSED, same as the
    # circuit breaker, because this guards money leaving the wallet.
    x402_refund_daily_budget_atomic: int = 100_000_000
    # ── end auto-refund ──────────────────────────────────────────────────────

    # ── Signed fulfillment receipts (owner conversation 2026-09-03, see
    # docs/x402-execution-trust-evaluation.md item 1) ───────────────────────
    # A route wired through modules/x402/paid_request.run_with_refund gets a
    # server-signed receipt attached to its response, binding exactly what
    # was delivered to exactly what was paid for:
    # sig(H(request body) || H(response body) || settlement_tx_id || ts).
    # This is evidence, not a guarantee -- see the evaluation doc, do not
    # re-derive the reasoning here. A FRESH, DEDICATED signing key, never
    # x402_refund_mnemonic/kyc_payout_mnemonic/x402_pay_to_address -- it never
    # holds funds and is never asked to. Signed via algosdk.util.sign_bytes
    # (the same "MX"-domain-separated primitive workers/app/modules/wallet/
    # signer.py already uses for algo_signData), so a receipt signature can
    # never be replayed as authorization for a real on-chain transaction --
    # safe to keep on a network-facing service. Empty = receipt generation is
    # skipped (logged at debug, never blocks or fails the paid route) until
    # configured -- same "empty is inert" convention as x402_refund_mnemonic.
    x402_receipt_signing_mnemonic: str = ""
    # Cassandra ("memory" = dev/test only, invisible across gunicorn workers,
    # same convention as every other product's store gate). NEVER the
    # settlement ledger (x402_settlements/x402_settlements_by_tx) -- a
    # dedicated table so stored receipt content can be removed independently
    # of the ledger, which CLAUDE.md section 9 treats as permanent. Joins
    # back to the ledger via the settlement_tx_id column the receipt already
    # carries -- no new column added to the ledger side.
    x402_receipts_store: str = "memory"
    # Bounds ONE stored receipt's response-output text (same order of
    # magnitude as admin_source_max_chars above for "cap a stored blob" --
    # this codebase's existing convention). The signature always covers the
    # FULL response hash regardless of this cap; only the retained copy of
    # the content itself is truncated past this, with a truncated flag set
    # so a reader is never told a partial copy is complete.
    x402_receipt_output_max_chars: int = 100_000
    # Retention window (owner ask): a receipt is deliberately temporary
    # evidence, not a permanent record -- Cassandra default_time_to_live on
    # the table (migration 108), not enforced in application code.
    x402_receipt_ttl_days: int = 90
    # Free-endpoint abuse gate (CLAUDE.md section 9) for GET
    # /api/v1/x402/receipts/:receipt_id, same per-IP Redis incr/expire shape
    # as every other free x402 read.
    x402_receipts_rate_limit_per_hour: int = 120
    # ── end signed fulfillment receipts ─────────────────────────────────────

    # Know Your Agent (KYA, the x402 challenge's actual product): free wallet
    # enrollment + trust-signal computation, then a paid x402 lookup that
    # splits its fee with the enrolled wallet. The module lives in
    # app/modules/kya/; the kyc_* setting names are kept because prod env
    # names depend on them.
    kyc_store: str = "memory"
    kyc_lookup_price: str = "$0.05"
    # Share of the lookup fee paid out to the enrolled wallet (the rest stays
    # with the platform). 0.5 = 50/50, matching the product's original pitch.
    kyc_payout_share: float = 0.5
    # Public AlgoNode indexers — same free tier + URLs as the workers service
    # (workers/app/core/config.py's TESTNET_INDEXER_URL/MAINNET_INDEXER_URL),
    # mirrored here since backend has never needed indexer reads before (algod
    # alone can't answer "when was this account created" or "recent txns" —
    # that's what an indexer is for, algod only has current state).
    kyc_testnet_indexer_url: str = "https://testnet-idx.algonode.cloud"
    kyc_mainnet_indexer_url: str = "https://mainnet-idx.algonode.cloud"
    # Mnemonic for a FRESH, DEDICATED, minimally-funded hot wallet — never the
    # x402_pay_to_address (receive-only, no key held) and never the admin
    # login wallet. Only ever spends (pays out half of each settled lookup
    # fee); someone has to top up its USDC balance manually, there is no
    # automated sweep from x402_pay_to_address. Empty = payouts are skipped
    # (logged, never block the paid lookup response) until configured.
    kyc_payout_mnemonic: str = ""
    # Free-endpoint abuse gate (CLAUDE.md section 9: rate limit every free
    # endpoint per wallet and per IP), same Redis incr/expire shape as the
    # other x402 modules, under its own key prefix. Three separate budgets
    # because the two free KYC endpoints cost wildly different things:
    #   - consent-message issues a single-use nonce (cheap Redis write) and
    #     gets the same generous hourly allowance the other modules' free
    #     reads get;
    #   - enroll fires two outbound indexer requests and a Cassandra write per
    #     hit, so its per-IP allowance is much tighter;
    #   - enroll is additionally limited per WALLET, because the cost that
    #     actually matters is how many distinct wallets get enrolled and
    #     wallet addresses are free to generate. Re-enrolling only refreshes
    #     an existing row's signals, so a handful a day is plenty.
    kyc_consent_rate_limit_per_hour: int = 120
    # How long a fetched consent-message stays signable. Stored in Redis and
    # embedded in the signed payload so a captured signature cannot be replayed
    # indefinitely (the previous message was a static string of wallet + version).
    kyc_consent_ttl_seconds: int = 300
    kyc_enroll_rate_limit_per_hour: int = 20
    kyc_enroll_wallet_rate_limit_per_day: int = 5

    # x402 endpoint directory (POST /x402/list paid, GET /x402/search free).
    # See app/modules/x402_directory/.
    x402_directory_store: str = "memory"
    # Flat listing fee, a Money string parsed by the tagged money parser in
    # modules/x402/client.py (which is also what attaches the challenge tag).
    x402_listing_price: str = "$0.10"
    # How long a paid listing stays live. Stated in the 402 offer's description
    # before the payer commits, and stored as the listing's term_end.
    x402_listing_term_days: int = 30
    # Free-endpoint abuse gate (CLAUDE.md section 9: rate limit every free
    # endpoint per IP), same Redis incr/expire shape as the contact form.
    x402_search_rate_limit_per_hour: int = 120
    # Hard cap on a search page — no unbounded listings (CLAUDE.md section 4).
    x402_search_max_results: int = 100
    # Hard cap on a probe-history read (roadmap item 7) — x402_probe_results
    # (097) TTLs at 30 days and holds at most ~1440 rows per listing at the
    # probe beat's current 30-min cadence, but the read is still bounded
    # independently (CLAUDE.md section 4), same reasoning as the search cap
    # above. Free, not priced: an owner call (2026-08-31) that real
    # uptime/latency history works better as a trust signal an agent (or
    # Relay) can point to freely than as its own paid product, the same
    # "don't charge for what's already effectively public" reasoning as the
    # News Engine's free article read.
    x402_probe_history_max_results: int = 200

    # x402 visibility board (POST /x402/board paid, GET /x402/board free).
    # See app/modules/x402_board/. Separate settings from the directory's on
    # purpose: it is a separate product whose price and term should move
    # independently of the directory's.
    x402_board_store: str = "memory"
    # Flat placement fee, a Money string parsed by the tagged money parser in
    # modules/x402/client.py (which is also what attaches the challenge tag).
    # Half the directory's listing fee: a board tile is pure presence, worth
    # less than a directory entry that makes an endpoint callable, and this is
    # meant to be the cheapest, lowest-friction paid surface an agent can try.
    x402_board_price: str = "$0.05"
    # How long a paid placement stays visible. Stated in the 402 offer's
    # description before the payer commits, and stored as term_end. Shorter
    # than the directory's 30 days: an advertising board has to churn to stay
    # worth reading, and a cheap tile should not buy a permanent squat.
    x402_board_term_days: int = 14
    # Free-endpoint abuse gate (CLAUDE.md section 9: rate limit every free
    # endpoint per IP), counted under its own key prefix, not the search one.
    x402_board_rate_limit_per_hour: int = 120
    # Hard cap on a board page — no unbounded listings (CLAUDE.md section 4).
    x402_board_max_results: int = 100
    # x402 feature-request board (POST /x402/features free, POST
    # /x402/features/:id/vote paid, GET /x402/features free, GET
    # /x402/features/demand paid). See app/modules/x402_features/. Separate
    # settings from the directory's and the board's on purpose: a third product
    # whose prices should move independently of theirs.
    x402_features_store: str = "memory"
    # Filing a request is free and anonymous (owner decision 2026-08-30: the
    # board's job is collecting endpoint ideas from agents, and a fee is
    # friction against that), so there is no request price. The only brake on
    # a flood of free filings is this per-IP hourly budget (CLAUDE.md section
    # 9: rate limit every free endpoint), counted under its own key, separate
    # from the browse budget below. Low on purpose: an honest agent files a
    # handful of ideas, not hundreds.
    x402_features_submit_rate_limit_per_hour: int = 20
    # Flat fee per vote. Money strings, parsed by the tagged money parser in
    # modules/x402/client.py (which is also what attaches the challenge tag).
    # Not free and not dust, because the entire point of a PAID vote board is
    # that the payment is the costly signal a free upvote cannot be. Keep this
    # flat -- the ranking counts votes, and a count is only amount-weighted
    # while every vote costs the same (see FeatureService.vote).
    x402_features_vote_price: str = "$0.02"
    # Fee to read the ranked demand signal. Still the most expensive surface
    # in the module (it resells every vote every agent has paid for, not one
    # write), but cut from $0.25 to $0.05 on 2026-08-30 -- $0.25 was an outlier
    # against every other paid-read price point in the marketplace (board
    # placement, KYC lookup both sit at $0.05), and a price
    # that high directly suppresses the paid-intent volume the competition's
    # Volume score is counting. $0.05 keeps it priced above a single vote
    # while matching the marketplace's established paid-read tier.
    x402_features_demand_price: str = "$0.05"
    # Free-endpoint abuse gate (CLAUDE.md section 9: rate limit every free
    # endpoint per IP), counted under its own key prefix, not the search or
    # board one. The paid demand read is not counted against this.
    x402_features_rate_limit_per_hour: int = 120
    # Hard cap on a browse or demand page — no unbounded listings (CLAUDE.md
    # section 4).
    x402_features_max_results: int = 100
    # How many requests the paid demand read scans before ranking them. The
    # ranking is an in-memory sort (see FeatureService.rank_by_demand for why
    # there is no third denormalized table), so this is what bounds it: the
    # ranking is exact while the board holds fewer requests than this, and
    # degrades to "the top of the N most recent" past it. Raise it, or build
    # the sweep-rebuilt rank projection, before the board outgrows it.
    x402_features_demand_scan_limit: int = 500
    # x402 endpoint grading (POST /x402/grades paid, GET /x402/grades/score
    # paid, GET /x402/grades free). See app/modules/x402_grading/. A fourth
    # product with its own settings for the same reason as the other three:
    # its prices should move independently of theirs.
    x402_grading_store: str = "memory"
    # Flat fee to submit one grade. Money strings, parsed by the tagged money
    # parser in modules/x402/client.py (which is also what attaches the
    # challenge tag).
    #
    # THIS FEE IS THE "STAKE" of roadmap item 6, and it is a one-way payment.
    # Nothing is held, escrowed, refunded, forfeited or slashed anywhere in
    # this module -- CLAUDE.md section 9 bars this project from holding user
    # funds, and the escrow primitive belongs to roadmap item 5's smart
    # contract, which is not started.
    #
    # Priced at the feature board's vote fee, not its request fee: a grade is
    # the same act as a vote -- one small paid datum contributed to an
    # aggregate somebody else reads -- and both are worthless as signals if
    # the fee is high enough that honest agents skip them. Flooding is bounded
    # by the one-grade-per-(grader, url) rule rather than by price.
    x402_grading_grade_price: str = "$0.02"
    # Fee to read one endpoint's aggregate score. Still priced above the grade
    # fee (an aggregate read is worth more than one contributed data point),
    # but cut from $0.10 to $0.03 on 2026-08-30 against a real external
    # comparable: Verun, the Berlin-hackathon-winning agent-trust-score
    # product doing the same thing on Algorand, charges $0.01 per verdict --
    # $0.10 was 10x that for a directly comparable read, which both looks
    # uncompetitive and suppresses the paid-intent volume the competition's
    # Volume score counts.
    x402_grading_score_price: str = "$0.03"
    # Free-endpoint abuse gate (CLAUDE.md section 9: rate limit every free
    # endpoint per IP), counted under its own key prefix, not the search,
    # board or features one.
    x402_grading_rate_limit_per_hour: int = 120
    # Hard cap on a free-index page and on how many individual grades the paid
    # score lookup serves — no unbounded listings (CLAUDE.md section 4).
    x402_grading_max_results: int = 100
    # How many grades of one endpoint the aggregate reads before averaging. The
    # aggregate is computed in Python over a single LIMITed partition read (see
    # GradingService.aggregate for why there is no counter column), so this is
    # what bounds it: the average is exact while an endpoint has fewer graders
    # than this, and the response says truncated=true past it.
    x402_grading_scan_limit: int = 500
    # Credibility weighting of the paid aggregate. A grade's weight is
    #   min(base + that wallet's all-time atomic spend with us, max)
    # summed over the settlement ledger at read time. Read
    # modules/x402_grading/services/credibility.py before changing either;
    # both numbers are in atomic units of the payment asset (USDC has 6
    # decimals, so 10_000 = $0.01).
    #
    # The base is what every grade is worth before any spending history. It is
    # never zero: each grade was itself paid for, and a zero weight would
    # silently delete a paid grade from the average. It is also what every
    # weight falls back to when the ledger cannot be read, which makes the
    # weighted mean degrade to the plain mean rather than to 0/0.
    x402_grading_base_weight_atomic: int = 10_000
    # Ceiling on one wallet's weight, so credibility cannot be bought outright:
    # without it a single wallet that has spent enough with us outweighs every
    # honest grader combined. 100x the base -- a large but finite multiple of a
    # newcomer's influence.
    x402_grading_max_weight_atomic: int = 1_000_000
    # How far back the credibility sum reads the settlement ledger, in whole
    # UTC days. Each day is one partition-key read with a bound LIMIT, so this
    # is literally the number of queries one paid score lookup costs (the sum
    # answers for every grader of that endpoint in ONE pass, so it does not
    # scale with grader count). It also states a product rule: credibility is
    # earned by recent spending, not by a wallet's whole history.
    x402_grading_spend_lookback_days: int = 30
    # Rows read per ledger day partition during a credibility sum. Bounds the
    # scan at lookback_days x this; a payer whose settlements are past this
    # many rows into a busy day is under-counted, which is the argument for
    # building the by-payer ledger projection credibility.py flags rather than
    # raising this.
    x402_grading_spend_scan_limit: int = 500
    # Usage-proof-of-payment (owner ask 2026-09-02): every grade submission
    # must name a payment txid proving the grader actually transacted with
    # the endpoint being graded, verified independently on-chain via the
    # public indexer (and, for a URL not listed with us, a live SSRF-guarded
    # fetch of its own 402 offer to learn its payTo -- see
    # services/usage_proof.py). This does NOT change credibility weighting
    # (services/credibility.py is untouched); it is a visible per-grade flag
    # only. Timeout for the indexer HTTP call.
    x402_grading_usage_proof_timeout_s: float = 8.0
    # Policy for when the indexer itself cannot be reached (not "the proof
    # was wrong" -- that is always a hard 400, this setting cannot soften
    # it). True (default, matches "no txid, no grade, full stop"): the grade
    # submission is rejected until the indexer is reachable again, coupling
    # grading uptime to indexer uptime on purpose -- a grade this module
    # cannot verify is not stored as if it were. False: an operator escape
    # hatch for a known indexer outage -- the grade still submits with
    # usage_verified=False rather than blocking the product outright.
    x402_grading_usage_proof_required: bool = True

    # ── x402 News Engine pay-per-call (GET /x402/news and GET
    # /x402/news/articles/:id both free, GET /x402/news/search paid). See
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
    # endpoint per IP), counted under its own key prefix, separate from the
    # other x402 products' budgets. Only the free headline list is counted.
    x402_news_rate_limit_per_hour: int = 120
    # Hard cap on a headline page and on paid search hits -- no unbounded
    # listings (CLAUDE.md section 4).
    x402_news_max_results: int = 50
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

    # ── x402 uptime/reachability check. See app/modules/x402_uptime/ and
    # docs/x402-uptime-check-design.md. Prototype/v0: a single GET, no
    # response body ever downloaded (status line + headers + timing only),
    # SSRF-guarded on every hop (reuses app.core.ssrf_guard.resolve_public_ip,
    # same shared primitive x402_scan already uses), cached per target with an
    # asymmetric TTL, rate-limited on two independent dimensions (caller IP
    # and target host). Disabled by default — this is a design prototype,
    # not a live product; the price is directly anchored against
    # x402_ping_price/x402_news_search_price (both $0.001), but both
    # rate-limit numbers and the cache TTLs below are reasoned defaults, not
    # owner-confirmed operating parameters — see the design doc's Open
    # Questions before flipping this on.
    x402_uptime_enabled: bool = False
    x402_uptime_price: str = "$0.001"
    # Per caller IP, covering the free pre-payment surface (URL validation,
    # a 402 offer lookup) the same way x402_scan_rate_limit_per_hour does —
    # 120/hour matches the majority convention in this codebase
    # (news/board/features/grading/catalog) rather than scan's tighter
    # 30/hour, because this endpoint's pre-payment work is cheap (URL
    # parsing only, no sandbox/container spin-up).
    x402_uptime_rate_limit_per_hour: int = 120
    # Per target host[:port], counting only REAL fetches (cache hits never
    # increment it) — the actual DDoS defense: bounds real outbound traffic
    # to any one target regardless of how many different callers/wallets pay
    # for a check. No existing precedent in this codebase for a
    # caller-chosen-destination limit; 20/hour (~1 real check every 3
    # minutes per target) is a fresh judgment call, not derived from
    # anything else here.
    x402_uptime_target_rate_limit_per_hour: int = 20
    # Asymmetric cache freshness window: a "down" result (unreachable or a
    # 5xx) is trusted for less time than an "up" one, same direction as the
    # media proxy's own asymmetric TTL (24h success vs 1h failure
    # placeholder) — a real recovery should become visible again quickly.
    # Which direction is actually SAFER to get wrong is an open product-trust
    # question the owner hasn't stated; see the design doc.
    x402_uptime_cache_ttl_up_seconds: int = 180
    x402_uptime_cache_ttl_down_seconds: int = 30
    # Per-hop connect+read-headers timeout. No separate overall deadline is
    # needed the way x402_scan/the x402 probe require one: there is no
    # response body to slow-drip here, so the worst case is bounded by this
    # value times (x402_uptime_max_redirects + 1).
    x402_uptime_check_timeout_s: float = 5.0
    x402_uptime_max_redirects: int = 3
    # ── end x402 uptime/reachability check ──

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
    # Money string per MB, parsed by the tagged money parser in
    # modules/x402/client.py. The route prices a paid upload at
    # ceil(declared_size_bytes / 1MB) * this rate, computed before the
    # payment gate (the price must be fixed before the 402 offer).
    x402_storage_price_per_mb: str = "$0.02"
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
    # How long one paid create (or a renewal) tries to add, in days. The
    # remaining window is ALSO capped by x402_storage_max_remaining_days
    # (renewing early cannot stack past that ceiling).
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

    # ── x402 agent social network, Phase S0 only (identity/foundation layer;
    # see docs/x402-social-design.md and CLAUDE.md section 9 roadmap). The
    # module directory is app/modules/x402_social/. S1 (posts/comments/
    # reactions/follows/groups/trending) and S2 (community moderation,
    # explicitly NOT approved -- see the design doc's own section 5 sign-off
    # block) add their own settings in their own phases; only the S0 subset
    # lives here.
    x402_social_store: str = "memory"
    # One-time identity floor (design doc section 2.1): matches the
    # directory's listing price. Paid because an unpriced registration would
    # be a free sybil mint -- the eligibility rule Phase S2's community
    # moderation design leans on assumes registration cost real money.
    x402_social_register_price: str = "$0.10"
    # Free-endpoint abuse gate (CLAUDE.md section 9: rate limit every free
    # endpoint per wallet AND per IP) for POST /auth/challenge + POST
    # /auth/session together (design doc section 4.2) -- deliberately fails
    # open even on a Redis outage: the signature check downstream is the
    # real security boundary for this pair of routes, not the rate limit.
    x402_social_session_rate_limit_per_hour: int = 60
    # Same gate, for the free session-authenticated writes (PATCH /profile in
    # Phase S0; unfollow/leave/group-mod actions join this budget in later
    # phases) -- keyed by wallet, not IP, since the caller is already
    # session-authenticated by the time this runs.
    x402_social_free_write_rate_limit_per_hour: int = 60
    # Free-endpoint abuse gate for the two Phase-S0 free read routes (GET
    # /agents, GET /agents/{wallet}) -- per IP only, same shape as
    # x402_search_rate_limit_per_hour. NOT in the design doc's own Phase-S0
    # settings enumeration (docs/x402-social-design.md section 1 only names
    # one x402_social_read_rate_limit_per_hour covering every phase's free
    # reads); added here anyway because CLAUDE.md section 9's "rate limit
    # every free endpoint per wallet and per IP" is non-negotiable and Phase
    # S0 already ships two free read routes that need it -- S1's additional
    # free reads (feed, comments, trending, ...) reuse this same setting when
    # they ship, so it is not renamed or duplicated later.
    x402_social_read_rate_limit_per_hour: int = 600
    # Bearer session token lifetime (design doc section 4.2): Redis-only,
    # deliberately -- a lost session is a 60-second re-login for an agent
    # that holds its own key, so durability buys nothing.
    x402_social_session_ttl_seconds: int = 86400
    # Hard cap on a page of the agent directory (GET /agents) -- no unbounded
    # listings (CLAUDE.md section 4).
    x402_social_max_results: int = 100
    # Agent Discovery Search (added 2026-09-03): GET /agents/search, a paid
    # read (like x402_features_demand / x402_news_search) over the free-text
    # interests field, which has no other way to filter/search today.
    x402_social_agent_search_price: str = "$0.01"
    # ── end x402 agent social network (Phase S0) ──────────────────────────────

    # ── x402 agent social network, Phase S1 (the network: posts, comments,
    # reactions, follows, groups, trending -- design doc sections 2.2-2.9).
    x402_social_post_price: str = "$0.01"
    x402_social_comment_price: str = "$0.005"
    x402_social_react_price: str = "$0.002"
    x402_social_follow_price: str = "$0.005"
    x402_social_group_create_price: str = "$0.25"
    x402_social_group_join_price: str = "$0.01"
    # Markdown body size cap for POST /posts (design doc section 2.2) --
    # checked by services/markdown_guard.py, BEFORE the payment gate.
    x402_social_post_max_bytes: int = 16384
    x402_social_max_tags: int = 5
    # Home feed (GET /feed) read-side fan-out cap: at most this many of a
    # caller's most-recently-followed agents AND at most this many of their
    # most-recently-joined groups are scanned per read (design doc section
    # 2.4). The response reports "truncated_to" when either cap bites.
    x402_social_feed_fanout_limit: int = 50
    # ── end x402 agent social network (Phase S1) ──────────────────────────────

    # ── x402 agent social network, Phase S2: community moderation (design
    # doc section 5, owner sign-off 2026-09-03; section 8.1's admin
    # emergency lever shares this same audit trail but has no settings of
    # its own -- it is require_admin_wallet-gated, not priced). Master flag:
    # the whole S2 surface (POST /reports, GET /cases, GET /cases/{id},
    # POST /cases/{id}/vote, the standing karma fields) stays unregistered
    # until this is flipped, separate from and in addition to
    # x402_social_store's own "memory" gate -- see
    # api/routes.py.register_x402_social_routes and falcon_main.py.
    x402_social_moderation_enabled: bool = False
    # The most expensive recurring action on the platform (design doc
    # section 5.2): a report conscripts other agents' attention and puts a
    # target's standing at stake, so it is deliberately priced above the
    # post it attacks (5x x402_social_post_price).
    x402_social_report_price: str = "$0.05"
    # Low, because quorum needs volunteers -- but paid, because a free vote
    # is a free sybil lever; combined with the registration-predates-the-
    # case eligibility rule (moderation_service.py), stuffing a vote costs
    # real, ledger-visible money.
    x402_social_case_vote_price: str = "$0.005"
    # Voting window (design doc section 5.3): _resolve_if_due resolves a
    # case lazily, on the next read or write that touches it after this many
    # seconds have elapsed since it opened -- no scheduler, no Celery here.
    x402_social_case_window_seconds: int = 86400
    # Minimum distinct eligible voters for a case to resolve upheld (design
    # doc section 5.3) -- below this, the window expiring resolves
    # not-upheld regardless of the ratio.
    x402_social_case_quorum: int = 5
    # uphold / total >= this ratio, AND quorum met, resolves a case upheld
    # (design doc section 5.3). Two ints, not a single float (fixed
    # 2026-09-03, A5): the old `x402_social_case_uphold_ratio: float = 0.667`
    # could never land on an exact two-thirds split -- 4/6, 6/9, 8/12 all
    # compute as 0.6666... < the float literal 0.667 and resolved REJECTED
    # even though "at least two-thirds" was the evident intent (a float
    # boundary bug, not a design choice). moderation_service._resolve_case
    # compares `tally.uphold * ratio_denominator >= ratio_numerator * total`
    # -- exact integer arithmetic, no floating-point boundary, so an exact
    # split always resolves correctly regardless of what these two ints are
    # set to.
    x402_social_case_uphold_ratio_numerator: int = 2
    x402_social_case_uphold_ratio_denominator: int = 3
    # The section 5.4 ban formula's three knobs: ban_seconds =
    # min(base x multiplier**offenses_in_window, cap). Base 30 min, x4,
    # cap 30 days pins the "30m -> 2h -> 8h -> 32h -> ~5.3d -> ~21d -> 30d
    # (cap)" sequence the design doc documents and moderation_service's
    # regression tests pin exactly.
    x402_social_ban_base_seconds: int = 1800
    x402_social_ban_multiplier: int = 4
    x402_social_ban_cap_seconds: int = 2592000
    # Offenses older than this stop counting toward the ban formula's
    # offenses_in_window (design doc section 5.4) -- bans rehabilitate,
    # they do not accumulate eternally. The LIFETIME offense_count on
    # x402_social_standing is never decremented by this; only ban severity
    # decays.
    x402_social_offense_decay_days: int = 90
    # Section 5.4.1's two independent false-report throttles. Concurrency
    # cap: at most this many unresolved reports per reporter at once
    # (caller-fault, payment kept, 409, enforced by an LWT slot claim on
    # x402_social_reporter_slots). Cooldown: escalating filing throttle on
    # every rejected resolution, base 15 min doubling per consecutive
    # rejection, capped at 7 days, streak reset to 0 on an upheld report --
    # refused pre-gate, free, 403, while active (a platform-imposed throttle
    # the caller could not have avoided by paying more).
    x402_social_report_max_open: int = 2
    x402_social_report_cooldown_base_seconds: int = 900
    x402_social_report_cooldown_cap_seconds: int = 604800
    # ── end x402 agent social network (Phase S2) ──────────────────────────────

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
    # A deliberately trivial paid route (GET /api/v1/x402/ping): proves a
    # client's signing pipeline actually works, and tests the facilitator's
    # tolerance for a sub-cent price, before risking money on a real product.
    # $0.001 == 1000 atomic USDC units at 6 decimals, no rounding loss.
    x402_ping_price: str = "$0.001"

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
    # Preview (`?preview=true`, checked by the route, see x402_ping): bypasses
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
