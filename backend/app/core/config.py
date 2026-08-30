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

    # x402 paid-endpoint plumbing (Algorand Global x402 Challenge). Off by
    # default until a facilitator/pay_to address is actually configured.
    x402_enabled: bool = False
    x402_facilitator_url: str = "https://facilitator.goplausible.xyz/"
    # TestNet CAIP-2 id by default — flip to the mainnet genesis hash for the
    # real contest submission, not before.
    x402_network: str = "algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI="
    # Public address only — no private key is held by this module.
    x402_pay_to_address: str = ""

    # KYC-as-a-service (the x402 challenge's actual product): free wallet
    # enrollment + trust-signal computation, then a paid x402 lookup that
    # splits its fee with the enrolled wallet. See app/modules/kyc/.
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
    # x402 feature-request board (POST /x402/features paid, POST
    # /x402/features/:id/vote paid, GET /x402/features free, GET
    # /x402/features/demand paid). See app/modules/x402_features/. Separate
    # settings from the directory's and the board's on purpose: a third product
    # whose prices should move independently of theirs.
    x402_features_store: str = "memory"
    # Flat fee to file one feature request. Money strings, parsed by the tagged
    # money parser in modules/x402/client.py (which is also what attaches the
    # challenge tag).
    #
    # Priced at the board's placement fee, not the directory's listing fee:
    # both are one paid write of a short piece of text, and filing a request is
    # meant to be as low-friction as putting up a tile.
    x402_features_request_price: str = "$0.05"
    # Flat fee per vote. Below the request fee on purpose: a vote is a smaller
    # act than authoring a request, and the demand signal is only as good as
    # the number of honest agents willing to cast one. Not free and not dust,
    # because the entire point of a PAID vote board is that the payment is the
    # costly signal a free upvote cannot be. Keep this flat -- the ranking
    # counts votes, and a count is only amount-weighted while every vote costs
    # the same (see FeatureService.vote).
    x402_features_vote_price: str = "$0.02"
    # Fee to read the ranked demand signal. The most expensive surface in the
    # module by an order of magnitude, and the only one priced above the other
    # products' write fees: it is the aggregate of every vote every agent has
    # paid for, so a builder reading it is buying the whole board's accumulated
    # paid signal rather than performing one write. It is also the only read
    # here that resells other payers' contributions, which is what makes it
    # worth more than a nickel.
    x402_features_demand_price: str = "$0.25"
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
    # Fee to read one endpoint's aggregate score. 5x the grade fee: a grade is
    # one data point and this is every grader's paid contribution to that
    # endpoint at once, the same reasoning that puts the feature board's demand
    # read above its vote. Below that demand read's $0.25 because this returns
    # one endpoint's score rather than the whole board's ranking.
    x402_grading_score_price: str = "$0.10"
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

    # Replay window for an already-spent payment header. Must be >= 2x the
    # facilitator's own HTTP timeout (FacilitatorConfig.timeout defaults to
    # 30s in x402-avm==2.0.2) so a header can never be re-presented while the
    # first settle of it is still in flight.
    x402_replay_ttl_seconds: int = 900

    @property
    def cors_origins(self) -> list[str]:
        """Parse the comma-separated CORS origins setting into a list."""
        raw = self.cors_allowed_origins.strip()
        if not raw:
            return []
        return [origin.strip() for origin in raw.split(",") if origin.strip()]


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
