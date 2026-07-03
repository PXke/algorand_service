"""First-party pageview analytics over Cassandra counters + Redis HLL.

Recorded server-side from the SSR document routes (and the search API), plus a
first-party beacon POST for Flutter in-app route changes (see
seo/api/routes.py:beacon_pageview) — no third-party JS, and it stays within
the Cassandra/Typesense/Redis stack. Pageview, referrer, bot, search and 404
tallies are Cassandra counters; unique-visitor counts are privacy-safe Redis
HyperLogLogs (no raw IP stored). Writes are fire-and-forget so they never add
latency; reads are admin-only and infrequent.
"""

from __future__ import annotations

import contextlib
import hashlib
import ipaddress
import logging
from datetime import UTC, datetime
from functools import lru_cache
from urllib.parse import urlparse

from app.core.config import settings

log = logging.getLogger(__name__)

_BOT_TOKENS = (
    "bot",
    "crawl",
    "spider",
    "slurp",
    "bingpreview",
    "facebookexternalhit",
    "embedly",
    "quora link preview",
    "outbrain",
    "pinterest",
    "vkshare",
    "w3c_validator",
    "headless",
    "python-requests",
    "httpx",
    "curl",
    "wget",
    "go-http-client",
    "ahrefs",
    "semrush",
    "gptbot",
    "ccbot",
    "claudebot",
    "perplexitybot",
    "google-extended",
    "applebot",
    "yandex",
    "duckduckbot",
    # Uptime monitors, link unfurlers and SEO/scraping tools — non-human traffic
    # that lacks the tokens above and would otherwise count as "human".
    "uptimerobot",
    "pingdom",
    "statuscake",
    "site24x7",
    "betteruptime",
    "datadog",
    "newrelicpinger",
    "prometheus",
    "kube-probe",
    "lighthouse",
    "gtmetrix",
    "petalbot",
    "bytespider",
    "dataforseo",
    "mj12bot",
    "dotbot",
    "screaming frog",
    "telegrambot",
    "whatsapp",
    "discordbot",
    "slackbot",
    "twitterbot",
    "linkedinbot",
    "skypeuripreview",
    "google favicon",
    # Internet-wide scanners / scrapers that send a browser-ish "Mozilla/" UA and
    # no Referer, so they slip the checks below and inflate human "(direct)".
    "zgrab",
    "zmap",
    "masscan",
    "nmap",
    "pathscan",
    "visionheight",
    "censys",
    "shodan",
    "internetmeasurement",
    "internet-measurement",
    "leakix",
    "expanse",
    "paloaltonetworks",
    "netsystemsresearch",
    "l9explore",
    "l9tcpid",
    "odin",
    "scanworld",
    "scaninfo",
    "/scan",
    "researchscan",
    "aani",
    "gdnplus",
)

_TODAY_FMT = "%Y-%m-%d"


def _today() -> str:
    return datetime.now(UTC).strftime(_TODAY_FMT)


def _recent_days(days: int, offset: int = 0) -> list[str]:
    """`days` calendar days ending `offset` days before today (offset=0 -> today)."""
    today = datetime.now(UTC).date()
    return [
        (today.fromordinal(today.toordinal() - i)).strftime(_TODAY_FMT)
        for i in range(offset, offset + days)
    ]


_STATIC_PATH_LABELS = {
    "/": "Home",
    "/news": "News index",
    "/search": "Search",
    "/about": "About",
    "/news/suggestions": "Suggestions",
}

_ARTICLE_PREFIX = "/news/articles/"
_SECTION_PREFIX = "/section/"


def _static_label(path: str) -> str:
    """Friendly label for a path that needs no DB lookup."""
    if path in _STATIC_PATH_LABELS:
        return _STATIC_PATH_LABELS[path]
    if path.startswith(_SECTION_PREFIX):
        return "Section · " + path[len(_SECTION_PREFIX) :]
    return path


def section_bucket(path: str) -> str:
    """Coarse content bucket for a path, for the section-level rollup. Article
    paths are bucketed generically here; the read layer upgrades them to the
    article's primary tag ('Section · DeFi') when it can resolve one."""
    if path in _STATIC_PATH_LABELS:
        return _STATIC_PATH_LABELS[path]
    if path.startswith(_SECTION_PREFIX):
        return "Section · " + path[len(_SECTION_PREFIX) :]
    if path.startswith(_ARTICLE_PREFIX):
        return "Article"
    return "Other"


def _resolve_labels(paths: list[str], article_cards: dict[str, object]) -> dict[str, str]:
    """Map each path to a human-readable label, from the already-fetched article
    metadata batch (see `_fetch_article_cards`) rather than a fresh DB lookup."""
    labels = {p: _static_label(p) for p in paths}
    for p in paths:
        if not p.startswith(_ARTICLE_PREFIX):
            continue
        row = article_cards.get(p)
        labels[p] = (row.title if row and row.title else None) or "Article"
    return labels


# Automation/headless signatures. Some (e.g. "headless") also appear in the bot
# denylist; kept here too so ua_class can label a UA even outside is_bot.
_HEADLESS_TOKENS = (
    "headless",
    "phantomjs",
    "electron",
    "puppeteer",
    "playwright",
    "selenium",
    "webdriver",
    "cypress",
    "splash",
)


def is_bot(user_agent: str | None) -> bool:
    ua = (user_agent or "").lower()
    if not ua:
        return True  # no UA -> almost always a script/scanner
    if any(tok in ua for tok in _BOT_TOKENS):
        return True
    # Every mainstream browser sends a "Mozilla/..." product token. A non-empty
    # UA without it is a library/scraper that just isn't in the denylist above —
    # the main thing that was inflating "human (direct)".
    return "mozilla/" not in ua


def ua_class(user_agent: str | None) -> str:
    """Coarse class for a UA, used to break down the '(direct)' bucket so plain
    browser traffic (dark social, bookmarks) is distinguishable from scripts."""
    ua = (user_agent or "").lower()
    if not ua:
        return "no-ua"
    if any(t in ua for t in _HEADLESS_TOKENS):
        return "headless"
    if "mozilla/" not in ua:
        return "non-browser"
    if "mobi" in ua or "android" in ua or "iphone" in ua or "ipad" in ua:
        return "mobile-browser"
    return "desktop-browser"


# Browser families worth distinguishing, matched against the UA. Order matters:
# Edge ships "Edg/", Chrome-derivatives ship "Chrome/", and Chrome itself ships
# "Safari/" — so the more specific token has to win first.
_BROWSER_FAMILIES = (
    ("Edge", ("edg/", "edga/", "edgios/")),
    ("Samsung Internet", ("samsungbrowser",)),
    ("Opera", ("opr/", "opera")),
    ("Chrome", ("chrome/", "crios/", "chromium")),
    ("Firefox", ("firefox/", "fxios/")),
    ("Safari", ("safari/",)),
)


def browser_family(user_agent: str | None) -> str:
    """Coarse browser family for a human UA (Chrome/Safari/Firefox/Edge…), or
    'Other' when nothing matches."""
    ua = (user_agent or "").lower()
    if not ua:
        return "Other"
    for name, tokens in _BROWSER_FAMILIES:
        if any(t in ua for t in tokens):
            return name
    return "Other"


# Friendly names for crawlers worth distinguishing, matched against the UA. Order
# matters: first hit wins, so put specific tokens before generic ones.
_BOT_NAMES = (
    ("Googlebot", ("googlebot", "google favicon", "google-extended")),
    ("Bingbot", ("bingbot", "bingpreview")),
    ("GPTBot", ("gptbot",)),
    ("ClaudeBot", ("claudebot", "anthropic")),
    ("PerplexityBot", ("perplexitybot",)),
    ("CCBot", ("ccbot",)),
    ("Applebot", ("applebot",)),
    ("DuckDuckBot", ("duckduckbot",)),
    ("YandexBot", ("yandex",)),
    ("Bytespider", ("bytespider",)),
    ("AhrefsBot", ("ahrefs",)),
    ("SemrushBot", ("semrush",)),
    (
        "Social unfurler",
        (
            "facebookexternalhit",
            "twitterbot",
            "linkedinbot",
            "slackbot",
            "discordbot",
            "telegrambot",
            "whatsapp",
            "skypeuripreview",
            "embedly",
            "pinterest",
            "vkshare",
            "quora link preview",
        ),
    ),
    (
        "Uptime monitor",
        (
            "uptimerobot",
            "pingdom",
            "statuscake",
            "site24x7",
            "betteruptime",
            "datadog",
            "newrelicpinger",
            "prometheus",
            "kube-probe",
        ),
    ),
    (
        "SEO/audit tool",
        (
            "petalbot",
            "dataforseo",
            "mj12bot",
            "dotbot",
            "screaming frog",
            "lighthouse",
            "gtmetrix",
            "w3c_validator",
        ),
    ),
    (
        "Generic HTTP client",
        (
            "python-requests",
            "httpx",
            "curl",
            "wget",
            "go-http-client",
        ),
    ),
)


def bot_name(user_agent: str | None) -> str:
    """A friendly crawler name for a bot UA (e.g. 'GPTBot'), or 'Other bot'."""
    ua = (user_agent or "").lower()
    if not ua:
        return "No user-agent"
    for name, tokens in _BOT_NAMES:
        if any(t in ua for t in tokens):
            return name
    return "Other bot"


# Crawlers that fetch content to train or ground LLMs. "Are AIs reading us" is a
# first-class signal for a content site, so we surface their share of bot traffic
# as a headline KPI. Names match the labels `bot_name` already stores.
AI_CRAWLERS = frozenset(
    {
        "GPTBot",
        "ClaudeBot",
        "PerplexityBot",
        "CCBot",
        "Bytespider",
    }
)


def ai_crawler_stats(bot_rows: list[dict]) -> dict:
    """AI-crawler view total and its share of all bot views, from ranked
    `{bot, views}` rows."""
    total = sum(int(r.get("views", 0)) for r in bot_rows)
    ai = sum(int(r.get("views", 0)) for r in bot_rows if r.get("bot") in AI_CRAWLERS)
    return {"views": ai, "share_of_bots": (ai / total) if total else 0.0}


# ── Unique visitors (Redis HyperLogLog) ──────────────────────────────────────
# A privacy-safe visitor token = sha256(salt | ip | ua), truncated. No raw IP is
# ever stored; the salt makes the hash non-reversible. Daily HLL keys per kind;
# period uniques come from PFCOUNT over several daily keys (it unions without
# materializing). Everything fails open — Redis hiccups never block page serving.
_UV_PREFIX = "algorand:uv:"
_UV_TTL_SECONDS = 95 * 24 * 60 * 60  # a touch past the 90-day max read window


@lru_cache(maxsize=1)
def _uv_redis():
    import redis

    return redis.from_url(settings.redis_url, socket_connect_timeout=2)


def _uv_token(ip: str, ua: str | None) -> bytes:
    raw = f"{settings.analytics_hll_salt}|{ip}|{ua or ''}".encode()
    return hashlib.sha256(raw).digest()[:16]


def _uv_key(kind: str, day: str) -> str:
    return f"{_UV_PREFIX}{kind}:{day}"


def record_unique(kind: str, client_ip: str | None, user_agent: str | None, day: str) -> None:
    """Best-effort: add this visitor to the day's HyperLogLog for `kind`."""
    if not client_ip:
        return
    ip = client_ip.split(",")[0].strip()  # left-most of any X-Forwarded-For chain
    if not ip:
        return
    try:
        r = _uv_redis()
        key = _uv_key(kind, day)
        r.pfadd(key, _uv_token(ip, user_agent))
        r.expire(key, _UV_TTL_SECONDS)
    except Exception as exc:  # Redis down — uniques are non-critical
        log.debug("unique record skipped: %s", exc)


def _unique_counts(kind: str, window: list[str]) -> tuple[dict[str, int], int]:
    """Per-day unique counts plus the period-wide unique (union) for `kind`."""
    try:
        r = _uv_redis()
        per_day = {d: int(r.pfcount(_uv_key(kind, d))) for d in window}
        total = int(r.pfcount(*[_uv_key(kind, d) for d in window])) if window else 0
        return per_day, total
    except Exception as exc:
        log.debug("unique read skipped: %s", exc)
        return {}, 0


# ── Sessions & returning visitors (Redis presence keys + Cassandra counters) ──
# A "session" is a visitor token's activity within a 30-min sliding window. The
# first hit of a session is classified new vs returning by whether the token has
# a `seen:` marker (kept for the same ~95-day window as the uniques HLL). No raw
# IP is stored — the token is the same non-reversible hash used for uniques.
_SESSION_PREFIX = "algorand:sess:"
_SEEN_PREFIX = "algorand:seen:"
_SESSION_TTL_SECONDS = 30 * 60


def record_session(session, client_ip: str | None, user_agent: str | None, day: str) -> None:
    """Best-effort: stitch this human pageview into a session and, on a new
    session, bump session_daily split by new/returning. Also confirms a session
    as multi-page the moment its 2nd hit lands (vtype="multipage", counted
    separately from new/returning — see `_session_counts_from_rows`). A UA
    denylist alone can't catch a scraper spoofing a browser UA; a session that
    never gets a 2nd hit is a cheap, record-time-free bot-likelihood signal for
    the breakdowns instead. Fails open."""
    if not client_ip:
        return
    ip = client_ip.split(",")[0].strip()  # left-most of any X-Forwarded-For chain
    if not ip:
        return
    try:
        r = _uv_redis()
        token = _uv_token(ip, user_agent).hex()
        sess_key = f"{_SESSION_PREFIX}{token}"
        hits = r.incr(sess_key)
        r.expire(sess_key, _SESSION_TTL_SECONDS)  # (re)slide the 30-min window
        is_new_session = hits == 1
        if not is_new_session:
            if hits == 2:  # first repeat hit — confirms this session isn't a bounce
                try:
                    from app.core.statements import AnalyticsStmts

                    session.execute_async(AnalyticsStmts.SESSION_BUMP, (day, "multipage"))
                except Exception as exc:
                    log.debug("session multipage record skipped: %s", exc)
            return  # mid-session pageview — already counted at session start
        seen_key = f"{_SEEN_PREFIX}{token}"
        vtype = "returning" if r.exists(seen_key) else "new"
        r.set(seen_key, day, ex=_UV_TTL_SECONDS)  # remember/refresh this visitor
    except Exception as exc:  # Redis down — sessions are non-critical
        log.debug("session record (redis) skipped: %s", exc)
        return
    try:
        from app.core.statements import AnalyticsStmts

        session.execute_async(AnalyticsStmts.SESSION_BUMP, (day, vtype))
    except Exception as exc:
        log.debug("session record (cassandra) skipped: %s", exc)


def _session_counts_from_rows(session_by_day: dict[str, list]) -> dict[str, int]:
    """New/returning/total session counts summed across already-fetched
    `session_daily` rows. An empty/missing input (un-migrated table) yields
    zeros. `multipage` (sessions confirmed to have a 2nd hit) is tracked
    separately and excluded from `total` — it's a subset of new+returning,
    not an additional session."""
    out = {"new": 0, "returning": 0, "total": 0, "multipage": 0}
    for rows in session_by_day.values():
        for r in rows:
            n = int(r.sessions)
            out[r.vtype] = out.get(r.vtype, 0) + n
            if r.vtype in ("new", "returning"):
                out["total"] += n
    return out


# ── Geography (local GeoIP database, country-level, no IP stored) ─────────────
@lru_cache(maxsize=1)
def _geoip_reader():
    """Lazy DB-IP/MaxMind reader, or None when unavailable (lib or db missing)."""
    path = settings.geoip_db_path
    if not path:
        return None
    try:
        import geoip2.database

        return geoip2.database.Reader(path)
    except Exception as exc:  # missing lib / db file — geo just stays empty
        log.debug("geoip reader unavailable: %s", exc)
        return None


def country_for_ip(client_ip: str | None) -> str:
    """ISO-3166-1 alpha-2 country for a client IP, or '' when unknown. Fail-open:
    no DB, an unparseable/private IP, or any lookup error yields ''. The IP is
    never stored — only the resolved country is counted."""
    if not client_ip:
        return ""
    ip = client_ip.split(",")[0].strip()  # left-most of any X-Forwarded-For chain
    if not ip:
        return ""
    reader = _geoip_reader()
    if reader is None:
        return ""
    try:
        return reader.country(ip).country.iso_code or ""
    except Exception:  # address not in db / private / lookup error
        return ""


def _ignored_hosts() -> set[str]:
    """Hosts/IPs that count as our own (the server IP, configured office IPs)."""
    return {h.strip().lower() for h in settings.analytics_ignore_ips.split(",") if h.strip()}


def _own_host() -> str:
    """The public site's bare host (no scheme/www/port), for self-referral checks."""
    raw = settings.public_site_url
    host = (urlparse(raw).netloc or raw).lower().replace("www.", "")
    return host.split(":")[0]


def _extra_internal_hosts() -> set[str]:
    """Alternate hostnames that serve this same site (ANALYTICS_INTERNAL_HOSTS)."""
    return {
        h.strip().lower().replace("www.", "")
        for h in settings.analytics_internal_hosts.split(",")
        if h.strip()
    }


def _is_self_referral(host: str) -> bool:
    """True when `host` is our own site, an alternate hostname that serves it, or
    the hosting server's IP — an in-site navigation, not a real external referral."""
    if host in _ignored_hosts():
        return True
    if host in _extra_internal_hosts():  # exact match — keep unrelated sub-domains external
        return True
    own = _own_host()
    return bool(own) and (host == own or host.endswith("." + own))


# Link-shim / mobile sub-domains folded back to the canonical host so one source
# isn't scattered across rows (l.facebook.com, m.facebook.com … are all Facebook).
_CANONICAL_HOSTS = {
    "l.facebook.com": "facebook.com",
    "lm.facebook.com": "facebook.com",
    "m.facebook.com": "facebook.com",
    "web.facebook.com": "facebook.com",
    "l.instagram.com": "instagram.com",
    "out.reddit.com": "reddit.com",
    "old.reddit.com": "reddit.com",
    "np.reddit.com": "reddit.com",
    "m.youtube.com": "youtube.com",
    "l.messenger.com": "messenger.com",
}


def _canonical_host(host: str) -> str:
    """Fold known link-shim / mobile sub-domains onto their canonical host."""
    return _CANONICAL_HOSTS.get(host, host)


def referrer_host(referer: str | None) -> str:
    """Classify a referrer into a host, '(internal)' for self-referrals (in-site
    navigation, our own IP), or '(direct)' for a missing/blank referer.

    '(direct)' and '(internal)' are kept apart on purpose: true direct (dark
    social, bookmarks, no Referer) is a different signal from an in-site reload."""
    if not referer:
        return "(direct)"
    host = (urlparse(referer).netloc or "").lower().replace("www.", "")
    host = host.split(":")[0]  # drop any :port so IPs match the ignore list
    if not host:
        return "(direct)"
    if _is_self_referral(host):
        return "(internal)"
    return _canonical_host(host)[:120]


# Query params that carry no source identity — campaign/click tags that would
# otherwise explode the full-URL cardinality with near-duplicate rows.
_TRACKING_PARAMS = frozenset(
    {
        "fbclid",
        "gclid",
        "dclid",
        "gbraid",
        "wbraid",
        "msclkid",
        "yclid",
        "mc_eid",
        "mc_cid",
        "igshid",
        "igsh",
        "ref",
        "ref_src",
        "ref_url",
        "ref_source",
        "source",
        "cmpid",
        "campaign",
        "_hsenc",
        "_hsmi",
        "vero_id",
        "vero_conv",
        "oly_anon_id",
        "oly_enc_id",
        "spm",
    }
)


def normalize_referrer_url(referer: str | None) -> str | None:
    """Full external referrer URL, normalized for stable counting: scheme dropped,
    'www.' stripped, fragment dropped, tracking/campaign params removed, and the
    whole thing length-capped. Returns None for a missing/blank or self-referral
    URL so only real external sources get a per-URL counter."""
    from urllib.parse import parse_qsl, urlencode

    if not referer:
        return None
    try:
        u = urlparse(referer)
    except ValueError:
        return None
    host = (u.netloc or "").lower().replace("www.", "").split(":")[0]
    if not host or _is_self_referral(host):
        return None
    kept = [
        (k, v)
        for k, v in parse_qsl(u.query, keep_blank_values=False)
        if k.lower() not in _TRACKING_PARAMS and not k.lower().startswith("utm_")
    ]
    query = urlencode(kept)
    path = u.path or "/"
    norm = f"{host}{path}" + (f"?{query}" if query else "")
    return norm[:300]


# Referrer-host categories, matched by exact host or registrable-domain suffix.
# AI/News are listed before Social/Search so a more specific host (e.g.
# gemini.google.com -> AI) wins over a broader family domain. '(direct)' and
# '(internal)' are handled apart.
_REFERRER_CATEGORIES = (
    (
        "AI assistant",
        (
            "chatgpt.com",
            "openai.com",
            "perplexity.ai",
            "claude.ai",
            "anthropic.com",
            "gemini.google.com",
            "bard.google.com",
            "copilot.microsoft.com",
            "phind.com",
            "you.com",
        ),
    ),
    (
        "News & aggregators",
        (
            "coindesk.com",
            "cointelegraph.com",
            "cryptoslate.com",
            "decrypt.co",
            "theblock.co",
            "bloomberg.com",
            "feedly.com",
            "flipboard.com",
            "news.google.com",
            "techcrunch.com",
            "messari.io",
            "news.ycombinator.com",
        ),
    ),
    (
        "Social",
        (
            "reddit.com",
            "t.co",
            "twitter.com",
            "x.com",
            "facebook.com",
            "lnkd.in",
            "linkedin.com",
            "youtube.com",
            "youtu.be",
            "instagram.com",
            "tiktok.com",
            "t.me",
            "telegram.org",
            "discord.com",
            "mastodon.social",
            "bsky.app",
            "lemmy.world",
            "threads.net",
            "medium.com",
        ),
    ),
    (
        "Search",
        (
            "google.com",
            "bing.com",
            "duckduckgo.com",
            "ecosia.org",
            "yahoo.com",
            "yandex.com",
            "baidu.com",
            "qwant.com",
            "startpage.com",
            "search.brave.com",
            "search.marginalia.nu",
            "kagi.com",
        ),
    ),
)


def _host_in(host: str, token: str) -> bool:
    """True when `host` is `token` or a sub-domain of it."""
    return host == token or host.endswith("." + token)


def referrer_category(host: str) -> str:
    """Roll a referrer host (as returned by `referrer_host`) up into a coarse
    acquisition channel. '(direct)'/'(internal)' pass through as Direct/Internal."""
    if host == "(direct)":
        return "Direct"
    if host == "(internal)":
        return "Internal"
    h = host.lower()
    for name, tokens in _REFERRER_CATEGORIES:
        if any(_host_in(h, t) for t in tokens):
            return name
    return "Other"


def campaign_label(params: dict) -> str | None:
    """A short campaign label from a landing URL's query params, or None.

    Tagged links are the only reliable way to attribute dark-social / Facebook
    in-app traffic that arrives with no Referer. Prefers utm_source(+utm_campaign),
    falls back to a bare `ref`. Values are lower-cased, trimmed and length-capped
    to keep cardinality bounded."""

    def _g(key: str) -> str:
        v = params.get(key)
        if isinstance(v, (list, tuple)):  # some frameworks give a list per key
            v = v[0] if v else ""
        return (str(v or "").strip().lower())[:60]

    source = _g("utm_source")
    if source:
        campaign = _g("utm_campaign") or _g("utm_medium")
        return f"{source} / {campaign}" if campaign else source
    ref = _g("ref")
    if ref:
        return f"ref:{ref}"
    return None


def is_internal_client(client_ip: str | None) -> bool:
    """True for self/internal traffic we never count: the server's own public IP
    (per ANALYTICS_IGNORE_IPS), plus all loopback / private / link-local ranges
    (health checks, monitoring, SSR self-fetch)."""
    if not client_ip:
        return False
    ip = client_ip.split(",")[0].strip()  # X-Forwarded-For may be a chain
    if ip.lower() in _ignored_hosts():
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback or addr.is_link_local


def _record_direct(
    session, day: str, path: str, referer: str | None, user_agent: str | None
) -> None:
    """Diagnostics for a human pageview that landed in '(direct)': bump the
    UA-class counter and append a short-lived raw sample (TTL on the table)."""
    from app.core.statements import AnalyticsStmts

    uac = ua_class(user_agent)
    session.execute_async(AnalyticsStmts.DIRECT_UACLASS_BUMP, (day, uac))
    session.execute_async(
        AnalyticsStmts.DIRECT_SAMPLE_INSERT,
        (day, path[:200], (referer or "")[:300], (user_agent or "")[:300], uac),
    )


def record_pageview(
    *,
    path: str,
    referer: str | None,
    user_agent: str | None,
    client_ip: str | None = None,
    campaign: str | None = None,
) -> None:
    """Best-effort counter bumps for one document request. Never raises."""
    if is_internal_client(client_ip):
        return  # don't count the server itself / internal probes
    try:
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import AnalyticsStmts

        session = get_cassandra_session()
        day = _today()
        kind = "bot" if is_bot(user_agent) else "human"
        # Privacy-safe unique visitor count (Redis HLL), independent of Cassandra.
        record_unique(kind, client_ip, user_agent, day)
        session.execute_async(AnalyticsStmts.PAGEVIEW_BUMP, (kind, day))
        # Per-path counter, split by kind so Top Pages can show human vs bot.
        session.execute_async(AnalyticsStmts.PATH_KIND_BUMP, (day, path[:200], kind))
        if kind == "bot":
            # Which crawler — so the bot column identifies Googlebot/GPTBot/etc.
            session.execute_async(AnalyticsStmts.BOT_BUMP, (day, bot_name(user_agent)))
        else:
            # Server-side session stitching + new-vs-returning split (human only).
            record_session(session, client_ip, user_agent, day)
            # Country (GeoIP, no IP stored) and campaign tag (utm/ref) — human only.
            country = country_for_ip(client_ip)
            if country:
                session.execute_async(AnalyticsStmts.GEO_BUMP, (day, country))
            if campaign:
                session.execute_async(AnalyticsStmts.CAMPAIGN_BUMP, (day, campaign[:80]))
            # Site-wide device + browser + hour-of-day segmentation (human only).
            session.execute_async(AnalyticsStmts.DEVICE_BUMP, (day, ua_class(user_agent)))
            session.execute_async(AnalyticsStmts.BROWSER_BUMP, (day, browser_family(user_agent)))
            session.execute_async(AnalyticsStmts.HOUR_BUMP, (day, datetime.now(UTC).hour))
            referrer = referrer_host(referer)
            session.execute_async(AnalyticsStmts.REFERRER_BUMP, (day, referrer))
            # Source -> landing-page attribution (which referrer drove which page).
            session.execute_async(AnalyticsStmts.REFERRER_PATH_BUMP, (day, referrer, path[:200]))
            if referrer == "(direct)":
                _record_direct(session, day, path, referer, user_agent)
            else:
                # Full external referrer URL (which exact thread/page, not just the
                # host). Skipped for direct/internal by normalize_referrer_url.
                ref_url = normalize_referrer_url(referer)
                if ref_url:
                    session.execute_async(AnalyticsStmts.REFERRER_URL_BUMP, (day, ref_url))
    except Exception as exc:  # missing tables / cassandra down — analytics is non-critical
        log.debug("pageview record skipped: %s", exc)


def record_search(query: str, result_count: int, *, user_agent: str | None = None) -> None:
    """Best-effort: count a search term for the day (and separately when it
    returned nothing). Bots are skipped so the demand signal stays human."""
    q = (query or "").strip().lower()
    if not q or is_bot(user_agent):
        return
    try:
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import AnalyticsStmts

        session = get_cassandra_session()
        day = _today()
        q = q[:120]
        session.execute_async(AnalyticsStmts.SEARCH_BUMP, (day, q))
        if result_count <= 0:
            session.execute_async(AnalyticsStmts.SEARCH_ZERO_BUMP, (day, q))
    except Exception as exc:
        log.debug("search record skipped: %s", exc)


def record_notfound(*, path: str, client_ip: str | None = None) -> None:
    """Best-effort: count a request to an unknown article/section URL — broken
    inbound links and crawler waste. Internal/self traffic is excluded."""
    if is_internal_client(client_ip):
        return
    try:
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import AnalyticsStmts

        session = get_cassandra_session()
        session.execute_async(AnalyticsStmts.NOTFOUND_BUMP, (_today(), path[:200]))
    except Exception as exc:
        log.debug("notfound record skipped: %s", exc)


def _recent_direct_samples(session, window: set[str], limit: int) -> list[dict]:
    """Latest raw '(direct)' samples across the window, newest day first.

    The table clusters newest-first, so a per-day LIMIT yields the most recent
    rows without an ALLOW FILTERING scan; we stop once `limit` are collected."""
    from app.core.statements import AnalyticsStmts

    samples: list[dict] = []
    for day in sorted(window, reverse=True):
        if len(samples) >= limit:
            break
        rows = session.execute(AnalyticsStmts.DIRECT_SAMPLE_BY_DAY, (day, limit - len(samples)))
        for r in rows:
            samples.append(
                {
                    "day": day,
                    "path": r.path,
                    "referer": r.referer or "",
                    "user_agent": r.user_agent or "",
                    "ua_class": r.ua_class,
                }
            )
    return samples


def _build_alerts(out: dict, cur404: int, prev404: int) -> list[dict]:
    """A small rules pass producing at-a-glance anomaly chips, over data already
    aggregated in `out` (plus the whole-window 404 totals, not just the top N,
    passed in separately). Each alert is {level: info|warn, text}."""
    alerts: list[dict] = []

    def add(level: str, text: str) -> None:
        alerts.append({"level": level, "text": text})

    totals = out.get("totals") or {}
    prev = out.get("prev_totals") or {}

    # Human traffic swing vs the prior period.
    h, ph = int(totals.get("human", 0)), int(prev.get("human", 0))
    if ph > 0:
        delta = (h - ph) / ph * 100
        if delta <= -25:
            add("warn", f"Human visits down {abs(round(delta))}% vs the prior period.")
        elif delta >= 25:
            add("info", f"Human visits up {round(delta)}% vs the prior period.")

    # Returning-visitor rate, when there are enough sessions to be meaningful.
    sess = out.get("sessions") or {}
    if int(sess.get("total", 0)) >= 20 and float(sess.get("returning_rate", 0.0)) < 0.1:
        add("warn", f"Low returning-visitor rate ({round(sess['returning_rate'] * 100)}%).")

    # 404 spike vs the prior period.
    if cur404 >= 10 and (prev404 == 0 or cur404 >= 2 * prev404):
        add("warn", f"Broken/404 requests elevated ({cur404} in window).")

    # AI crawlers taking a large share of bot traffic.
    ai = out.get("ai_crawler") or {}
    if int(ai.get("views", 0)) >= 10 and float(ai.get("share_of_bots", 0.0)) >= 0.4:
        add("info", f"AI crawlers are {round(ai['share_of_bots'] * 100)}% of bot traffic.")

    # Searches that found nothing — direct content-gap signal.
    zero = out.get("zero_searches") or []
    if zero:
        add("info", f"{len(zero)} search term(s) returned no results — content gaps.")

    return alerts


# ── Bulk day-partition fan-out ───────────────────────────────────────────────
# Every read-path table below is one partition per day; the old implementation
# queried each table in its own sequential per-day loop (15+ tables x up to 90
# days = 1000+ blocking round-trips per read_analytics() call, several of them
# re-scanning the same table under different names — e.g. pageview_path_kind
# was read once each for top-pages, sections and the editorial scorecard).
# `_fetch_tables_by_day` runs every (table, day) pair in one concurrent batch
# via the driver's execute_concurrent, and callers share the result instead of
# re-querying.
def _fetch_tables_by_day(
    specs: dict[str, object], days: list[str], *, concurrency: int = 48
) -> dict[str, dict[str, list]]:
    """specs: {name: prepared_statement}. Returns {name: {day: rows}}, silently
    omitting a (name, day) whose query errored (e.g. a table a migration hasn't
    created yet) — same fail-open contract the old per-table try/except had."""
    from app.core.cassandra import execute_parallel

    pairs: list[tuple[str, str]] = []
    batch = []
    for name, stmt in specs.items():
        for day in days:
            pairs.append((name, day))
            batch.append((stmt, (day,)))
    out: dict[str, dict[str, list]] = {name: {} for name in specs}
    if not batch:
        return out
    results = execute_parallel(batch, concurrency=concurrency, raise_on_error=False)
    for (name, day), (ok, res) in zip(pairs, results, strict=True):
        if ok:
            out[name][day] = list(res)
        else:
            log.debug("%s/%s fetch skipped: %s", name, day, res)
    return out


def _rank(
    by_day: dict[str, list], key: str, value_col: str = "views", limit: int | None = None
) -> list[dict]:
    """Sum `value_col` per distinct `key` across already-fetched {day: rows},
    ranked descending. Output rows are always shaped {key: ..., "views": ...}
    regardless of the source column's name."""
    agg: dict = {}
    for rows in by_day.values():
        for r in rows:
            k = getattr(r, key)
            agg[k] = agg.get(k, 0) + int(getattr(r, value_col))
    ranked = sorted(agg.items(), key=lambda kv: kv[1], reverse=True)
    if limit is not None:
        ranked = ranked[:limit]
    return [{key: k, "views": v} for k, v in ranked]


def _sum_views(by_day: dict[str, list]) -> int:
    return sum(int(r.views) for rows in by_day.values() for r in rows)


def _paths_from_rows(path_kind_by_day: dict[str, list], *, limit: int) -> list[dict]:
    """Top pages carry a human/bot split (from the kind-partitioned table)."""
    agg: dict[str, dict[str, int]] = {}
    for rows in path_kind_by_day.values():
        for r in rows:
            bucket = agg.setdefault(r.path, {"human": 0, "bot": 0})
            bucket[r.kind] = bucket.get(r.kind, 0) + int(r.views)
    ranked = sorted(agg.items(), key=lambda kv: kv[1]["human"] + kv[1]["bot"], reverse=True)[:limit]
    return [
        {"path": p, "human": v["human"], "bot": v["bot"], "views": v["human"] + v["bot"]}
        for p, v in ranked
    ]


def _referrer_paths_from_rows(rp_by_day: dict[str, list], *, limit: int) -> list[dict]:
    """Top (referrer, landing-path) pairs over the window."""
    agg: dict[tuple[str, str], int] = {}
    for rows in rp_by_day.values():
        for r in rows:
            agg[(r.referrer, r.path)] = agg.get((r.referrer, r.path), 0) + int(r.views)
    ranked = sorted(agg.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [{"referrer": k[0], "path": k[1], "views": v} for k, v in ranked]


def _hours_from_rows(hour_by_day: dict[str, list]) -> list[dict]:
    """Human views per hour-of-day (0-23), summed across the window."""
    sums = dict.fromkeys(range(24), 0)
    for rows in hour_by_day.values():
        for r in rows:
            if r.hour is not None and 0 <= r.hour <= 23:
                sums[r.hour] += int(r.views)
    return [{"hour": h, "views": sums[h]} for h in range(24)]


def _referrer_categories_from_rows(referrer_by_day: dict[str, list]) -> list[dict]:
    """All referrer hosts rolled up into acquisition channels (Search, Social,
    AI, News, Direct, Internal, Other) over the window."""
    agg: dict[str, int] = {}
    for rows in referrer_by_day.values():
        for r in rows:
            cat = referrer_category(r.referrer)
            agg[cat] = agg.get(cat, 0) + int(r.views)
    ranked = sorted(agg.items(), key=lambda kv: kv[1], reverse=True)
    return [{"category": k, "views": v} for k, v in ranked]


def _sections_from_rows(
    path_kind_by_day: dict[str, list], article_cards: dict[str, object], *, limit: int
) -> list[dict]:
    """Human views per content section. Article paths resolve to the article's
    primary tag ('Section · DeFi') via the pre-fetched article metadata batch;
    everything else buckets by route (Home, Section · X, Search, Other)."""
    agg: dict[str, int] = {}
    for rows in path_kind_by_day.values():
        for r in rows:
            if r.kind != "human":
                continue
            path = r.path or ""
            if path.startswith(_ARTICLE_PREFIX):
                row = article_cards.get(path)
                bucket = "Section · " + str(row.tags[0]) if row and row.tags else "Article"
            else:
                bucket = section_bucket(path)
            agg[bucket] = agg.get(bucket, 0) + int(r.views)
    ranked = sorted(agg.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [{"section": k, "views": v} for k, v in ranked]


def _session_daily_from_rows(session_by_day: dict[str, list], window: set[str]) -> list[dict]:
    """Per-day new/returning session counts for the Audience chart."""
    per_day = {d: {"new": 0, "returning": 0} for d in window}
    for day, rows in session_by_day.items():
        if day not in per_day:
            continue
        for r in rows:
            if r.vtype in per_day[day]:
                per_day[day][r.vtype] += int(r.sessions)
    return [
        {"day": d, "new": per_day[d]["new"], "returning": per_day[d]["returning"]}
        for d in sorted(window)
    ]


def _ai_crawler_from_rows(bot_by_day: dict[str, list], window: set[str]) -> dict:
    """AI-crawler views (GPTBot/ClaudeBot/…), their share of all bot traffic,
    and a per-day trend."""
    daily = dict.fromkeys(window, 0)
    total_bot = total_ai = 0
    for day, rows in bot_by_day.items():
        for r in rows:
            v = int(r.views)
            total_bot += v
            if r.bot in AI_CRAWLERS:
                daily[day] = daily.get(day, 0) + v
                total_ai += v
    return {
        "views": total_ai,
        "share_of_bots": (total_ai / total_bot) if total_bot else 0.0,
        "daily": [{"day": d, "views": daily.get(d, 0)} for d in sorted(window)],
    }


def _editorial_scorecard_from_rows(
    path_kind_by_day: dict[str, list],
    article_cards: dict[str, object],
    window: set[str],
    *,
    limit: int,
) -> list[dict]:
    """Top articles by human views, each with age-since-publish and a daily view
    series — so a slow-burn explainer is distinguishable from a one-day spike."""
    per_article: dict[str, dict[str, int]] = {}
    for day, rows in path_kind_by_day.items():
        for r in rows:
            if r.kind != "human":
                continue
            path = r.path or ""
            if not path.startswith(_ARTICLE_PREFIX):
                continue
            per_article.setdefault(path, {})[day] = int(r.views)
    ranked = sorted(per_article.items(), key=lambda kv: sum(kv[1].values()), reverse=True)[:limit]
    today = datetime.now(UTC).date()
    rows_out: list[dict] = []
    for path, byday in ranked:
        row = article_cards.get(path)
        title = (row.title if row and row.title else None) or "Article"
        published = row.published_at if row else None
        tag = str(row.tags[0]) if row and row.tags else None
        published_iso, age_days = None, None
        if published is not None:
            try:
                published_iso = published.isoformat()
                age_days = (today - published.date()).days
            except Exception as exc:
                log.debug("scorecard age calc skipped for %s: %s", path, exc)
        rows_out.append(
            {
                "path": path,
                "label": title,
                "section": tag,
                "published_at": published_iso,
                "age_days": age_days,
                "views": sum(byday.values()),
                "daily": [{"day": d, "views": byday.get(d, 0)} for d in sorted(window)],
            }
        )
    return rows_out


def _distinct_article_ids(by_day: dict[str, dict[str, list]]) -> dict:
    """Every distinct article path referenced anywhere in the fetched tables,
    mapped to its parsed UUID — the union covers sections, the editorial
    scorecard and path-label resolution, so article metadata is fetched once."""
    from uuid import UUID

    ids: dict[str, UUID] = {}

    def _consider(path: str) -> None:
        if not path or path in ids or not path.startswith(_ARTICLE_PREFIX):
            return
        with contextlib.suppress(ValueError):
            ids[path] = UUID(path[len(_ARTICLE_PREFIX) :])

    for table in ("path_kind", "referrer_path", "notfound"):
        for rows in by_day.get(table, {}).values():
            for r in rows:
                _consider(r.path or "")
    return ids


def _fetch_article_cards(article_ids: dict) -> dict[str, object]:
    """One concurrent batch fetching title/published_at/tags for every distinct
    article path in `article_ids`, reused by section bucketing, the editorial
    scorecard and path-label resolution instead of each querying it again."""
    from app.core.cassandra import execute_parallel_with_args
    from app.core.statements import ArticleStmts

    if not article_ids:
        return {}
    paths = list(article_ids)
    results = execute_parallel_with_args(
        ArticleStmts.GET_CARD,
        [(article_ids[p],) for p in paths],
        concurrency=48,
        raise_on_error=False,
    )
    out: dict[str, object] = {}
    for path, (ok, res) in zip(paths, results, strict=True):
        if ok:
            row = res.one()
            if row:
                out[path] = row
    return out


def read_analytics(days: int = 14, *, top: int = 20) -> dict:
    """Daily human/bot series + aggregated top paths and referrers over `days`."""
    out: dict = {
        "days": days,
        "daily": [],
        "top_paths": [],
        "top_referrers": [],
        "totals": {},
        "prev_totals": {},
        "direct_uaclass": [],
        "direct_samples": [],
        "top_searches": [],
        "zero_searches": [],
        "top_bots": [],
        "referrer_paths": [],
        "top_notfound": [],
        "device": [],
        "browser": [],
        "hours": [],
        "referrer_categories": [],
        "sections": [],
        "top_referrer_urls": [],
        "sessions": {},
        "sessions_daily": [],
        "ai_crawler": {},
        "articles": [],
        "geo": [],
        "campaigns": [],
        "alerts": [],
    }
    try:
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import AnalyticsStmts

        session = get_cassandra_session()
        window = set(_recent_days(days))
        prev_window = set(_recent_days(days, offset=days))

        # Per-kind partition is one row/day — read it whole once, then slice both
        # the current and prior window in-app (avoids the driver's `IN` quirk).
        def _series(kind: str) -> dict[str, int]:
            rows = session.execute(AnalyticsStmts.PAGEVIEW_SERIES_BY_KIND, (kind,))
            return {r.day: int(r.views) for r in rows}

        human, bot = _series("human"), _series("bot")
        # Unique visitors (Redis HLL): per-day human uniques for the chart, plus
        # period-wide human/bot uniques (PFCOUNT unions the daily keys).
        uv_human_day, uv_human = _unique_counts("human", sorted(window))
        _, uv_bot = _unique_counts("bot", sorted(window))
        _, uv_human_prev = _unique_counts("human", sorted(prev_window))
        out["daily"] = [
            {
                "day": d,
                "human": human.get(d, 0),
                "bot": bot.get(d, 0),
                "human_unique": uv_human_day.get(d, 0),
            }
            for d in sorted(window)
        ]
        out["totals"] = {
            "human": sum(human.get(d, 0) for d in window),
            "bot": sum(bot.get(d, 0) for d in window),
            "human_unique": uv_human,
            "bot_unique": uv_bot,
        }
        out["prev_totals"] = {
            "human": sum(human.get(d, 0) for d in prev_window),
            "bot": sum(bot.get(d, 0) for d in prev_window),
            "human_unique": uv_human_prev,
        }

        # Every remaining table is one partition per day. Instead of a sequential
        # per-day loop per table (was 15+ tables x up to 90 days = 1000+ blocking
        # round-trips), fetch every (table, day) pair for the current window in
        # one concurrent batch and have every aggregation below share it — several
        # tables (path_kind, referrer, bot, session) were previously re-scanned
        # 2-3x under different names.
        table_specs = {
            "path_kind": AnalyticsStmts.PATH_KIND_BY_DAY,
            "referrer": AnalyticsStmts.AGG_REFERRER,
            "direct_uaclass": AnalyticsStmts.AGG_DIRECT_UACLASS,
            "search": AnalyticsStmts.AGG_SEARCH,
            "search_zero": AnalyticsStmts.AGG_SEARCH_ZERO,
            "bot": AnalyticsStmts.AGG_BOT,
            "notfound": AnalyticsStmts.AGG_NOTFOUND,
            "referrer_path": AnalyticsStmts.REFERRER_PATH_BY_DAY,
            "hour": AnalyticsStmts.HOUR_BY_DAY,
            "device": AnalyticsStmts.AGG_DEVICE,
            "browser": AnalyticsStmts.AGG_BROWSER,
            "referrer_url": AnalyticsStmts.AGG_REFERRER_URL,
            "session": AnalyticsStmts.SESSION_BY_DAY,
            "geo": AnalyticsStmts.AGG_GEO,
            "campaign": AnalyticsStmts.AGG_CAMPAIGN,
        }
        by_day = _fetch_tables_by_day(table_specs, sorted(window))
        # The prior window only needs two tables (session totals for the
        # returning-rate comparison, notfound totals for the 404-spike alert).
        prev_by_day = _fetch_tables_by_day(
            {"session": AnalyticsStmts.SESSION_BY_DAY, "notfound": AnalyticsStmts.AGG_NOTFOUND},
            sorted(prev_window),
        )

        # Sessions & returning visitors (server-side, from session_daily). Pages
        # per visit divides human pageviews by total sessions.
        sess = _session_counts_from_rows(by_day["session"])
        sess_total = sess.get("total", 0)
        human_total = out["totals"]["human"]
        # Sessions that never got a confirmed 2nd hit — a cheap bot-likelihood
        # signal (a UA denylist alone misses a scraper spoofing a browser UA)
        # surfaced here for the breakdown, not used to filter anything.
        bounce_sessions = max(sess_total - sess.get("multipage", 0), 0)
        out["sessions"] = {
            **sess,
            "returning_rate": (sess.get("returning", 0) / sess_total) if sess_total else 0.0,
            "pages_per_visit": (human_total / sess_total) if sess_total else 0.0,
            "bounce_sessions": bounce_sessions,
            "bounce_rate": (bounce_sessions / sess_total) if sess_total else 0.0,
        }
        prev_sess = _session_counts_from_rows(prev_by_day["session"])
        out["prev_totals"]["sessions"] = prev_sess.get("total", 0)
        out["prev_totals"]["returning"] = prev_sess.get("returning", 0)

        # Article metadata (title/published_at/tags) for every distinct article
        # path referenced anywhere below, fetched once and shared by sections,
        # the editorial scorecard and path-label resolution.
        article_cards = _fetch_article_cards(_distinct_article_ids(by_day))

        out["top_paths"] = _paths_from_rows(by_day["path_kind"], limit=top)
        out["top_referrers"] = _rank(by_day["referrer"], "referrer", limit=top)

        # Breakdown of the '(direct)' bucket: UA-class counts + a recent raw
        # sample. The sample read stays sequential/early-stopping (it wants only
        # the newest `top` rows, not a full-window scan).
        out["direct_uaclass"] = _rank(by_day["direct_uaclass"], "ua_class", limit=top)
        try:
            out["direct_samples"] = _recent_direct_samples(session, window, limit=top)
        except Exception as exc:
            log.warning("direct samples read skipped: %s", exc)

        out["top_searches"] = _rank(by_day["search"], "query", "searches", limit=top)
        out["zero_searches"] = _rank(by_day["search_zero"], "query", "searches", limit=top)
        out["top_bots"] = _rank(by_day["bot"], "bot", limit=top)
        out["referrer_paths"] = _referrer_paths_from_rows(by_day["referrer_path"], limit=top)
        out["top_notfound"] = _rank(by_day["notfound"], "path", limit=top)

        out["device"] = _rank(by_day["device"], "device", limit=top)
        out["browser"] = _rank(by_day["browser"], "browser", limit=top)
        out["hours"] = _hours_from_rows(by_day["hour"])
        out["referrer_categories"] = _referrer_categories_from_rows(by_day["referrer"])
        out["sections"] = _sections_from_rows(by_day["path_kind"], article_cards, limit=top)
        out["top_referrer_urls"] = _rank(by_day["referrer_url"], "referrer_url", limit=top)
        out["sessions_daily"] = _session_daily_from_rows(by_day["session"], window)
        out["ai_crawler"] = _ai_crawler_from_rows(by_day["bot"], window)
        out["articles"] = _editorial_scorecard_from_rows(
            by_day["path_kind"], article_cards, window, limit=top
        )
        out["geo"] = _rank(by_day["geo"], "country", limit=top)
        out["campaigns"] = _rank(by_day["campaign"], "campaign", limit=top)
        cur404 = _sum_views(by_day["notfound"])
        prev404 = _sum_views(prev_by_day["notfound"])
        out["alerts"] = _build_alerts(out, cur404, prev404)

        # Attach human-readable labels (article titles, friendly route names) to
        # every list that carries a path.
        path_lists = [out["top_paths"], out["referrer_paths"], out["top_notfound"]]
        all_paths = [r["path"] for lst in path_lists for r in lst if r.get("path")]
        labels = _resolve_labels(all_paths, article_cards)
        for lst in path_lists:
            for r in lst:
                if r.get("path"):
                    r["label"] = labels.get(r["path"], r["path"])
    except Exception as exc:
        log.warning("read_analytics failed: %s", exc)
        out["error"] = "analytics_unavailable"
    return out
