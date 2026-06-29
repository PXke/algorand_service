"""Application settings (msgspec.Struct, env-driven).

Replaces pydantic-settings: fields default below, and are overridden from a `.env`
file (dev) then the process environment (prod, injected via the systemd
EnvironmentFile) by the matching UPPER-CASED name. Values are coerced to the
field type. Unknown env vars are ignored.
"""

import os
from typing import get_args

import msgspec


class Settings(msgspec.Struct, kw_only=True):
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
    site_name: str = "PXke Algorand"
    site_tagline: str = "Algorand ecosystem news, search and tools."
    # Absolute path to the built Flutter web dir (holds index.html). Empty =
    # auto-detect: <release>/frontend_web (prod) then frontend_flutter/build/web (dev).
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
    news_max_articles_per_day: int = 7
    news_feed_limit: int = 50
    news_placement_slot: str = "news_feed_inline"
    news_placement_limit: int = 5
    news_placement_every_n_articles: int = 5
    # After an admin rejects a review, suppress re-enqueueing that URL in the
    # worker pipeline for this long (seconds). Mirror of the worker's
    # URL_REJECT_COOLDOWN_TTL — both must point redis_url at the same Redis DB.
    url_reject_cooldown_ttl: int = 7 * 24 * 3600

    celery_broker_url: str = "redis://localhost:6379/1"
    ingest_api_key: str = ""
    admin_wallet_addresses: str = ""

    @property
    def cors_origins(self) -> list[str]:
        raw = self.cors_allowed_origins.strip()
        if not raw:
            return []
        return [origin.strip() for origin in raw.split(",") if origin.strip()]


_TRUTHY = {"1", "true", "yes", "on"}


def _parse_dotenv(path: str) -> dict[str, str]:
    """Minimal KEY=VALUE .env reader (comments/blank lines skipped, quotes stripped)."""
    out: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                out[key.strip()] = value.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return out


def _coerce(value: str, typ: object):
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
