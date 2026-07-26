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
    # Robyn concurrency. `processes` = separate OS processes (separate GILs -> true
    # parallelism for the blocking Cassandra/Redis handlers); `workers` = Actix I/O
    # worker threads per process. Default 1/1 serialises everything behind one slow
    # request, so we run several. Tune per box via APP_PROCESSES / APP_WORKERS.
    app_processes: int = 4
    app_workers: int = 2

    # Public-facing site (used to build absolute canonical / OG / sitemap URLs
    # in the SEO-rendered document routes). Override per-env via PUBLIC_SITE_URL.
    public_site_url: str = "https://algorand.pxke.me"
    # Reserved for future resource hints. API preconnect was removed because
    # client fetches are deferred and early preconnect hurt Lighthouse scores.
    public_api_url: str = ""
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
    seo_default_image: str = "/icons/Icon-512.png"
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
    news_placement_every_n_articles: int = 5
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


settings = _load()
