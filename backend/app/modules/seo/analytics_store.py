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
import re
from datetime import UTC, datetime
from functools import lru_cache
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from app.core.config import settings

if TYPE_CHECKING:
    import geoip2.database
    import redis
    from cassandra.cluster import Session as CassandraSession

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
    "ipscanner",
    "infrawatch",
    "cyberconvoyscout",
    "netcraftsurveyagent",
    # A real Google-operated crawler (distinct product from Googlebot) that
    # sends a generic "Mozilla/... Chrome/..." UA with just a self-identifying
    # "(compatible; GoogleOther)" suffix — the fallback below only catches UAs
    # that DON'T look like Mozilla, so this needs its own token (found leaking
    # into "human" direct traffic 2026-07-12).
    "googleother",
)

# Real browsers never embed a URL in their own UA string. Polite crawlers do,
# by convention — "(compatible; Name/ver; +https://...)" or a bare trailing
# "+https://..." — so this is the general case of the specific bots above
# (FlipboardProxy, GoogleOther, etc.) and catches new ones without needing a
# name added here first. Found leaking into "human" direct traffic 2026-07-13:
# SkyWatch (a Bluesky automod bot), SvelteKit-FYI and NuxtFyi (SEO scrapers).
_SELF_ID_URL_RE = re.compile(r"\+https?://")

# Exact-match, not a substring token: this specific frozen iOS-13.2.3/Safari-
# 13.0.3 string is a well-known stock default UA bundled in several scraping
# libraries. Confirmed 2026-07-12 (see _UA_FREQ_THRESHOLD comment below) as
# the single biggest offender in pageview_direct_sample — 471 rows over 7
# days, peak 172/day — landing on many distinct article paths with no
# referrer each time. It stays under the frequency threshold on quieter days,
# so is_repeated_ua() alone won't always catch it; denylist it outright.
_KNOWN_DECOY_UAS = frozenset(
    {
        "mozilla/5.0 (iphone; cpu iphone os 13_2_3 like mac os x) applewebkit/605.1.15 "
        "(khtml, like gecko) version/13.0.3 mobile/15e148 safari/604.1",
    }
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
    """Coarse content bucket for a path, for the section-level rollup. Article paths are bucketed generically here; the read layer upgrades them to the article's primary tag ('Section · DeFi') when it can resolve one."""
    if path in _STATIC_PATH_LABELS:
        return _STATIC_PATH_LABELS[path]
    if path.startswith(_SECTION_PREFIX):
        return "Section · " + path[len(_SECTION_PREFIX) :]
    if path.startswith(_ARTICLE_PREFIX):
        return "Article"
    return "Other"


def _resolve_labels(paths: list[str], article_cards: dict[str, object]) -> dict[str, str]:
    """Map each path to a human-readable label, from the already-fetched article metadata batch (see `_fetch_article_cards`) rather than a fresh DB lookup."""
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
    """Heuristically classify a user-agent string as a bot/scraper rather than a human browser."""
    ua = (user_agent or "").lower()
    if not ua:
        return True  # no UA -> almost always a script/scanner
    if ua in _KNOWN_DECOY_UAS:
        return True
    if any(tok in ua for tok in _BOT_TOKENS):
        return True
    if _SELF_ID_URL_RE.search(ua):
        return True
    # Every mainstream browser sends a "Mozilla/..." product token. A non-empty
    # UA without it is a library/scraper that just isn't in the denylist above —
    # the main thing that was inflating "human (direct)".
    return "mozilla/" not in ua


# ── Repeated-UA anomaly detection ────────────────────────────────────────────
# is_bot() only catches bots that self-identify (a name token, or no "mozilla/"
# at all). It cannot catch a scraper that hardcodes one ordinary-looking browser
# UA and reuses it on every request — found 2026-07-12 in a full 7-day pull of
# pageview_direct_sample (3713 rows / 402 distinct UAs): one byte-identical
# legacy-iOS Safari string alone was 471 rows (peak 172/day), on top of the
# named bots is_bot() already denylists. Real diverse human traffic doesn't
# repeat one exact UA like that; a scraper replaying a fixed string does.
# Counted per exact UA (hashed, nothing stored raw) per day in Redis — fails
# open on any Redis error, same as the rest of this module's Redis usage.
_UA_FREQ_PREFIX = "algorand:uafreq:"
_UA_FREQ_TTL_SECONDS = 36 * 60 * 60  # a day plus buffer past midnight rollover
# Evidence-based, not a guess: a full 7-day pull of pageview_direct_sample
# (2026-07-12, 3713 rows / 402 distinct UAs) showed a clean gap between
# genuinely organic traffic (tops out at 12/day for the most repeated
# ordinary modern browser signature) and actual slow-repeat scrapers (two
# obsolete-version UAs — a 2016-era Firefox/47.0 and a Chrome/108-on-macOS-13
# string — steady at 19/day for a week straight). 15 sits in that gap.
# Revisit upward if real traffic grows enough to organically cross it.
_UA_FREQ_THRESHOLD = 15


def _ua_freq_key(user_agent: str, day: str) -> str:
    digest = hashlib.sha256(user_agent.encode()).hexdigest()[:16]
    return f"{_UA_FREQ_PREFIX}{day}:{digest}"


def _ua_repeat_count(user_agent: str | None, day: str) -> int | None:
    """Increments and returns today's exact-match count for this UA, or None on an empty UA / Redis hiccup (fails open — never blocks page serving). Shared by is_repeated_ua and record_pageview's retroactive-purge trigger so both read the SAME increment rather than double-counting."""
    ua = (user_agent or "").strip()
    if not ua:
        return None  # is_bot() already treats an empty UA as a bot
    try:
        r = _uv_redis()
        key = _ua_freq_key(ua, day)
        count = r.incr(key)
        if count == 1:
            r.expire(key, _UA_FREQ_TTL_SECONDS)
        return count
    except Exception as exc:  # Redis down — fail open, never block a real view
        log.debug("ua frequency check skipped: %s", exc)
        return None


def is_repeated_ua(user_agent: str | None, day: str | None = None) -> bool:
    """True once this exact UA string has been seen more than the threshold number of times today. Complements is_bot() rather than replacing it — call both."""
    count = _ua_repeat_count(user_agent, day or _today())
    return count is not None and count > _UA_FREQ_THRESHOLD


# ── UA structural plausibility ───────────────────────────────────────────────
# is_bot() only catches self-identifying bots; is_repeated_ua() only catches
# one fixed string reused past a daily threshold — a bot that fakes a fresh
# random UA every request slips both. Real browser UAs are generated by the
# vendor's own fixed code, not user-editable text, so certain tokens are
# permanently frozen or must self-consistently match — a violation is a
# same-request, zero-volume-needed tell a hand-rolled/badly-randomized UA
# generator gets wrong. Every rule below is pinned to a specific, stable,
# long-documented vendor convention (not a guess) so this doesn't rot as real
# browsers update:
#   - "(KHTML, like Gecko)" is one fixed phrase in every WebKit/Blink UA
#     (Chrome, Safari, Edge, Opera, Brave…) — found a real "live Gecko" typo
#     in prod sample data 2026-07-12.
#   - Chrome/Chromium has kept AppleWebKit AND the trailing Safari/ token
#     frozen at exactly "537.36" since ~2013 specifically so UA-sniffing
#     code doesn't break — real Chrome (and Chromium-based browsers) never
#     varies this, regardless of actual Chrome version.
#   - Firefox's `rv:` token and its trailing `Firefox/` version are always
#     identical by construction — the browser reads one value into both.
#   - Desktop Firefox (not Android) has kept its Gecko/ token frozen at the
#     literal placeholder "20100101" since ~2012, same rationale as Chrome's
#     537.36 freeze; Firefox for Android does NOT freeze this, so the check
#     is scoped to desktop only.
# Deliberately NOT checking Safari's own Version/·Safari/ token relationship
# (unlike Chrome, real Safari's history there is less rigidly consistent —
# not confident enough to avoid false positives on genuine older Safari).
_KHTML_TOKEN_RE = re.compile(r"KHTML")
_LIKE_GECKO_RE = re.compile(r"KHTML,\s*like Gecko")
_CHROME_TOKEN_RE = re.compile(r"Chrome/")
_APPLEWEBKIT_VERSION_RE = re.compile(r"AppleWebKit/([\d.]+)")
_TRAILING_SAFARI_VERSION_RE = re.compile(r"Safari/([\d.]+)")
_FIREFOX_VERSION_RE = re.compile(r"Firefox/([\d.]+)")
_GECKO_RV_RE = re.compile(r"rv:([\d.]+)")
_GECKO_VERSION_RE = re.compile(r"Gecko/(\S+)")
_CHROME_FROZEN_WEBKIT = "537.36"
_FIREFOX_DESKTOP_FROZEN_GECKO = "20100101"

# ── Chronologically-impossible platform/version combos ──────────────────────
# The structural checks above catch internally-inconsistent UAs. These catch
# ones that are perfectly self-consistent but describe a device or version
# that cannot exist in current traffic — the exact gap a UA-rotation scraper
# used to hide inside human "(direct)" (found 2026-07-20: a full week of
# pageview_direct_sample had PPC-Mac "Safari" strings and pre-2016 Chrome/
# Firefox versions on every sampled day — internally well-formed, but never
# repeating enough in one day to trip is_repeated_ua()).
#   - PowerPC Macs were discontinued in 2006; nothing running a PPC Mac OS X
#     stack is browsing the web today. Permanent, not evidence-based.
#   - Chrome dropped Windows XP/Server 2003 (NT 5.1/5.2) support at version 49
#     (Feb 2016) and never shipped a later build for it — permanent, not
#     evidence-based.
#   - Chrome and Firefox are both evergreen, auto-updating on a ~4-week
#     release cadence; version 100 shipped in Chrome/Firefox in March/May
#     2022, so a genuine install four-plus years out of date doesn't happen
#     at any real volume. Evidence-based (like _UA_FREQ_THRESHOLD above, not
#     a permanent vendor fact) — revisit upward as real major versions climb.
_PPC_MAC_RE = re.compile(r"PPC Mac OS X")
_WINDOWS_LEGACY_NT_RE = re.compile(r"Windows NT 5\.[12]")
_CHROME_MAJOR_RE = re.compile(r"Chrome/(\d+)")
_FIREFOX_MAJOR_RE = re.compile(r"Firefox/(\d+)")
_CHROME_MAX_MAJOR_ON_WINDOWS_XP = 49
_CHROME_STALE_MAJOR_FLOOR = 100
_FIREFOX_STALE_MAJOR_FLOOR = 100


def is_malformed_ua(user_agent: str | None) -> bool:
    """True when a UA claims to be a specific mainstream browser but violates that vendor's own fixed/frozen string conventions — something a real install of that browser cannot produce. Catches fakes on the first request, unlike is_repeated_ua(). Fails open: an unrecognized (but internally consistent) UA shape is never flagged, only a proven contradiction."""
    ua = (user_agent or "").strip()
    if not ua:
        return False

    if _KHTML_TOKEN_RE.search(ua) and not _LIKE_GECKO_RE.search(ua):
        return True

    if _PPC_MAC_RE.search(ua):
        return True

    if _CHROME_TOKEN_RE.search(ua):
        webkit_match = _APPLEWEBKIT_VERSION_RE.search(ua)
        safari_match = _TRAILING_SAFARI_VERSION_RE.search(ua)
        if webkit_match and webkit_match.group(1) != _CHROME_FROZEN_WEBKIT:
            return True
        if safari_match and safari_match.group(1) != _CHROME_FROZEN_WEBKIT:
            return True

    chrome_major_match = _CHROME_MAJOR_RE.search(ua)
    if chrome_major_match:
        chrome_major = int(chrome_major_match.group(1))
        if _WINDOWS_LEGACY_NT_RE.search(ua) and chrome_major > _CHROME_MAX_MAJOR_ON_WINDOWS_XP:
            return True
        if chrome_major < _CHROME_STALE_MAJOR_FLOOR:
            return True

    firefox_match = _FIREFOX_VERSION_RE.search(ua)
    if firefox_match:
        rv_match = _GECKO_RV_RE.search(ua)
        if rv_match and rv_match.group(1) != firefox_match.group(1):
            return True
        is_mobile_firefox = "Android" in ua or "Mobile" in ua
        gecko_match = _GECKO_VERSION_RE.search(ua)
        if (
            not is_mobile_firefox
            and gecko_match
            and gecko_match.group(1) != _FIREFOX_DESKTOP_FROZEN_GECKO
        ):
            return True

    firefox_major_match = _FIREFOX_MAJOR_RE.search(ua)
    return bool(
        firefox_major_match and int(firefox_major_match.group(1)) < _FIREFOX_STALE_MAJOR_FLOOR
    )


# ── Fetch Metadata (Sec-Fetch-*) presence ────────────────────────────────────
# Every evergreen browser auto-sends Sec-Fetch-Mode on every request (document
# navigation AND same-origin fetch/XHR, e.g. the beacon POST) since Chrome 76
# (2019) / Firefox 90 (2021) — no mainstream HTTP client or scraping library
# (requests, httpx, curl, most headless scripts) sets it, and it can't be
# faked by copying a real UA string. Scoped to Chrome/Firefox tokens only:
# Safari didn't support Fetch Metadata until 16.4 (Mar 2023) and the
# AppleWebKit/Safari version numbers in a UA don't map cleanly to the actual
# Safari release, so a Safari (or iOS Chrome/"CriOS", which is WebKit under
# the hood and follows Safari's timeline, not "Chrome/") UA is never flagged
# here — same conservative carve-out is_malformed_ua takes on Safari above.
# (_CHROME_MAJOR_RE / _FIREFOX_MAJOR_RE are defined above, next to is_malformed_ua.)
_FETCH_METADATA_MIN_CHROME = 76
_FETCH_METADATA_MIN_FIREFOX = 90


def is_missing_fetch_metadata(user_agent: str | None, sec_fetch_mode: str | None) -> bool:
    """True when a UA that must send Sec-Fetch-Mode (a modern Chrome/Firefox) didn't. Checked once per request; the exact header value doesn't matter, only whether the browser bothered to set it at all."""
    if sec_fetch_mode:
        return False
    ua = user_agent or ""
    chrome_match = _CHROME_MAJOR_RE.search(ua)
    if chrome_match and int(chrome_match.group(1)) >= _FETCH_METADATA_MIN_CHROME:
        return True
    firefox_match = _FIREFOX_MAJOR_RE.search(ua)
    return bool(firefox_match and int(firefox_match.group(1)) >= _FETCH_METADATA_MIN_FIREFOX)


# ── Accept header presence/shape ─────────────────────────────────────────────
# A real Chrome/Firefox navigation request always sends a versioned, comma-
# separated Accept header (content types + quality values, e.g.
# "text/html,application/xhtml+xml,...,*/*;q=0.8") generated by the browser
# itself — never user-settable. The exact string drifts across versions (a
# new image format added, order tweaks), so this deliberately does NOT pattern-
# match the full value — only the two things a scripting library actually
# gets wrong: sending nothing at all, or sending the bare "*/*" that is the
# unconfigured default of python-requests/httpx (and what curl/wget send when
# -H Accept isn't set). Scoped to Chrome/Firefox tokens only, same conservative
# carve-out as is_missing_fetch_metadata (Safari's Accept conventions are less
# rigidly documented).
_BARE_WILDCARD_ACCEPT = "*/*"


def is_missing_accept_header(user_agent: str | None, accept: str | None) -> bool:
    """True when a UA claiming to be Chrome/Firefox sent no Accept header, or the bare "*/*" default an HTTP client library sends when the caller never set one — something a real browser navigation request cannot produce."""
    ua = user_agent or ""
    if not (_CHROME_TOKEN_RE.search(ua) or _FIREFOX_VERSION_RE.search(ua)):
        return False
    value = (accept or "").strip()
    return not value or value == _BARE_WILDCARD_ACCEPT


# ── Reader language (Accept-Language) ────────────────────────────────────────
_ACCEPT_LANG_TAG_RE = re.compile(r"^[a-zA-Z]{2,3}")


def primary_language(accept_language: str | None) -> str | None:
    """Best-effort primary language subtag from an Accept-Language header (e.g. "en-US,en;q=0.9,fa;q=0.8" -> "en"). None for missing/unparseable — never guess, an absent value is just left out of the breakdown."""
    header = (accept_language or "").strip()
    if not header:
        return None
    first = header.split(",", 1)[0].split(";", 1)[0].strip()
    match = _ACCEPT_LANG_TAG_RE.match(first)
    return match.group(0).lower() if match else None


def ua_class(user_agent: str | None) -> str:
    """Coarse class for a UA, used to break down the '(direct)' bucket so plain browser traffic (dark social, bookmarks) is distinguishable from scripts."""
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
    """Coarse browser family for a human UA (Chrome/Safari/Firefox/Edge…), or 'Other' when nothing matches."""
    ua = (user_agent or "").lower()
    if not ua:
        return "Other"
    for name, tokens in _BROWSER_FAMILIES:
        if any(t in ua for t in tokens):
            return name
    return "Other"


# ── Unique visitors (Redis HyperLogLog) ──────────────────────────────────────
# A privacy-safe visitor token = sha256(salt | ip | ua), truncated. No raw IP is
# ever stored; the salt makes the hash non-reversible. Daily HLL keys per kind;
# period uniques come from PFCOUNT over several daily keys (it unions without
# materializing). Everything fails open — Redis hiccups never block page serving.
_UV_PREFIX = "algorand:uv:"
_UV_TTL_SECONDS = 95 * 24 * 60 * 60  # a touch past the 90-day max read window


@lru_cache(maxsize=1)
def _uv_redis() -> redis.Redis:
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


def record_session(
    session: CassandraSession, client_ip: str | None, user_agent: str | None, day: str
) -> None:
    """Best-effort: stitch this human pageview into a session and, on a new session, bump session_daily split by new/returning. Also confirms a session as multi-page the moment its 2nd hit lands (vtype="multipage", counted separately from new/returning — see `_session_counts_from_rows`). A UA denylist alone can't catch a scraper spoofing a browser UA; a session that never gets a 2nd hit is a cheap, record-time-free bot-likelihood signal for the breakdowns instead. Fails open."""
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
    """New/returning/total session counts summed across already-fetched `session_daily` rows. An empty/missing input (un-migrated table) yields zeros. `multipage` (sessions confirmed to have a 2nd hit) is tracked separately and excluded from `total` — it's a subset of new+returning, not an additional session."""
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
def _geoip_reader() -> geoip2.database.Reader | None:
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
    """ISO-3166-1 alpha-2 country for a client IP, or '' when unknown.

    Fail-open: no DB, an unparseable/private IP, or any lookup error yields
    ''. The IP is never stored — only the resolved country is counted.
    """
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


# ── Hosting/datacenter ASN (local GeoIP-ASN database, no IP stored) ─────────
@lru_cache(maxsize=1)
def _geoip_asn_reader() -> geoip2.database.Reader | None:
    """Lazy DB-IP ASN reader, or None when unavailable (lib or db missing)."""
    path = settings.geoip_asn_db_path
    if not path:
        return None
    try:
        import geoip2.database

        return geoip2.database.Reader(path)
    except Exception as exc:  # missing lib / db file — hosting check stays off
        log.debug("geoip asn reader unavailable: %s", exc)
        return None


# Substrings of the ASN organization name (lowercased) for providers that sell
# generic cloud/VPS compute, not residential/mobile broadband — found 2026-07-20
# after a UA-rotation scraper (fake PPC-Mac/stale-Chrome strings, see
# is_malformed_ua above) was still slipping through as human "(direct)" despite
# passing every UA-shape check. Deliberately narrow and conservative: providers
# left OUT on purpose because their ASN also carries real residential/consumer
# traffic and would false-positive genuine readers —
#   - Cloudflare: WARP VPN + plain proxying puts real visitors behind its ASN.
#   - Microsoft: AS8075 covers Azure AND Xbox Live/consumer 365 traffic, not
#     cleanly separable by org name alone.
# Commercial-VPN users on one of the providers below (e.g. a residential reader
# tunnelling through a DigitalOcean-hosted VPN) will also be caught — an
# accepted false-positive for a heuristic this strong; small in volume next to
# what it catches. Spot-check real prod ASN org strings once the db is
# provisioned and tune this list from that, same as _UA_FREQ_THRESHOLD above.
_HOSTING_ASN_ORG_TOKENS = (
    "amazon",
    "google cloud",
    "digitalocean",
    "digital ocean",
    "hetzner",
    "ovh",
    "linode",
    "akamai connected cloud",
    "choopa",  # Vultr's underlying ASN org name
    "vultr",
    "alibaba",
    "tencent",
    "oracle",
    "scaleway",
    "online s.a.s",  # Scaleway's legal entity name
    "contabo",
    "leaseweb",
    "hostinger",
    "ionos",
    "m247",
    "psychz",
    "packet",
    "equinix",
    "upcloud",
    "kamatera",
    "hurricane electric",
    "colocrossing",
    "quadranet",
    "g-core",
    "worldstream",
    "cloudsigma",
)


def is_hosting_ip(client_ip: str | None) -> bool:
    """True when the client IP's ASN belongs to a cloud/VPS/hosting provider rather than a residential or mobile ISP.

    Fail-open: no DB, an unparseable/private IP, or any lookup error yields
    False. The IP is never stored — only the boolean classification is
    counted.
    """
    if not client_ip:
        return False
    ip = client_ip.split(",")[0].strip()
    if not ip:
        return False
    reader = _geoip_asn_reader()
    if reader is None:
        return False
    try:
        org = (reader.asn(ip).autonomous_system_organization or "").lower()
    except Exception:  # address not in db / private / lookup error
        return False
    return any(tok in org for tok in _HOSTING_ASN_ORG_TOKENS)


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
    """True when `host` is our own site, an alternate hostname that serves it, or the hosting server's IP — an in-site navigation, not a real external referral."""
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
    """Classify a referrer into a host, '(internal)' for self-referrals (in-site navigation, our own IP), or '(direct)' for a missing/blank referer.

    '(direct)' and '(internal)' are kept apart on purpose: true direct (dark
    social, bookmarks, no Referer) is a different signal from an in-site reload.
    """
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
    """Full external referrer URL, normalized for stable counting: scheme dropped, 'www.' stripped, fragment dropped, tracking/campaign params removed, and the whole thing length-capped. Returns None for a missing/blank or self-referral URL so only real external sources get a per-URL counter."""
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
    """Roll a referrer host (as returned by `referrer_host`) up into a coarse acquisition channel. '(direct)'/'(internal)' pass through as Direct/Internal."""
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
    to keep cardinality bounded.
    """

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
    """True for self/internal traffic we never count: the server's own public IP (per ANALYTICS_IGNORE_IPS), plus all loopback / private / link-local ranges (health checks, monitoring, SSR self-fetch)."""
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
    session: CassandraSession, day: str, path: str, referer: str | None, user_agent: str | None
) -> None:
    """Diagnostics for a human pageview that landed in '(direct)': bump the UA-class counter and append a short-lived raw sample (TTL on the table)."""
    from app.core.statements import AnalyticsStmts

    uac = ua_class(user_agent)
    session.execute_async(AnalyticsStmts.DIRECT_UACLASS_BUMP, (day, uac))
    session.execute_async(
        AnalyticsStmts.DIRECT_SAMPLE_INSERT,
        (day, path[:200], (referer or "")[:300], (user_agent or "")[:300], uac),
    )


def _purge_direct_sample_ua(session: CassandraSession, user_agent: str, day: str) -> int:
    """Retroactive correction (2026-07-22): the moment a UA's is_repeated_ua count JUST crosses _UA_FREQ_THRESHOLD, its earlier hits TODAY have already been counted as human — but only via the '(direct)' bucket is there a raw per-request log (pageview_direct_sample) to reconstruct the correction from. Walks today's sample rows for this exact UA, decrements every counter they fed, and deletes the rows so they stop showing as human direct traffic. Returns the number of hits purged.

    What this can NEVER reach, by design of what's stored at all: unique-
    visitor counts (Redis HyperLogLog has no remove operation — a one-way
    structure), sessions (stitched via a privacy-safe token with no UA
    linkage kept), and any bot traffic that arrived WITH a real referrer —
    pageview_direct_sample only ever captured '(direct)' hits.

    Best-effort: any Cassandra hiccup here must never surface to the caller —
    this runs inline in record_pageview and must not risk failing a real
    page view over a correction pass.
    """
    from app.core.statements import AnalyticsStmts

    try:
        rows = list(session.execute(AnalyticsStmts.DIRECT_SAMPLE_ALL_BY_DAY, (day,)))
    except Exception as exc:
        log.debug("ua purge scan skipped: %s", exc)
        return 0

    matches = [r for r in rows if r.user_agent == user_agent]
    if not matches:
        return 0

    per_path: dict[str, int] = {}
    for r in matches:
        per_path[r.path] = per_path.get(r.path, 0) + 1
        try:
            session.execute_async(AnalyticsStmts.DIRECT_SAMPLE_DELETE, (day, r.ts))
        except Exception as exc:
            log.debug("ua purge row delete skipped: %s", exc)

    total = len(matches)
    uac = ua_class(user_agent)
    browser = browser_family(user_agent)
    try:
        session.execute_async(AnalyticsStmts.PAGEVIEW_BUMP_DECR, (total, "human", day))
        session.execute_async(AnalyticsStmts.REFERRER_BUMP_DECR, (total, day, "(direct)"))
        session.execute_async(AnalyticsStmts.DIRECT_UACLASS_BUMP_DECR, (total, day, uac))
        session.execute_async(AnalyticsStmts.DEVICE_BUMP_DECR, (total, day, uac))
        session.execute_async(AnalyticsStmts.BROWSER_BUMP_DECR, (total, day, browser))
        for path, count in per_path.items():
            session.execute_async(
                AnalyticsStmts.PATH_KIND_BUMP_DECR, (count, day, path[:200], "human")
            )
    except Exception as exc:
        log.debug("ua purge counter decrement failed: %s", exc)

    log.info("retroactively purged %d hit(s) for repeated UA on %s", total, day)
    return total


_PVDEDUP_PREFIX = "algorand:pvdedup:"
_PVDEDUP_TTL_SECONDS = 4


def _is_recent_duplicate_pageview(client_ip: str | None, user_agent: str | None, path: str) -> bool:
    """True when the same visitor hit the same path within a few seconds — the usual SSR landing + Flutter beacon double-count, or a route-settling burst."""
    if not client_ip:
        return False
    ip = client_ip.split(",")[0].strip()
    if not ip:
        return False
    try:
        r = _uv_redis()
        token = _uv_token(ip, user_agent).hex()
        key = f"{_PVDEDUP_PREFIX}{token}:{path[:200]}"
        added = r.set(key, "1", nx=True, ex=_PVDEDUP_TTL_SECONDS)
        return not added
    except Exception as exc:
        log.debug("pageview dedup skipped: %s", exc)
        return False


def record_pageview(
    *,
    path: str,
    referer: str | None,
    user_agent: str | None,
    client_ip: str | None = None,
    campaign: str | None = None,
    accept_language: str | None = None,
    sec_fetch_mode: str | None = None,
    accept: str | None = None,
) -> None:
    """Best-effort counter bumps for one document request. Never raises.

    Bot/scraper traffic is detected only to EXCLUDE it — no bot-specific
    counters, breakdowns or KPIs are kept anymore (2026-07-22): the dashboard
    only cares about real human traffic, so a hit that trips any of these
    checks is simply dropped rather than recorded under a "bot" bucket.
    """
    if is_internal_client(client_ip):
        return  # don't count the server itself / internal probes
    if _is_recent_duplicate_pageview(client_ip, user_agent, path):
        return

    day = _today()
    ua_count = _ua_repeat_count(user_agent, day)
    repeated = ua_count is not None and ua_count > _UA_FREQ_THRESHOLD
    if ua_count == _UA_FREQ_THRESHOLD + 1:
        # THIS request is the one that tips the UA over the threshold — its
        # own earlier hits today were already counted as human. Claw them
        # back now, from the one bucket ('(direct)') with enough per-request
        # detail to reconstruct the correction (see _purge_direct_sample_ua).
        try:
            from app.core.cassandra import get_cassandra_session

            _purge_direct_sample_ua(get_cassandra_session(), user_agent or "", day)
        except Exception as exc:
            log.debug("ua purge trigger skipped: %s", exc)

    if (
        is_bot(user_agent)
        or is_malformed_ua(user_agent)
        or repeated
        or is_missing_fetch_metadata(user_agent, sec_fetch_mode)
        or is_missing_accept_header(user_agent, accept)
        or is_hosting_ip(client_ip)
    ):
        return
    try:
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import AnalyticsStmts

        session = get_cassandra_session()
        # Privacy-safe unique visitor count (Redis HLL), independent of Cassandra.
        record_unique("human", client_ip, user_agent, day)
        session.execute_async(AnalyticsStmts.PAGEVIEW_BUMP, ("human", day))
        session.execute_async(AnalyticsStmts.PATH_KIND_BUMP, (day, path[:200], "human"))
        # Server-side session stitching + new-vs-returning split.
        record_session(session, client_ip, user_agent, day)
        # Country (GeoIP, no IP stored) and campaign tag (utm/ref).
        country = country_for_ip(client_ip)
        if country:
            session.execute_async(AnalyticsStmts.GEO_BUMP, (day, country))
        if campaign:
            session.execute_async(AnalyticsStmts.CAMPAIGN_BUMP, (day, campaign[:80]))
        # Site-wide device + browser + hour-of-day segmentation.
        session.execute_async(AnalyticsStmts.DEVICE_BUMP, (day, ua_class(user_agent)))
        session.execute_async(AnalyticsStmts.BROWSER_BUMP, (day, browser_family(user_agent)))
        session.execute_async(AnalyticsStmts.HOUR_BUMP, (day, datetime.now(UTC).hour))
        lang = primary_language(accept_language)
        if lang:
            session.execute_async(AnalyticsStmts.LANGUAGE_BUMP, (day, lang[:8]))
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
    """Best-effort: count a search term for the day (and separately when it returned nothing). Bots are skipped so the demand signal stays human."""
    q = (query or "").strip().lower()
    if not q or is_bot(user_agent) or is_malformed_ua(user_agent):
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
    """Best-effort: count a request to an unknown article/section URL — broken inbound links and crawler waste. Internal/self traffic is excluded."""
    if is_internal_client(client_ip):
        return
    try:
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import AnalyticsStmts

        session = get_cassandra_session()
        session.execute_async(AnalyticsStmts.NOTFOUND_BUMP, (_today(), path[:200]))
    except Exception as exc:
        log.debug("notfound record skipped: %s", exc)


def _recent_direct_samples(session: CassandraSession, window: set[str], limit: int) -> list[dict]:
    """Latest raw '(direct)' samples across the window, newest day first.

    The table clusters newest-first, so a per-day LIMIT yields the most recent
    rows without an ALLOW FILTERING scan; we stop once `limit` are collected.
    """
    from app.core.statements import AnalyticsStmts

    samples: list[dict] = []
    for day in sorted(window, reverse=True):
        if len(samples) >= limit:
            break
        rows = session.execute(AnalyticsStmts.DIRECT_SAMPLE_BY_DAY, (day, limit - len(samples)))
        samples.extend(
            {
                "day": day,
                "path": r.path,
                "referer": r.referer or "",
                "user_agent": r.user_agent or "",
                "ua_class": r.ua_class,
            }
            for r in rows
        )
    return samples


def _build_alerts(out: dict, cur404: int, prev404: int) -> list[dict]:
    """A small rules pass producing at-a-glance anomaly chips, over data already aggregated in `out` (plus the whole-window 404 totals, not just the top N, passed in separately). Each alert is {level: info|warn, text}."""
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
    """specs: {name: prepared_statement}. Returns {name: {day: rows}}, silently omitting a (name, day) whose query errored (e.g. a table a migration hasn't created yet) — same fail-open contract the old per-table try/except had."""
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
    """Sum `value_col` per distinct `key` across already-fetched {day: rows}, ranked descending. Output rows are always shaped {key: ..., "views": ...} regardless of the source column's name."""
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
    """Top pages, human traffic only (bot hits are excluded at record time and never reach this table's 'human' partition)."""
    agg: dict[str, int] = {}
    for rows in path_kind_by_day.values():
        for r in rows:
            if r.kind != "human":
                continue
            agg[r.path] = agg.get(r.path, 0) + int(r.views)
    ranked = sorted(agg.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [{"path": p, "views": v} for p, v in ranked]


def _referrer_paths_from_rows(rp_by_day: dict[str, list], *, limit: int) -> list[dict]:
    """Top (referrer, landing-path) pairs over the window."""
    agg: dict[tuple[str, str], int] = {}
    for rows in rp_by_day.values():
        for r in rows:
            agg[(r.referrer, r.path)] = agg.get((r.referrer, r.path), 0) + int(r.views)
    ranked = sorted(agg.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [{"referrer": k[0], "path": k[1], "views": v} for k, v in ranked]


def _article_referrers_from_rows(
    rp_by_day: dict[str, list],
    article_cards: dict[str, object],
    *,
    limit_articles: int,
    limit_referrers: int = 5,
) -> list[dict]:
    """Top articles by total REFERRED views, each with its own top referrers — unlike referrer_paths (a single flat top-N pairs list dominated by high- traffic non-article pages like Home/sections), this answers "which referrer leads to which article" directly, one row per article. '(direct)' carries no source signal, so it's excluded entirely here — both from the per-article breakdown and from the total (2026-07-23)."""
    agg: dict[str, dict[str, int]] = {}
    for rows in rp_by_day.values():
        for r in rows:
            path = r.path or ""
            if not path.startswith(_ARTICLE_PREFIX) or r.referrer == "(direct)":
                continue
            bucket = agg.setdefault(path, {})
            bucket[r.referrer] = bucket.get(r.referrer, 0) + int(r.views)

    totals = {path: sum(refs.values()) for path, refs in agg.items()}
    top_paths = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:limit_articles]

    out: list[dict] = []
    for path, total in top_paths:
        row = article_cards.get(path)
        title = (row.title if row and row.title else None) or "Article"
        referrers = sorted(agg[path].items(), key=lambda kv: kv[1], reverse=True)[:limit_referrers]
        out.append(
            {
                "path": path,
                "label": title,
                "views": total,
                "referrers": [{"referrer": r, "views": v} for r, v in referrers],
            }
        )
    return out


def _referrer_articles_from_rows(
    rp_by_day: dict[str, list],
    article_cards: dict[str, object],
    *,
    limit_referrers: int,
    limit_articles: int = 20,
) -> list[dict]:
    """Top referrers, each with its own top articles — the mirror of _article_referrers_from_rows (grouped by referrer instead of by article). '(direct)' is excluded for the same reason as its sibling: it carries no source signal (2026-07-23)."""
    agg: dict[str, dict[str, int]] = {}
    for rows in rp_by_day.values():
        for r in rows:
            path = r.path or ""
            if not path.startswith(_ARTICLE_PREFIX) or r.referrer == "(direct)":
                continue
            bucket = agg.setdefault(r.referrer, {})
            bucket[path] = bucket.get(path, 0) + int(r.views)

    totals = {ref: sum(paths.values()) for ref, paths in agg.items()}
    top_referrers = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:limit_referrers]

    out: list[dict] = []
    for referrer, total in top_referrers:
        articles = sorted(agg[referrer].items(), key=lambda kv: kv[1], reverse=True)[
            :limit_articles
        ]
        article_rows = []
        for path, views in articles:
            row = article_cards.get(path)
            title = (row.title if row and row.title else None) or "Article"
            article_rows.append({"path": path, "label": title, "views": views})
        out.append({"referrer": referrer, "views": total, "articles": article_rows})
    return out


def _hours_from_rows(hour_by_day: dict[str, list]) -> list[dict]:
    """Human views per hour-of-day (0-23), summed across the window."""
    sums = dict.fromkeys(range(24), 0)
    for rows in hour_by_day.values():
        for r in rows:
            if r.hour is not None and 0 <= r.hour <= 23:
                sums[r.hour] += int(r.views)
    return [{"hour": h, "views": sums[h]} for h in range(24)]


def _referrer_categories_from_rows(referrer_by_day: dict[str, list]) -> list[dict]:
    """All referrer hosts rolled up into acquisition channels (Search, Social, AI, News, Direct, Internal, Other) over the window."""
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
    """Human views per content section. Article paths resolve to the article's primary tag ('Section · DeFi') via the pre-fetched article metadata batch; everything else buckets by route (Home, Section · X, Search, Other)."""
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


# A referrer-over-time chart with one line per referrer is only legible up to
# a handful of series — everything past the top N by total volume gets rolled
# into "Other" rather than omitted, so the daily totals still add up.
_REFERRERS_DAILY_TOP_N = 6


def _referrers_daily_from_rows(referrer_by_day: dict[str, list], window: set[str]) -> dict:
    """Per-day views for the top referrers over the window (+ 'Other'), for a trend chart — top_referrers only gives a single window-wide ranking."""
    totals: dict[str, int] = {}
    for rows in referrer_by_day.values():
        for r in rows:
            totals[r.referrer] = totals.get(r.referrer, 0) + int(r.views)
    top_referrers = [
        k
        for k, _ in sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[
            :_REFERRERS_DAILY_TOP_N
        ]
    ]
    top_set = set(top_referrers)
    per_day: dict[str, dict[str, int]] = {d: dict.fromkeys(top_referrers, 0) for d in window}
    for day, rows in referrer_by_day.items():
        if day not in per_day:
            continue
        for r in rows:
            views = int(r.views)
            if r.referrer in top_set:
                per_day[day][r.referrer] += views
            else:
                per_day[day]["Other"] = per_day[day].get("Other", 0) + views
    if any("Other" in per_day[d] for d in per_day):
        for d in per_day:
            per_day[d].setdefault("Other", 0)
        series = [*top_referrers, "Other"]
    else:
        series = top_referrers
    return {
        "referrers": series,
        "daily": [{"day": d, **per_day[d]} for d in sorted(window)],
    }


def _editorial_scorecard_from_rows(
    path_kind_by_day: dict[str, list],
    article_cards: dict[str, object],
    window: set[str],
    *,
    limit: int,
) -> list[dict]:
    """Top articles by human views, each with age-since-publish and a daily view series — so a slow-burn explainer is distinguishable from a one-day spike."""
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
    """Every distinct article path referenced anywhere in the fetched tables, mapped to its parsed UUID — the union covers sections, the editorial scorecard and path-label resolution, so article metadata is fetched once."""
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
    """One concurrent batch fetching title/published_at/tags for every distinct article path in `article_ids`, reused by section bucketing, the editorial scorecard and path-label resolution instead of each querying it again."""
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
    """Daily human traffic series + aggregated top paths and referrers over `days`. Bot/scraper traffic is excluded at record time (see record_pageview) and never appears here — no bot counters are kept."""
    out: dict = {
        "days": days,
        "daily": [],
        "top_paths": [],
        "top_referrers": [],
        "referrers_daily": {"referrers": [], "daily": []},
        "totals": {},
        "prev_totals": {},
        "direct_uaclass": [],
        "direct_samples": [],
        "top_searches": [],
        "zero_searches": [],
        "referrer_paths": [],
        "article_referrers": [],
        "referrer_articles": [],
        "top_notfound": [],
        "device": [],
        "browser": [],
        "languages": [],
        "hours": [],
        "referrer_categories": [],
        "sections": [],
        "top_referrer_urls": [],
        "sessions": {},
        "sessions_daily": [],
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

        human = _series("human")
        # Unique visitors (Redis HLL): per-day uniques for the chart, plus
        # period-wide uniques (PFCOUNT unions the daily keys).
        uv_human_day, uv_human = _unique_counts("human", sorted(window))
        _, uv_human_prev = _unique_counts("human", sorted(prev_window))
        out["daily"] = [
            {
                "day": d,
                "human": human.get(d, 0),
                "human_unique": uv_human_day.get(d, 0),
            }
            for d in sorted(window)
        ]
        out["totals"] = {
            "human": sum(human.get(d, 0) for d in window),
            "human_unique": uv_human,
        }
        out["prev_totals"] = {
            "human": sum(human.get(d, 0) for d in prev_window),
            "human_unique": uv_human_prev,
        }

        # Every remaining table is one partition per day. Instead of a sequential
        # per-day loop per table (was 15+ tables x up to 90 days = 1000+ blocking
        # round-trips), fetch every (table, day) pair for the current window in
        # one concurrent batch and have every aggregation below share it — several
        # tables (path_kind, referrer, session) were previously re-scanned
        # 2-3x under different names.
        table_specs = {
            "path_kind": AnalyticsStmts.PATH_KIND_BY_DAY,
            "referrer": AnalyticsStmts.AGG_REFERRER,
            "direct_uaclass": AnalyticsStmts.AGG_DIRECT_UACLASS,
            "search": AnalyticsStmts.AGG_SEARCH,
            "search_zero": AnalyticsStmts.AGG_SEARCH_ZERO,
            "notfound": AnalyticsStmts.AGG_NOTFOUND,
            "referrer_path": AnalyticsStmts.REFERRER_PATH_BY_DAY,
            "hour": AnalyticsStmts.HOUR_BY_DAY,
            "device": AnalyticsStmts.AGG_DEVICE,
            "browser": AnalyticsStmts.AGG_BROWSER,
            "language": AnalyticsStmts.AGG_LANGUAGE,
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
        out["referrers_daily"] = _referrers_daily_from_rows(by_day["referrer"], window)

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
        out["referrer_paths"] = _referrer_paths_from_rows(by_day["referrer_path"], limit=top)
        out["article_referrers"] = _article_referrers_from_rows(
            by_day["referrer_path"], article_cards, limit_articles=top
        )
        out["referrer_articles"] = _referrer_articles_from_rows(
            by_day["referrer_path"], article_cards, limit_referrers=top
        )
        out["top_notfound"] = _rank(by_day["notfound"], "path", limit=top)

        out["device"] = _rank(by_day["device"], "device", limit=top)
        out["browser"] = _rank(by_day["browser"], "browser", limit=top)
        out["languages"] = _rank(by_day["language"], "lang", limit=top)
        out["hours"] = _hours_from_rows(by_day["hour"])
        out["referrer_categories"] = _referrer_categories_from_rows(by_day["referrer"])
        out["sections"] = _sections_from_rows(by_day["path_kind"], article_cards, limit=top)
        out["top_referrer_urls"] = _rank(by_day["referrer_url"], "referrer_url", limit=top)
        out["sessions_daily"] = _session_daily_from_rows(by_day["session"], window)
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
