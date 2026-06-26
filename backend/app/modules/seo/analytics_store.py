"""First-party pageview analytics over Cassandra counters + Redis HLL.

Recorded server-side from the SSR document routes (and the search API) — no
client JS, and it stays within the Cassandra/Typesense/Redis stack. Pageview,
referrer, bot, search and 404 tallies are Cassandra counters; unique-visitor
counts are privacy-safe Redis HyperLogLogs (no raw IP stored). Writes are
fire-and-forget so they never add latency; reads are admin-only and infrequent.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
from datetime import UTC, datetime
from functools import lru_cache
from urllib.parse import urlparse

from app.core.config import settings

log = logging.getLogger(__name__)

_BOT_TOKENS = (
    "bot", "crawl", "spider", "slurp", "bingpreview", "facebookexternalhit",
    "embedly", "quora link preview", "outbrain", "pinterest", "vkshare",
    "w3c_validator", "headless", "python-requests", "httpx", "curl", "wget",
    "go-http-client", "ahrefs", "semrush", "gptbot", "ccbot", "claudebot",
    "perplexitybot", "google-extended", "applebot", "yandex", "duckduckbot",
    # Uptime monitors, link unfurlers and SEO/scraping tools — non-human traffic
    # that lacks the tokens above and would otherwise count as "human".
    "uptimerobot", "pingdom", "statuscake", "site24x7", "betteruptime",
    "datadog", "newrelicpinger", "prometheus", "kube-probe", "lighthouse",
    "gtmetrix", "petalbot", "bytespider", "dataforseo", "mj12bot", "dotbot",
    "screaming frog", "telegrambot", "whatsapp", "discordbot", "slackbot",
    "twitterbot", "linkedinbot", "skypeuripreview", "google favicon",
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
        return "Section · " + path[len(_SECTION_PREFIX):]
    return path


def _resolve_labels(session, paths: list[str]) -> dict[str, str]:
    """Map each path to a human-readable label, resolving article titles by id."""
    from uuid import UUID

    labels = {p: _static_label(p) for p in paths}
    for p in paths:
        if not p.startswith(_ARTICLE_PREFIX):
            continue
        try:
            aid = UUID(p[len(_ARTICLE_PREFIX):])
        except ValueError:
            labels[p] = "Article"
            continue
        try:
            row = session.execute(
                "SELECT title FROM articles_by_id WHERE article_id = %s", (aid,)
            ).one()
        except Exception:  # missing row / table — fall back to a generic label
            row = None
        labels[p] = (row.title if row and row.title else None) or "Article"
    return labels


# Automation/headless signatures. Some (e.g. "headless") also appear in the bot
# denylist; kept here too so ua_class can label a UA even outside is_bot.
_HEADLESS_TOKENS = (
    "headless", "phantomjs", "electron", "puppeteer", "playwright",
    "selenium", "webdriver", "cypress", "splash",
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
    if "mozilla/" not in ua:
        return True
    return False


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
    ("Social unfurler", (
        "facebookexternalhit", "twitterbot", "linkedinbot", "slackbot",
        "discordbot", "telegrambot", "whatsapp", "skypeuripreview", "embedly",
        "pinterest", "vkshare", "quora link preview",
    )),
    ("Uptime monitor", (
        "uptimerobot", "pingdom", "statuscake", "site24x7", "betteruptime",
        "datadog", "newrelicpinger", "prometheus", "kube-probe",
    )),
    ("SEO/audit tool", (
        "petalbot", "dataforseo", "mj12bot", "dotbot", "screaming frog",
        "lighthouse", "gtmetrix", "w3c_validator",
    )),
    ("Generic HTTP client", (
        "python-requests", "httpx", "curl", "wget", "go-http-client",
    )),
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


def _ignored_hosts() -> set[str]:
    """Hosts/IPs that count as our own (the server IP, configured office IPs)."""
    return {
        h.strip().lower()
        for h in settings.analytics_ignore_ips.split(",")
        if h.strip()
    }


def _own_host() -> str:
    """The public site's bare host (no scheme/www/port), for self-referral checks."""
    raw = settings.public_site_url
    host = (urlparse(raw).netloc or raw).lower().replace("www.", "")
    return host.split(":")[0]


def _is_self_referral(host: str) -> bool:
    """True when `host` is our own site or the hosting server's IP — an in-site
    navigation, not a real external referral."""
    if host in _ignored_hosts():
        return True
    own = _own_host()
    return bool(own) and (host == own or host.endswith("." + own))


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
    return host[:120]


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
    from app.core.cassandra import prepare_cached

    uac = ua_class(user_agent)
    session.execute_async(
        prepare_cached(
            "UPDATE pageview_direct_uaclass_daily SET views = views + 1 "
            "WHERE day = ? AND ua_class = ?"
        ),
        (day, uac),
    )
    session.execute_async(
        prepare_cached(
            "INSERT INTO pageview_direct_sample "
            "(day, ts, path, referer, user_agent, ua_class) "
            "VALUES (?, now(), ?, ?, ?, ?)"
        ),
        (day, path[:200], (referer or "")[:300], (user_agent or "")[:300], uac),
    )


def record_pageview(
    *, path: str, referer: str | None, user_agent: str | None, client_ip: str | None = None
) -> None:
    """Best-effort counter bumps for one document request. Never raises."""
    if is_internal_client(client_ip):
        return  # don't count the server itself / internal probes
    try:
        from app.core.cassandra import get_cassandra_session, prepare_cached

        session = get_cassandra_session()
        day = _today()
        kind = "bot" if is_bot(user_agent) else "human"
        # Privacy-safe unique visitor count (Redis HLL), independent of Cassandra.
        record_unique(kind, client_ip, user_agent, day)
        session.execute_async(
            prepare_cached(
                "UPDATE pageview_daily SET views = views + 1 WHERE kind = ? AND day = ?"
            ),
            (kind, day),
        )
        # Per-path counter, split by kind so Top Pages can show human vs bot.
        session.execute_async(
            prepare_cached(
                "UPDATE pageview_path_kind_daily SET views = views + 1 "
                "WHERE day = ? AND path = ? AND kind = ?"
            ),
            (day, path[:200], kind),
        )
        if kind == "bot":
            # Which crawler — so the bot column identifies Googlebot/GPTBot/etc.
            session.execute_async(
                prepare_cached(
                    "UPDATE pageview_bot_daily SET views = views + 1 "
                    "WHERE day = ? AND bot = ?"
                ),
                (day, bot_name(user_agent)),
            )
        else:
            referrer = referrer_host(referer)
            session.execute_async(
                prepare_cached(
                    "UPDATE pageview_referrer_daily SET views = views + 1 "
                    "WHERE day = ? AND referrer = ?"
                ),
                (day, referrer),
            )
            # Source -> landing-page attribution (which referrer drove which page).
            session.execute_async(
                prepare_cached(
                    "UPDATE pageview_referrer_path_daily SET views = views + 1 "
                    "WHERE day = ? AND referrer = ? AND path = ?"
                ),
                (day, referrer, path[:200]),
            )
            if referrer == "(direct)":
                _record_direct(session, day, path, referer, user_agent)
    except Exception as exc:  # missing tables / cassandra down — analytics is non-critical
        log.debug("pageview record skipped: %s", exc)


def record_search(query: str, result_count: int, *, user_agent: str | None = None) -> None:
    """Best-effort: count a search term for the day (and separately when it
    returned nothing). Bots are skipped so the demand signal stays human."""
    q = (query or "").strip().lower()
    if not q or is_bot(user_agent):
        return
    try:
        from app.core.cassandra import get_cassandra_session, prepare_cached

        session = get_cassandra_session()
        day = _today()
        q = q[:120]
        session.execute_async(
            prepare_cached(
                "UPDATE search_query_daily SET searches = searches + 1 "
                "WHERE day = ? AND query = ?"
            ),
            (day, q),
        )
        if result_count <= 0:
            session.execute_async(
                prepare_cached(
                    "UPDATE search_zero_daily SET searches = searches + 1 "
                    "WHERE day = ? AND query = ?"
                ),
                (day, q),
            )
    except Exception as exc:
        log.debug("search record skipped: %s", exc)


def record_notfound(*, path: str, client_ip: str | None = None) -> None:
    """Best-effort: count a request to an unknown article/section URL — broken
    inbound links and crawler waste. Internal/self traffic is excluded."""
    if is_internal_client(client_ip):
        return
    try:
        from app.core.cassandra import get_cassandra_session, prepare_cached

        session = get_cassandra_session()
        session.execute_async(
            prepare_cached(
                "UPDATE pageview_notfound_daily SET views = views + 1 "
                "WHERE day = ? AND path = ?"
            ),
            (_today(), path[:200]),
        )
    except Exception as exc:
        log.debug("notfound record skipped: %s", exc)


def _recent_direct_samples(session, window: set[str], limit: int) -> list[dict]:
    """Latest raw '(direct)' samples across the window, newest day first.

    The table clusters newest-first, so a per-day LIMIT yields the most recent
    rows without an ALLOW FILTERING scan; we stop once `limit` are collected."""
    samples: list[dict] = []
    for day in sorted(window, reverse=True):
        if len(samples) >= limit:
            break
        rows = session.execute(
            "SELECT path, referer, user_agent, ua_class FROM pageview_direct_sample "
            "WHERE day = %s LIMIT %s",
            (day, limit - len(samples)),
        )
        for r in rows:
            samples.append({
                "day": day,
                "path": r.path,
                "referer": r.referer or "",
                "user_agent": r.user_agent or "",
                "ua_class": r.ua_class,
            })
    return samples


def read_analytics(days: int = 14, *, top: int = 20) -> dict:
    """Daily human/bot series + aggregated top paths and referrers over `days`."""
    out: dict = {
        "days": days, "daily": [], "top_paths": [], "top_referrers": [],
        "totals": {}, "prev_totals": {},
        "direct_uaclass": [], "direct_samples": [],
        "top_searches": [], "zero_searches": [], "top_bots": [],
        "referrer_paths": [], "top_notfound": [],
    }
    try:
        from app.core.cassandra import get_cassandra_session

        session = get_cassandra_session()
        window = set(_recent_days(days))
        prev_window = set(_recent_days(days, offset=days))

        # Per-kind partition is one row/day — read it whole once, then slice both
        # the current and prior window in-app (avoids the driver's `IN` quirk).
        def _series(kind: str) -> dict[str, int]:
            rows = session.execute(
                "SELECT day, views FROM pageview_daily WHERE kind = %s", (kind,)
            )
            return {r.day: int(r.views) for r in rows}

        human, bot = _series("human"), _series("bot")
        # Unique visitors (Redis HLL): per-day human uniques for the chart, plus
        # period-wide human/bot uniques (PFCOUNT unions the daily keys).
        uv_human_day, uv_human = _unique_counts("human", sorted(window))
        _, uv_bot = _unique_counts("bot", sorted(window))
        _, uv_human_prev = _unique_counts("human", sorted(prev_window))
        out["daily"] = [
            {"day": d, "human": human.get(d, 0), "bot": bot.get(d, 0),
             "human_unique": uv_human_day.get(d, 0)}
            for d in sorted(window)
        ]
        out["totals"] = {
            "human": sum(human.get(d, 0) for d in window),
            "bot": sum(bot.get(d, 0) for d in window),
            "human_unique": uv_human, "bot_unique": uv_bot,
        }
        out["prev_totals"] = {
            "human": sum(human.get(d, 0) for d in prev_window),
            "bot": sum(bot.get(d, 0) for d in prev_window),
            "human_unique": uv_human_prev,
        }

        # Each day is its own partition — read the N day-partitions and aggregate.
        def _aggregate(table: str, key: str, value_col: str = "views") -> list[dict]:
            agg: dict[str, int] = {}
            for day in window:
                rows = session.execute(
                    f"SELECT {key}, {value_col} FROM {table} WHERE day = %s", (day,)
                )
                for r in rows:
                    k = getattr(r, key)
                    agg[k] = agg.get(k, 0) + int(getattr(r, value_col))
            ranked = sorted(agg.items(), key=lambda kv: kv[1], reverse=True)[:top]
            return [{key: k, "views": v} for k, v in ranked]

        def _safe_aggregate(table: str, key: str, value_col: str = "views") -> list[dict]:
            """`_aggregate` but tolerant of a not-yet-migrated table (new in 039)."""
            try:
                return _aggregate(table, key, value_col)
            except Exception as exc:
                log.warning("%s aggregate skipped: %s", table, exc)
                return []

        def _aggregate_referrer_paths() -> list[dict]:
            """Top (referrer, landing-path) pairs over the window."""
            agg: dict[tuple[str, str], int] = {}
            try:
                for day in window:
                    rows = session.execute(
                        "SELECT referrer, path, views FROM pageview_referrer_path_daily "
                        "WHERE day = %s",
                        (day,),
                    )
                    for r in rows:
                        agg[(r.referrer, r.path)] = agg.get((r.referrer, r.path), 0) + int(r.views)
            except Exception as exc:
                log.warning("referrer-path aggregate skipped: %s", exc)
                return []
            ranked = sorted(agg.items(), key=lambda kv: kv[1], reverse=True)[:top]
            return [{"referrer": k[0], "path": k[1], "views": v} for k, v in ranked]

        # Top pages carry a human/bot split (read from the kind-partitioned table).
        def _aggregate_paths() -> list[dict]:
            agg: dict[str, dict[str, int]] = {}
            try:
                for day in window:
                    rows = session.execute(
                        "SELECT path, kind, views FROM pageview_path_kind_daily WHERE day = %s",
                        (day,),
                    )
                    for r in rows:
                        bucket = agg.setdefault(r.path, {"human": 0, "bot": 0})
                        bucket[r.kind] = bucket.get(r.kind, 0) + int(r.views)
            except Exception as exc:  # table not migrated yet — keep the rest of the page
                log.warning("top-pages aggregate skipped: %s", exc)
                return []
            ranked = sorted(
                agg.items(), key=lambda kv: kv[1]["human"] + kv[1]["bot"], reverse=True
            )[:top]
            return [
                {"path": p, "human": v["human"], "bot": v["bot"],
                 "views": v["human"] + v["bot"]}
                for p, v in ranked
            ]

        out["top_paths"] = _aggregate_paths()
        out["top_referrers"] = _aggregate("pageview_referrer_daily", "referrer")

        # Breakdown of the '(direct)' bucket: UA-class counts + a recent raw
        # sample. Both tables are new (migration 038) — tolerate their absence so
        # an un-migrated env still renders the rest of the page.
        try:
            out["direct_uaclass"] = _aggregate(
                "pageview_direct_uaclass_daily", "ua_class"
            )
        except Exception as exc:
            log.warning("direct ua-class aggregate skipped: %s", exc)
        try:
            out["direct_samples"] = _recent_direct_samples(session, window, limit=top)
        except Exception as exc:
            log.warning("direct samples read skipped: %s", exc)

        # Analytics expansion (migration 039): search demand, bot identities,
        # source->landing attribution, dead links. Each tolerates an absent table.
        out["top_searches"] = _safe_aggregate("search_query_daily", "query", "searches")
        out["zero_searches"] = _safe_aggregate("search_zero_daily", "query", "searches")
        out["top_bots"] = _safe_aggregate("pageview_bot_daily", "bot")
        out["referrer_paths"] = _aggregate_referrer_paths()
        out["top_notfound"] = _safe_aggregate("pageview_notfound_daily", "path")

        # Attach human-readable labels (article titles, friendly route names) to
        # every list that carries a path.
        path_lists = [out["top_paths"], out["referrer_paths"], out["top_notfound"]]
        all_paths = [r["path"] for lst in path_lists for r in lst if r.get("path")]
        labels = _resolve_labels(session, all_paths)
        for lst in path_lists:
            for r in lst:
                if r.get("path"):
                    r["label"] = labels.get(r["path"], r["path"])
    except Exception as exc:
        log.warning("read_analytics failed: %s", exc)
        out["error"] = "analytics_unavailable"
    return out
