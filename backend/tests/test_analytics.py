"""Bot-detection heuristics and session/search tracking for the analytics store."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Never

import pytest

from app.core.statements import AnalyticsStmts
from app.modules.seo import analytics_store as a


def _patch_prepare(monkeypatch: pytest.MonkeyPatch) -> None:
    """AnalyticsStmts.X resolves to a prepared statement via prepare_cached, which calls the REAL get_cassandra_session() unless patched — needed by any test that accesses a statement attribute for the first time, even when it also passes its own fake `session` object into the function under test (the descriptor access happens independently of that)."""
    monkeypatch.setattr(
        "app.core.cassandra.get_cassandra_session",
        lambda: SimpleNamespace(prepare=lambda cql: cql),
    )


def test_is_bot_detection() -> None:
    """Flags known crawler/library UAs and blank UAs, passes a real Chrome UA."""
    assert a.is_bot("Googlebot/2.1 (+http://www.google.com/bot.html)")
    assert a.is_bot("Mozilla/5.0 (compatible; bingbot/2.0)")
    assert a.is_bot(None)
    assert a.is_bot("")
    assert a.is_bot("python-requests/2.31")
    assert not a.is_bot(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
    )


def test_is_missing_fetch_metadata_flags_modern_chrome_and_firefox() -> None:
    """Flags modern Chrome/Firefox UAs lacking Sec-Fetch-Mode, passes when present."""
    chrome_ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    firefox_ua = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.3; rv:132.0) Gecko/20100101 Firefox/132.0"
    )
    # No Sec-Fetch-Mode at all -> both would auto-send it since Chrome 76 / Firefox 90.
    assert a.is_missing_fetch_metadata(chrome_ua, None)
    assert a.is_missing_fetch_metadata(firefox_ua, "")
    # Header present (any value — only presence is checked) -> not flagged.
    assert not a.is_missing_fetch_metadata(chrome_ua, "navigate")
    assert not a.is_missing_fetch_metadata(firefox_ua, "cors")


def test_is_missing_fetch_metadata_ignores_old_and_non_qualifying_uas() -> None:
    """Never flags pre-rollout Chrome/Firefox versions or Safari for missing Sec-Fetch-Mode."""
    # A Chrome/Firefox version below the Fetch-Metadata rollout never had it —
    # missing the header there is expected, not a bot tell.
    assert not a.is_missing_fetch_metadata("Mozilla/5.0 Chrome/60.0.0.0 Safari/537.36", None)
    assert not a.is_missing_fetch_metadata(
        "Mozilla/5.0 (X11; Linux x86_64; rv:80.0) Gecko/20100101 Firefox/80.0", None
    )
    # Safari (and WebKit-based iOS Chrome/"CriOS") didn't support Fetch
    # Metadata until 16.4 — never flagged regardless of Sec-Fetch-Mode.
    assert not a.is_missing_fetch_metadata(
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        None,
    )


def test_is_missing_accept_header_flags_absent_or_bare_wildcard() -> None:
    """Flags a missing or bare "*/*" Accept header on Chrome, passes a real Accept value."""
    chrome_ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    # No Accept header, or the bare "*/*" default of an unconfigured HTTP
    # client library — a real Chrome/Firefox navigation never sends either.
    assert a.is_missing_accept_header(chrome_ua, None)
    assert a.is_missing_accept_header(chrome_ua, "")
    assert a.is_missing_accept_header(chrome_ua, "*/*")
    assert not a.is_missing_accept_header(
        chrome_ua,
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    )


def test_is_missing_accept_header_ignores_non_chrome_firefox_uas() -> None:
    """Never flags Safari or an absent UA for a missing Accept header."""
    # Safari's Accept conventions aren't checked here — same conservative
    # carve-out as is_missing_fetch_metadata.
    safari_ua = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/26.5.2 Mobile/15E148 Safari/604.1"
    )
    assert not a.is_missing_accept_header(safari_ua, None)
    assert not a.is_missing_accept_header(None, None)


def test_primary_language_parses_accept_language_header() -> None:
    """Extracts the base language code from an Accept-Language header."""
    assert a.primary_language("en-US,en;q=0.9,fa;q=0.8") == "en"
    assert a.primary_language("fr-FR") == "fr"
    assert a.primary_language("zh-Hans-CN;q=1.0") == "zh"
    assert a.primary_language(None) is None
    assert a.primary_language("") is None
    assert a.primary_language("***") is None


def test_is_bot_flags_self_identifying_url_convention() -> None:
    """Flags UAs carrying a "+http(s)://" self-ID link, passes ordinary browser UAs."""
    # Polite crawlers (but never real browsers) embed a "+https://..." info
    # link in their UA by convention. Found leaking into "human (direct)"
    # traffic 2026-07-13: a Bluesky automod bot, and two third-party SEO
    # preview scrapers — none named in the token denylist.
    assert a.is_bot(
        "Mozilla/5.0 (compatible; SkyWatch/1.0; +https://github.com/skywatch-bsky/skywatch-automod)"
    )
    assert a.is_bot("Mozilla/5.0 (compatible; SvelteKit-FYI/1.0; +https://sveltekit.fyi)")
    assert a.is_bot("Mozilla/5.0 (compatible; NuxtFyi/0.1; +https://nuxt.fyi)")
    assert a.is_bot(
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.11; rv:49.0) Gecko/20100101 "
        "Firefox/49.0 (FlipboardProxy/1.2; +http://flipboard.com/browserproxy)"
    )
    # Ordinary browsers never carry a "+http" self-ID link.
    assert not a.is_bot(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
    )


def test_is_bot_flags_known_decoy_ua() -> None:
    """Exact-matches the frozen iOS-13.2.3/Safari-13.0.3 decoy UA but not a different iPhone build."""
    # Exact-match denylist for the specific frozen iOS-13.2.3/Safari-13.0.3
    # string identified 2026-07-12 as the single biggest offender in
    # pageview_direct_sample (471 rows/7 days, peak 172/day) — structurally
    # well-formed and non-self-identifying, so only an exact match catches it
    # on the first request rather than waiting on is_repeated_ua's threshold.
    assert a.is_bot(
        "Mozilla/5.0 (iPhone; CPU iPhone OS 13_2_3 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/13.0.3 Mobile/15E148 Safari/604.1"
    )
    # A different iPhone build/Safari version is real diverse traffic, not this bot.
    assert not a.is_bot(
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    )


def test_referrer_host_classification() -> None:
    """Classifies missing referrers as direct, own-domain as internal, and others by host."""
    # Missing/blank Referer is true direct (dark social, bookmarks, no header).
    assert a.referrer_host(None) == "(direct)"
    assert a.referrer_host("") == "(direct)"
    # Our own domain is an in-site navigation -> '(internal)', NOT direct, so the
    # two signals stay separable.
    assert a.referrer_host("https://algorand.pxke.me/news") == "(internal)"
    assert a.referrer_host("https://www.algorand.pxke.me/x") == "(internal)"  # www
    assert a.referrer_host("https://www.ecosia.org/search?q=algorand") == "ecosia.org"
    assert a.referrer_host("https://t.co/abc") == "t.co"
    # The loose old substring match could treat short/partial hosts as self —
    # guard it: a real external host must resolve to itself, not '(internal)'.
    assert a.referrer_host("https://me.example/x") == "me.example"


def test_referrer_host_hides_server_ip() -> None:
    """Classifies the hosting server's own IP (with or without port) as internal."""
    # The hosting server's own IP is internal, not a real referral source.
    assert a.referrer_host("http://5.135.131.229/news") == "(internal)"
    assert a.referrer_host("http://5.135.131.229:8080/news") == "(internal)"  # with port


def test_is_bot_flags_non_browser_uas() -> None:
    """Flags any non-empty UA lacking a "Mozilla/" token, passes real browser strings."""
    # Non-empty UA without a "Mozilla/" token is a library/scraper, not human —
    # this is what was inflating "human (direct)".
    assert a.is_bot("MyCustomFetcher/1.0")
    assert a.is_bot("Java/17.0.1")
    # Real browser strings still pass through as human.
    assert not a.is_bot(
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605 Mobile Safari/604"
    )


def test_ua_class_buckets() -> None:
    """Buckets UAs into no-ua, headless, non-browser, mobile-browser and desktop-browser."""
    assert a.ua_class(None) == "no-ua"
    assert a.ua_class("HeadlessChrome/120") == "headless"
    assert a.ua_class("MyCustomFetcher/1.0") == "non-browser"
    assert (
        a.ua_class("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile Safari/604")
        == "mobile-browser"
    )
    assert (
        a.ua_class("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36")
        == "desktop-browser"
    )


def test_is_internal_client_filters_self_and_private() -> None:
    """Flags the server's own IP, loopback, private ranges and their XFF leftmost entry."""
    assert a.is_internal_client("5.135.131.229")  # the server's own public IP
    assert a.is_internal_client("127.0.0.1")  # loopback
    assert a.is_internal_client("10.0.0.5")
    assert a.is_internal_client("192.168.1.4")
    # X-Forwarded-For chain: the left-most entry is the real client
    assert a.is_internal_client("127.0.0.1, 5.135.131.229")
    assert not a.is_internal_client("8.8.8.8")  # a real external visitor
    assert not a.is_internal_client(None)
    assert not a.is_internal_client("")


def test_recent_duplicate_pageview_dedupes_burst(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dedupes a repeat pageview to the same path but not a different path, and flags known bot UAs."""
    store: dict[str, str] = {}

    class _FakeRedis:
        def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:  # noqa: ARG002 -- name must match the real callee's keyword arg
            if nx and key in store:
                return False
            store[key] = value
            return True

    monkeypatch.setattr(a, "_uv_redis", lambda: _FakeRedis())
    ip = "8.8.8.8"
    ua = "Mozilla/5.0 Chrome/120"
    assert not a._is_recent_duplicate_pageview(ip, ua, "/news")
    assert a._is_recent_duplicate_pageview(ip, ua, "/news")
    assert not a._is_recent_duplicate_pageview(ip, ua, "/about")
    assert a.is_bot("UptimeRobot/2.0 (http://uptimerobot.com)")
    assert a.is_bot("Mozilla/5.0 (compatible; TwitterBot/1.0)")
    assert a.is_bot("Datadog/Synthetics")


def test_browser_family_is_specific_first() -> None:
    """Classifies Edge/Chrome/Safari/Firefox by their most specific token, ignoring the generic ones they also carry."""
    # Edge ships "Edg/" alongside "Chrome/" and "Safari/" — the specific token wins.
    assert (
        a.browser_family("Mozilla/5.0 (Windows NT 10.0) Chrome/120 Safari/537.36 Edg/120") == "Edge"
    )
    # Plain Chrome ships "Safari/" too, but must classify as Chrome.
    assert a.browser_family("Mozilla/5.0 (Windows NT 10.0) Chrome/120 Safari/537.36") == "Chrome"
    # Real Safari has no Chrome token.
    assert (
        a.browser_family("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) Version/17 Safari/605")
        == "Safari"
    )
    assert a.browser_family("Mozilla/5.0 (X11; Linux) Firefox/121") == "Firefox"
    assert a.browser_family(None) == "Other"
    assert a.browser_family("MyCustomFetcher/1.0") == "Other"


def test_referrer_category_channels() -> None:
    """Buckets referrer hosts into Search/Social/AI assistant/News/Other, preferring the more specific subdomain."""
    assert a.referrer_category("ecosia.org") == "Search"
    assert a.referrer_category("google.com") == "Search"
    assert a.referrer_category("reddit.com") == "Social"
    assert a.referrer_category("t.co") == "Social"
    assert a.referrer_category("perplexity.ai") == "AI assistant"
    # A more specific sub-domain wins over the broader family it sits under.
    assert a.referrer_category("gemini.google.com") == "AI assistant"
    assert a.referrer_category("cointelegraph.com") == "News & aggregators"
    assert a.referrer_category("some-random-blog.example") == "Other"
    # The synthetic buckets pass through unchanged.
    assert a.referrer_category("(direct)") == "Direct"
    assert a.referrer_category("(internal)") == "Internal"


def test_section_bucket_routes() -> None:
    """Maps known path shapes to their section bucket label, else Other."""
    assert a.section_bucket("/") == "Home"
    assert a.section_bucket("/search") == "Search"
    assert a.section_bucket("/section/defi") == "Section · defi"
    assert a.section_bucket("/news/articles/00000000-0000-0000-0000-000000000000") == "Article"
    assert a.section_bucket("/some/unknown/path") == "Other"


def test_normalize_referrer_url_strips_noise() -> None:
    """Strips tracking params/fragment/www, drops self-referrals, and length-caps the result."""
    # Tracking/campaign params and the fragment are dropped, host loses 'www.',
    # the path is kept — so the exact thread/page is preserved without dupes.
    assert (
        a.normalize_referrer_url(
            "https://www.reddit.com/r/algorand/comments/xyz/?utm_source=foo&fbclid=bar#c1"
        )
        == "reddit.com/r/algorand/comments/xyz/"
    )
    # A meaningful query param survives.
    assert (
        a.normalize_referrer_url("https://news.example.com/feed?id=42&utm_medium=email")
        == "news.example.com/feed?id=42"
    )
    # Self-referrals and blanks never get a URL counter.
    assert a.normalize_referrer_url(None) is None
    assert a.normalize_referrer_url("") is None
    assert a.normalize_referrer_url("https://www.algorand.pxke.me/news") is None
    # Pathless referrer normalizes to a bare "/".
    assert a.normalize_referrer_url("https://t.co") == "t.co/"
    # Output is length-capped.
    assert len(a.normalize_referrer_url("https://x.example/" + "a" * 500)) <= 300


def test_uv_token_is_stable_and_distinct() -> None:
    """Derives a stable 16-byte token from IP+UA that changes with either input and never leaks the raw IP."""
    # Same visitor -> same 16-byte token (so period uniques dedupe across days);
    # different IP or UA -> different token. No raw IP is recoverable.
    t1 = a._uv_token("8.8.8.8", "Mozilla/5.0")
    assert t1 == a._uv_token("8.8.8.8", "Mozilla/5.0")
    assert len(t1) == 16
    assert isinstance(t1, bytes)
    assert t1 != a._uv_token("8.8.4.4", "Mozilla/5.0")
    assert t1 != a._uv_token("8.8.8.8", "Mozilla/4.0")
    assert b"8.8.8.8" not in t1


def test_record_search_skips_empty_and_bots(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never touches Cassandra when recording a blank query or a bot-UA search."""
    # Should no-op (no Cassandra access) for blank queries and bot UAs.
    called = False

    def _boom() -> Never:
        nonlocal called
        called = True
        raise AssertionError("must not touch Cassandra")

    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", _boom, raising=False)
    a.record_search("", 3)
    a.record_search("algorand", 3, user_agent="Googlebot/2.1")
    assert called is False


class _FakeRedis:
    """Minimal in-memory stand-in for the bits record_session touches."""

    def __init__(self) -> None:
        self.store: dict = {}

    def exists(self, k: str) -> int:
        return 1 if k in self.store else 0

    def set(self, k: str, v: object, ex: int | None = None) -> None:  # noqa: ARG002 -- name must match the real callee's keyword arg
        self.store[k] = v

    def incr(self, k: str) -> int:
        self.store[k] = int(self.store.get(k, 0)) + 1
        return self.store[k]

    def expire(self, k: str, ttl: int) -> None:
        pass


def test_record_session_new_then_returning(monkeypatch: pytest.MonkeyPatch) -> None:
    """Classifies a first hit as new, a same-session repeat as multipage, and a post-expiry hit as returning."""
    fake = _FakeRedis()
    monkeypatch.setattr(a, "_uv_redis", lambda: fake)
    bumps: list = []

    class _Sess:
        # `prepare` lets the statement registry resolve AnalyticsStmts.SESSION_BUMP
        # (the descriptor calls get_cassandra_session().prepare(cql)); the resolved
        # statement is ignored by execute_async, which just records the params.
        def prepare(self, cql: str) -> str:
            return cql

        def execute_async(self, _stmt: str, params: tuple) -> None:
            bumps.append(params)  # (day, vtype)

    sess = _Sess()
    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: sess)
    day = "2026-06-27"

    # First hit -> a new session, classified 'new'.
    a.record_session(sess, "8.8.8.8", "Mozilla/5.0", day)
    assert bumps == [(day, "new")]

    # Second hit while the 30-min window lives -> same session, no new/returning
    # bump, but it does confirm the session as multi-page (not a bounce).
    a.record_session(sess, "8.8.8.8", "Mozilla/5.0", day)
    assert bumps == [(day, "new"), (day, "multipage")]

    # A 3rd hit in the same session doesn't re-confirm multipage.
    a.record_session(sess, "8.8.8.8", "Mozilla/5.0", day)
    assert len(bumps) == 2

    # Session window expires (drop sess:) but the seen: marker persists ->
    # the next visit is a returning session.
    token = a._uv_token("8.8.8.8", "Mozilla/5.0").hex()
    del fake.store[f"{a._SESSION_PREFIX}{token}"]
    a.record_session(sess, "8.8.8.8", "Mozilla/5.0", day)
    assert bumps[-1] == (day, "returning")


def test_record_session_skips_without_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never touches Redis when recording a session with no client IP."""

    # No client IP -> never touches Redis or Cassandra.
    def _boom() -> Never:
        raise AssertionError("must not touch Redis")

    monkeypatch.setattr(a, "_uv_redis", _boom)
    a.record_session(None, None, "Mozilla/5.0", "2026-06-27")


def test_is_bot_flags_internet_scanners() -> None:
    """Flags known internet-scanner UAs despite their browser-ish Mozilla/ prefix."""
    # Scanners send a browser-ish Mozilla/ UA + no Referer, so they used to slip
    # into human "(direct)". They must now classify as bots.
    assert a.is_bot("Mozilla/5.0 zgrab/0.x")
    assert a.is_bot("Mozilla/5.0 (compatible; pathscan/1.0)")
    assert a.is_bot("visionheight.com/scan Mozilla/5.0 (Macintosh) Chrome/120")
    assert a.is_bot("Mozilla/5.0 (compatible; CensysInspect/1.1)")
    # A genuine browser is still human.
    assert not a.is_bot(
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605 Mobile Safari/604"
    )


def test_is_bot_flags_self_identifying_tools_found_in_direct_traffic() -> None:
    """Flags IPScanner and GoogleOther UAs that self-identify in parens despite a Mozilla/ prefix."""
    # 2026-07-12: a 500-row sample of "human direct" pageviews was ~35%
    # IPScanner and ~10% GoogleOther, both sending Mozilla/-prefixed UAs so
    # they slipped the "mozilla/" fallback check like the internet scanners
    # above. Both self-identify in parens like the rest of the denylist.
    assert a.is_bot("Mozilla/5.0 (compatible; IPScanner/1.0)")
    assert a.is_bot(
        "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/150.0.7871.46 Mobile Safari/537.36 (compatible; GoogleOther)"
    )


def test_is_repeated_ua_flags_only_past_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only flags an exact-match UA once its per-day hit count crosses the threshold."""
    # 2026-07-12: same sample also had 100/500 byte-identical requests for one
    # ordinary-looking (non-self-identifying) legacy-iOS UA — is_bot() can't
    # catch this by name, so this is a separate per-day exact-match counter.
    fake = _FakeRedis()
    monkeypatch.setattr(a, "_uv_redis", lambda: fake)
    ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 13_2_3 like Mac OS X) Version/13.0.3 Safari/604.1"
    day = "2026-07-12"

    for _ in range(a._UA_FREQ_THRESHOLD):
        assert not a.is_repeated_ua(ua, day=day)
    # The next hit crosses the threshold.
    assert a.is_repeated_ua(ua, day=day)


def test_is_repeated_ua_counts_each_distinct_ua_separately(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keeps a fresh per-day count for a different UA, unaffected by another UA's hit count."""
    fake = _FakeRedis()
    monkeypatch.setattr(a, "_uv_redis", lambda: fake)
    day = "2026-07-12"
    for _ in range(a._UA_FREQ_THRESHOLD + 5):
        a.is_repeated_ua("Mozilla/5.0 (Windows NT 10.0) Chrome/133", day=day)
    # A different UA on the same day starts its own fresh count, unaffected by
    # how many times the first UA was seen.
    assert not a.is_repeated_ua("Mozilla/5.0 (Macintosh) Safari/605", day=day)


def test_is_repeated_ua_ignores_empty_and_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never flags an empty UA, and fails open (not flagged) when Redis is down."""
    assert not a.is_repeated_ua(None)
    assert not a.is_repeated_ua("")

    def _boom() -> Never:
        raise ConnectionError("redis down")

    monkeypatch.setattr(a, "_uv_redis", _boom)
    assert not a.is_repeated_ua("Mozilla/5.0 (Windows NT 10.0) Chrome/133")


class _FakeSession:
    """Captures execute/execute_async calls; execute() returns canned rows."""

    def __init__(self, rows: tuple = ()) -> None:
        self.rows = list(rows)
        self.calls: list = []

    def execute(self, stmt: str, params: tuple) -> list:
        self.calls.append(("execute", stmt, params))
        return self.rows

    def execute_async(self, stmt: str, params: tuple) -> None:
        self.calls.append(("execute_async", stmt, params))

    def prepare(self, cql: str) -> str:
        return cql


def test_purge_direct_sample_ua_decrements_matching_hits_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deletes and decrements counters only for the day's rows matching the target UA."""
    _patch_prepare(monkeypatch)
    ua = "Mozilla/5.0 (X11; Linux x86_64) Chrome/130.0.0.0 Safari/537.36"
    other_ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6) Version/17.6 Safari/604.1"
    rows = [
        SimpleNamespace(
            ts="t1", path="/about", referer="", user_agent=ua, ua_class="desktop-browser"
        ),
        SimpleNamespace(
            ts="t2", path="/about", referer="", user_agent=ua, ua_class="desktop-browser"
        ),
        SimpleNamespace(
            ts="t3", path="/contact", referer="", user_agent=ua, ua_class="desktop-browser"
        ),
        SimpleNamespace(
            ts="t4", path="/", referer="", user_agent=other_ua, ua_class="mobile-browser"
        ),
    ]
    sess = _FakeSession(rows)
    purged = a._purge_direct_sample_ua(sess, ua, "2026-07-22")
    assert purged == 3

    deletes = [c for c in sess.calls if c[1] == AnalyticsStmts.DIRECT_SAMPLE_DELETE]
    assert {c[2] for c in deletes} == {
        ("2026-07-22", "t1"),
        ("2026-07-22", "t2"),
        ("2026-07-22", "t3"),
    }

    pageview_decr = next(c for c in sess.calls if c[1] == AnalyticsStmts.PAGEVIEW_BUMP_DECR)
    assert pageview_decr[2] == (3, "human", "2026-07-22")

    referrer_decr = next(c for c in sess.calls if c[1] == AnalyticsStmts.REFERRER_BUMP_DECR)
    assert referrer_decr[2] == (3, "2026-07-22", "(direct)")

    path_decrs = {c[2] for c in sess.calls if c[1] == AnalyticsStmts.PATH_KIND_BUMP_DECR}
    assert path_decrs == {
        (2, "2026-07-22", "/about", "human"),
        (1, "2026-07-22", "/contact", "human"),
    }


def test_purge_direct_sample_ua_no_match_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns zero purged and issues no deletes when no row matches the target UA."""
    _patch_prepare(monkeypatch)
    sess = _FakeSession(
        [
            SimpleNamespace(
                ts="t1", path="/", referer="", user_agent="other-ua", ua_class="desktop-browser"
            )
        ]
    )
    purged = a._purge_direct_sample_ua(sess, "target-ua", "2026-07-22")
    assert purged == 0
    assert sess.calls == [("execute", AnalyticsStmts.DIRECT_SAMPLE_ALL_BY_DAY, ("2026-07-22",))]


def test_purge_direct_sample_ua_fails_closed_on_scan_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns zero purged (fails closed) when the Cassandra scan raises."""
    _patch_prepare(monkeypatch)

    class _BoomSession:
        def execute(self, _stmt: str, _params: tuple) -> Never:
            raise ConnectionError("cassandra down")

    assert a._purge_direct_sample_ua(_BoomSession(), "any-ua", "2026-07-22") == 0


def test_record_pageview_triggers_purge_exactly_once_at_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fires the purge exactly once, on the request that tips the UA over the frequency threshold."""
    # The purge must fire on the request that TIPS the UA over the threshold
    # (the 16th), not before (still counted human) and not again on the 17th+
    # (already handled, would be a wasted repeat scan).
    fake_redis = _FakeRedis()
    monkeypatch.setattr(a, "_uv_redis", lambda: fake_redis)
    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: _FakeSession())
    purge_calls: list = []
    monkeypatch.setattr(
        a, "_purge_direct_sample_ua", lambda _sess, ua, day: purge_calls.append((ua, day))
    )
    # record_session touches Cassandra further downstream on the human path;
    # stub it out so this test stays focused on the purge trigger.
    monkeypatch.setattr(a, "record_session", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(a, "record_unique", lambda *_args, **_kwargs: None)

    ua = "Mozilla/5.0 (X11; Linux x86_64) Chrome/130.0.0.0 Safari/537.36"
    for _ in range(a._UA_FREQ_THRESHOLD):
        a.record_pageview(path="/about", referer=None, user_agent=ua)
    assert purge_calls == []  # not yet — still under threshold

    a.record_pageview(path="/about", referer=None, user_agent=ua)  # the 16th
    assert purge_calls == [(ua, a._today())]

    a.record_pageview(path="/about", referer=None, user_agent=ua)  # the 17th
    assert purge_calls == [(ua, a._today())]  # unchanged — no re-trigger


def test_is_malformed_ua_flags_real_fake_found_in_prod_sample() -> None:
    """Flags a UA carrying three independent structural violations found live in prod."""
    # 2026-07-12: this exact string was in a live pageview_direct_sample pull —
    # three independent violations in one UA (typo'd "live Gecko", a 3-part
    # AppleWebKit version, and a Chrome UA whose Safari/ token doesn't match
    # Chrome's permanently-frozen 537.36). Any one of them alone is enough.
    assert a.is_malformed_ua(
        "Mozilla/5.0 (Windows NT 6.2;en-US) AppleWebKit/537.32.36 "
        "(KHTML, live Gecko) Chrome/55.0.3103.66 Safari/537.32"
    )


def test_is_malformed_ua_flags_bad_khtml_phrase_alone() -> None:
    """Flags a UA with a misspelled "liek Gecko" KHTML phrase."""
    assert a.is_malformed_ua("Mozilla/5.0 (X11; Linux x86_64) KHTML, liek Gecko Safari/537.36")


def test_is_malformed_ua_flags_chrome_with_wrong_webkit_version() -> None:
    """Flags Chrome claiming an AppleWebKit version other than the permanently-frozen 537.36."""
    # Real Chrome has kept AppleWebKit frozen at exactly 537.36 since ~2013,
    # regardless of actual Chrome version — this is impossible from a real
    # install no matter how old or new.
    assert a.is_malformed_ua(
        "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/601.1 "
        "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/601.1"
    )


def test_is_malformed_ua_flags_firefox_rv_mismatch() -> None:
    """Flags a Firefox UA whose rv: token doesn't match its trailing Firefox/ version."""
    # rv: and the trailing Firefox/ version are always identical by
    # construction in a real Firefox — a mismatch can't happen organically.
    assert a.is_malformed_ua("Mozilla/5.0 (Windows NT 10.0; rv:47.0) Gecko/20100101 Firefox/128.0")


def test_is_malformed_ua_flags_desktop_firefox_unfrozen_gecko() -> None:
    """Flags desktop Firefox with an unfrozen Gecko/ token, but not mobile Firefox which never freezes it."""
    # Desktop Firefox has kept Gecko/ frozen at "20100101" since ~2012 — a
    # real Gecko/128.0 desktop UA is impossible. Mobile (Android) Firefox is
    # explicitly NOT held to this, since it genuinely doesn't freeze this token.
    assert a.is_malformed_ua("Mozilla/5.0 (Windows NT 10.0; rv:128.0) Gecko/128.0 Firefox/128.0")
    assert not a.is_malformed_ua(
        "Mozilla/5.0 (Android 15; Mobile; rv:152.0) Gecko/152.0 Firefox/152.0"
    )


def test_is_malformed_ua_flags_dead_ppc_mac_platform() -> None:
    """Flags a UA claiming the discontinued PowerPC Mac OS X platform."""
    # PowerPC Macs were discontinued in 2006 — nothing running "PPC Mac OS X"
    # is browsing the web today. Found 2026-07-20 recurring across every
    # sampled day of pageview_direct_sample, hiding in human "(direct)".
    assert a.is_malformed_ua(
        "Mozilla/5.0 (Macintosh; U; PPC Mac OS X; de-de) AppleWebKit/417.9 "
        "(KHTML, like Gecko) Safari/417.9.2"
    )


def test_is_malformed_ua_flags_chrome_on_windows_xp() -> None:
    """Flags a Chrome UA on Windows XP/Server 2003, a platform Chrome dropped in 2016."""
    # Chrome dropped Windows XP/Server 2003 (NT 5.1/5.2) support at version
    # 49 (Feb 2016) and never shipped a later build for it.
    assert a.is_malformed_ua(
        "Mozilla/5.0 (Windows NT 5.1; WOW64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/75.0.3770.100 Safari/537.36"
    )
    # A period-accurate Chrome/49 on the same OS is still flagged, but by the
    # separate stale-version-floor rule below, not this XP-specific one.


def test_is_malformed_ua_flags_stale_chrome_and_firefox_versions() -> None:
    """Flags Chrome/Firefox major versions below the evergreen-browser floor, including stale ChromeOS builds."""
    # Chrome/Firefox are evergreen, auto-updating browsers on a ~4-week
    # release cadence; a major version below 100 (shipped ~2022) is
    # implausible at real volume in current traffic. Also catches ChromeOS
    # devices claiming years-stale Chrome builds (ChromeOS force-updates).
    assert a.is_malformed_ua(
        "Mozilla/5.0 (Windows NT 6.3; WOW64; rv:33.0) Gecko/20100101 Firefox/33.0"
    )
    assert a.is_malformed_ua(
        "Mozilla/5.0 (X11; CrOS i686 3912.101.0) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/27.0.1453.116 Safari/537.36"
    )


def test_is_malformed_ua_allows_genuine_modern_browsers() -> None:
    """Never flags genuine modern Chrome/Firefox/Safari UAs, including a known-live but syntax-plausible one."""
    # Real, currently-plausible UAs must never be flagged.
    assert not a.is_malformed_ua(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
    )
    assert not a.is_malformed_ua(
        "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
    )
    assert not a.is_malformed_ua(
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.3.1 Safari/605.1.15"
    )
    # Real Safari's Version/·Safari/ relationship is deliberately not policed
    # (less rigidly consistent historically than Chrome's), so this known
    # live-traffic UA — plausible syntax, just suspicious on repeat volume —
    # must NOT be flagged by structure alone.
    assert not a.is_malformed_ua(
        "Mozilla/5.0 (iPhone; CPU iPhone OS 13_2_3 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/13.0.3 Mobile/15E148 Safari/604.1"
    )


def test_is_malformed_ua_ignores_empty() -> None:
    """Never flags a None or blank UA as malformed."""
    assert not a.is_malformed_ua(None)
    assert not a.is_malformed_ua("")


def test_session_counts_excludes_multipage_from_total() -> None:
    """Excludes the multipage subtotal from the new+returning total session count."""
    # `multipage` confirms a subset of already-counted new/returning sessions —
    # it must not inflate `total` (used for returning-rate/pages-per-visit).
    by_day = {
        "2026-06-27": [
            SimpleNamespace(vtype="new", sessions=10),
            SimpleNamespace(vtype="returning", sessions=4),
            SimpleNamespace(vtype="multipage", sessions=6),
        ],
    }
    out = a._session_counts_from_rows(by_day)
    assert out == {"new": 10, "returning": 4, "total": 14, "multipage": 6}


def test_referrer_host_folds_alternate_self_hosts() -> None:
    """Classifies the site's alternate hostnames as internal, but not an unrelated subdomain of the same apex."""
    # The site also answers on these hostnames -> in-site navigation, '(internal)'.
    assert a.referrer_host("https://pxke.me/x") == "(internal)"
    assert a.referrer_host("https://wordpress.pxke.me/y") == "(internal)"
    assert a.referrer_host("https://algosearch.pxke.me/z") == "(internal)"
    # An unrelated sub-domain on the same apex stays a real external referrer.
    assert a.referrer_host("https://blog.pxke.me/post") == "blog.pxke.me"


def test_referrer_host_folds_social_subdomains() -> None:
    """Collapses known link-shim/mobile subdomains to their canonical social host."""
    # Link-shim / mobile sub-domains collapse to the canonical host.
    assert a.referrer_host("https://m.facebook.com/x") == "facebook.com"
    assert a.referrer_host("https://l.facebook.com/l.php?u=y") == "facebook.com"
    assert a.referrer_host("https://lm.facebook.com/") == "facebook.com"
    assert a.referrer_host("https://out.reddit.com/t3_abc") == "reddit.com"
    # A normal host is unchanged.
    assert a.referrer_host("https://www.ecosia.org/search?q=algorand") == "ecosia.org"


def test_campaign_label() -> None:
    """Builds a campaign label from utm/ref params, falling back and length-capping as needed."""
    assert (
        a.campaign_label({"utm_source": "facebook", "utm_campaign": "Reti-Launch"})
        == "facebook / reti-launch"
    )
    # utm_medium is the fallback partner to utm_source.
    assert (
        a.campaign_label({"utm_source": "newsletter", "utm_medium": "email"})
        == "newsletter / email"
    )
    assert a.campaign_label({"utm_source": "twitter"}) == "twitter"
    assert a.campaign_label({"ref": "fb-2026-06-27"}) == "ref:fb-2026-06-27"
    # List-valued params (some frameworks) take the first value.
    assert a.campaign_label({"utm_source": ["x"]}) == "x"
    # Nothing taggable -> None.
    assert a.campaign_label({}) is None
    assert a.campaign_label({"q": "algorand"}) is None
    # Values are length-capped.
    assert len(a.campaign_label({"ref": "z" * 200})) <= 64


def test_country_for_ip_failopen_without_db() -> None:
    """Returns an empty string, never raising, when no GeoIP database is configured."""
    # No GeoIP db configured -> '' (never raises, never blocks a pageview).
    a._geoip_reader.cache_clear()
    assert a.country_for_ip("8.8.8.8") == ""
    assert a.country_for_ip(None) == ""
    assert a.country_for_ip("") == ""


def test_is_hosting_ip_failopen_without_db() -> None:
    """Returns False, never raising, when no ASN database is configured."""
    # No ASN db configured -> False (never raises, never blocks a pageview).
    a._geoip_asn_reader.cache_clear()
    assert a.is_hosting_ip("8.8.8.8") is False
    assert a.is_hosting_ip(None) is False
    assert a.is_hosting_ip("") is False


def test_is_hosting_ip_flags_known_cloud_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flags known cloud/hosting ASN orgs, but not a residential/mobile ISP."""

    class _FakeAsn:
        def __init__(self, org: str) -> None:
            self.autonomous_system_organization = org

    class _FakeReader:
        def __init__(self, org: str) -> None:
            self._org = org

        def asn(self, _ip: str) -> _FakeAsn:
            return _FakeAsn(self._org)

    monkeypatch.setattr(a, "_geoip_asn_reader", lambda: _FakeReader("AMAZON-02"))
    assert a.is_hosting_ip("1.2.3.4") is True

    monkeypatch.setattr(a, "_geoip_asn_reader", lambda: _FakeReader("HETZNER-AS"))
    assert a.is_hosting_ip("1.2.3.4") is True

    # A residential/mobile ISP ASN org must never be flagged.
    monkeypatch.setattr(
        a, "_geoip_asn_reader", lambda: _FakeReader("Comcast Cable Communications, LLC")
    )
    assert a.is_hosting_ip("1.2.3.4") is False


def test_is_hosting_ip_failopen_on_lookup_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns False (fails open) when the ASN reader raises during lookup."""

    class _FakeReader:
        def asn(self, _ip: str) -> Never:
            raise ValueError("address not in database")

    monkeypatch.setattr(a, "_geoip_asn_reader", lambda: _FakeReader())
    assert a.is_hosting_ip("1.2.3.4") is False


def test_referrers_daily_buckets_top_n_and_rolls_up_other() -> None:
    """Keeps only the top-N referrers as their own daily series, rolling the rest into Other."""
    # 8 distinct referrers across 2 days; only the top _REFERRERS_DAILY_TOP_N
    # (6) get their own series, the rest fold into "Other" per day.
    by_day = {
        "2026-07-21": [
            SimpleNamespace(referrer="google.com", views=50),
            SimpleNamespace(referrer="bing.com", views=20),
            SimpleNamespace(referrer="reddit.com", views=15),
            SimpleNamespace(referrer="twitter.com", views=10),
            SimpleNamespace(referrer="(direct)", views=8),
            SimpleNamespace(referrer="ecosia.org", views=5),
            SimpleNamespace(referrer="rare-blog.example", views=2),
            SimpleNamespace(referrer="another-rare.example", views=1),
        ],
        "2026-07-22": [
            SimpleNamespace(referrer="google.com", views=40),
            SimpleNamespace(referrer="rare-blog.example", views=3),
        ],
    }
    window = {"2026-07-21", "2026-07-22"}
    out = a._referrers_daily_from_rows(by_day, window)
    assert out["referrers"] == [
        "google.com",
        "bing.com",
        "reddit.com",
        "twitter.com",
        "(direct)",
        "ecosia.org",
        "Other",
    ]
    assert len(out["daily"]) == 2
    day1 = next(d for d in out["daily"] if d["day"] == "2026-07-21")
    assert day1["google.com"] == 50
    assert day1["Other"] == 3  # rare-blog.example + another-rare.example
    day2 = next(d for d in out["daily"] if d["day"] == "2026-07-22")
    assert day2["google.com"] == 40
    assert day2["bing.com"] == 0  # present but zero, not missing
    assert day2["Other"] == 3


def test_referrers_daily_no_other_when_under_top_n() -> None:
    """Omits the Other bucket entirely when the day has fewer referrers than the top-N cap."""
    by_day = {"2026-07-21": [SimpleNamespace(referrer="google.com", views=10)]}
    out = a._referrers_daily_from_rows(by_day, {"2026-07-21"})
    assert out["referrers"] == ["google.com"]
    assert "Other" not in out["daily"][0]


def test_article_referrers_groups_by_article_not_flat_pairs() -> None:
    """Groups referrers under each article, excluding non-article paths and direct traffic."""
    # Non-article paths (Home, sections) must be excluded entirely — the flat
    # referrer_paths list already covers those; this is article-only.
    article_a = f"{a._ARTICLE_PREFIX}11111111-1111-1111-1111-111111111111"
    article_b = f"{a._ARTICLE_PREFIX}22222222-2222-2222-2222-222222222222"
    by_day = {
        "2026-07-21": [
            SimpleNamespace(referrer="google.com", path=article_a, views=30),
            SimpleNamespace(referrer="reddit.com", path=article_a, views=10),
            SimpleNamespace(referrer="(direct)", path=article_a, views=5),
            SimpleNamespace(referrer="bing.com", path=article_b, views=8),
            SimpleNamespace(referrer="google.com", path="/", views=100),  # excluded: not an article
        ],
    }
    article_cards = {
        article_a: SimpleNamespace(title="Algorand Ships Thing"),
        article_b: SimpleNamespace(title="Another Article"),
    }
    out = a._article_referrers_from_rows(
        by_day, article_cards, limit_articles=10, limit_referrers=2
    )
    assert len(out) == 2
    top = out[0]
    assert top["path"] == article_a
    assert top["label"] == "Algorand Ships Thing"
    # '(direct)' carries no source signal — excluded from both the breakdown
    # and the total (30 + 10, not 45).
    assert top["views"] == 40
    assert top["referrers"] == [
        {"referrer": "google.com", "views": 30},
        {"referrer": "reddit.com", "views": 10},
    ]
    assert "(direct)" not in [r["referrer"] for r in top["referrers"]]
    assert out[1]["path"] == article_b
    assert out[1]["label"] == "Another Article"


def test_article_referrers_excludes_direct_only_article_entirely() -> None:
    """Omits an article entirely when all of its traffic is direct."""
    # An article with ONLY direct traffic has zero referred views and must not
    # appear at all, not show up with an empty referrers list.
    article_a = f"{a._ARTICLE_PREFIX}44444444-4444-4444-4444-444444444444"
    by_day = {"2026-07-21": [SimpleNamespace(referrer="(direct)", path=article_a, views=50)]}
    out = a._article_referrers_from_rows(by_day, {}, limit_articles=10)
    assert out == []


def test_referrer_articles_is_the_mirror_of_article_referrers() -> None:
    """Groups articles under each referrer (the inverse grouping), excluding direct traffic."""
    article_a = f"{a._ARTICLE_PREFIX}55555555-5555-5555-5555-555555555555"
    article_b = f"{a._ARTICLE_PREFIX}66666666-6666-6666-6666-666666666666"
    by_day = {
        "2026-07-21": [
            SimpleNamespace(referrer="google.com", path=article_a, views=30),
            SimpleNamespace(referrer="google.com", path=article_b, views=5),
            SimpleNamespace(referrer="reddit.com", path=article_a, views=10),
            SimpleNamespace(referrer="(direct)", path=article_a, views=100),  # excluded
            SimpleNamespace(
                referrer="google.com", path="/", views=1000
            ),  # excluded: not an article
        ],
    }
    article_cards = {
        article_a: SimpleNamespace(title="Article A"),
        article_b: SimpleNamespace(title="Article B"),
    }
    out = a._referrer_articles_from_rows(
        by_day, article_cards, limit_referrers=10, limit_articles=20
    )
    assert len(out) == 2
    google = next(r for r in out if r["referrer"] == "google.com")
    assert google["views"] == 35
    assert google["articles"] == [
        {"path": article_a, "label": "Article A", "views": 30},
        {"path": article_b, "label": "Article B", "views": 5},
    ]
    reddit = next(r for r in out if r["referrer"] == "reddit.com")
    assert reddit["views"] == 10
    assert reddit["articles"] == [{"path": article_a, "label": "Article A", "views": 10}]
    assert "(direct)" not in [r["referrer"] for r in out]


def test_article_referrers_falls_back_to_generic_label() -> None:
    """Labels an article "Article" when it has no matching entry in article_cards."""
    article_a = f"{a._ARTICLE_PREFIX}33333333-3333-3333-3333-333333333333"
    by_day = {"2026-07-21": [SimpleNamespace(referrer="google.com", path=article_a, views=5)]}
    out = a._article_referrers_from_rows(by_day, {}, limit_articles=10)
    assert out[0]["label"] == "Article"


def test_recent_days_window() -> None:
    """Returns the last 7 YYYY-MM-DD dates, most recent first."""
    days = a._recent_days(7)
    assert len(days) == 7
    assert days[0] == a._today()  # most recent first
    assert all(len(d) == 10 for d in days)  # YYYY-MM-DD


def test_recent_days_offset_is_disjoint_prior_window() -> None:
    """Returns a strictly-older, non-overlapping window when given an offset."""
    cur = a._recent_days(7)
    prev = a._recent_days(7, offset=7)
    assert len(prev) == 7
    assert set(cur).isdisjoint(prev)  # prior window doesn't overlap the current one
    assert prev[0] < cur[-1]  # prior window is strictly older


def test_static_label_friendly_names() -> None:
    """Maps known static paths to friendly labels, falling back to the raw path for articles."""
    assert a._static_label("/") == "Home"
    assert a._static_label("/news") == "News index"
    assert a._static_label("/section/defi") == "Section · defi"
    # article paths are resolved against the DB, so the static fallback is the path
    assert a._static_label("/news/articles/abc") == "/news/articles/abc"


def test_beacon_accept_wildcard_is_not_treated_as_a_bot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A browser fetch() POST sends Accept: */*, which must not drop the pageview.

    The bare-*/* rule is a scripting-library tell on a document NAVIGATION,
    where browsers always send a rich versioned Accept. On the SPA's JSON
    beacon it is what every real reader sends, so applying it there silently
    discarded all in-app navigation from Chrome/Firefox (and left no trace,
    since rejected hits aren't bucketed anywhere).
    """
    fake_redis = _FakeRedis()
    monkeypatch.setattr(a, "_uv_redis", lambda: fake_redis)
    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: _FakeSession())
    monkeypatch.setattr(a, "record_session", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(a, "record_unique", lambda *_args, **_kwargs: None)
    written: list = []
    monkeypatch.setattr(a, "_write_pageview_counters", lambda **kw: written.append(kw))

    chrome = "Mozilla/5.0 (X11; Linux x86_64) Chrome/128.0.0.0 Safari/537.36"
    beacon = {
        "referer": None,
        "user_agent": chrome,
        "sec_fetch_mode": "cors",  # browsers DO send this on fetch()
        "accept": "*/*",
    }

    a.record_pageview(path="/topic/defi", navigation=False, **beacon)
    assert [w["path"] for w in written] == ["/topic/defi"]

    # Same headers on a document request stay a bot tell: a real navigation
    # never sends bare */*.
    written.clear()
    a.record_pageview(path="/topic/sdk", navigation=True, **beacon)
    assert written == []


def test_missing_accept_language_flags_scripted_client_only_on_navigation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No Accept-Language from a browser-claiming UA is a bot tell on a navigation, but is never judged on the beacon."""
    chrome = "Mozilla/5.0 (X11; Linux x86_64) Chrome/128.0.0.0 Safari/537.36"
    assert a.is_missing_accept_language(chrome, None)
    assert a.is_missing_accept_language(chrome, "   ")
    assert not a.is_missing_accept_language(chrome, "en-US,en;q=0.9")
    # Unrecognised UA is never judged on this signal.
    assert not a.is_missing_accept_language("SomeNiche/1.0", None)

    fake_redis = _FakeRedis()
    monkeypatch.setattr(a, "_uv_redis", lambda: fake_redis)
    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: _FakeSession())
    monkeypatch.setattr(a, "record_session", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(a, "record_unique", lambda *_args, **_kwargs: None)
    written: list = []
    monkeypatch.setattr(a, "_write_pageview_counters", lambda **kw: written.append(kw))

    hit = {
        "referer": None,
        "user_agent": chrome,
        "sec_fetch_mode": "cors",
        "accept": "*/*",
        "accept_language": None,
    }
    a.record_pageview(path="/topic/defi", navigation=False, **hit)
    assert [w["path"] for w in written] == ["/topic/defi"]  # beacon: not judged

    written.clear()
    a.record_pageview(path="/topic/sdk", navigation=True, **hit)
    assert written == []  # navigation: dropped
