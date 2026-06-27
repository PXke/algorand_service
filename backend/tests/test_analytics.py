from __future__ import annotations

from app.modules.seo import analytics_store as a


def test_is_bot_detection() -> None:
    assert a.is_bot("Googlebot/2.1 (+http://www.google.com/bot.html)")
    assert a.is_bot("Mozilla/5.0 (compatible; bingbot/2.0)")
    assert a.is_bot(None) and a.is_bot("")  # no UA -> treat as bot
    assert a.is_bot("python-requests/2.31")
    assert not a.is_bot(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
    )


def test_referrer_host_classification() -> None:
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
    # The hosting server's own IP is internal, not a real referral source.
    assert a.referrer_host("http://5.135.131.229/news") == "(internal)"
    assert a.referrer_host("http://5.135.131.229:8080/news") == "(internal)"  # with port


def test_is_bot_flags_non_browser_uas() -> None:
    # Non-empty UA without a "Mozilla/" token is a library/scraper, not human —
    # this is what was inflating "human (direct)".
    assert a.is_bot("MyCustomFetcher/1.0")
    assert a.is_bot("Java/17.0.1")
    # Real browser strings still pass through as human.
    assert not a.is_bot(
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605 Mobile Safari/604"
    )


def test_ua_class_buckets() -> None:
    assert a.ua_class(None) == "no-ua"
    assert a.ua_class("HeadlessChrome/120") == "headless"
    assert a.ua_class("MyCustomFetcher/1.0") == "non-browser"
    assert a.ua_class(
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile Safari/604"
    ) == "mobile-browser"
    assert a.ua_class(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"
    ) == "desktop-browser"


def test_is_internal_client_filters_self_and_private() -> None:
    assert a.is_internal_client("5.135.131.229")  # the server's own public IP
    assert a.is_internal_client("127.0.0.1")  # loopback
    assert a.is_internal_client("10.0.0.5") and a.is_internal_client("192.168.1.4")
    # X-Forwarded-For chain: the left-most entry is the real client
    assert a.is_internal_client("127.0.0.1, 5.135.131.229")
    assert not a.is_internal_client("8.8.8.8")  # a real external visitor
    assert not a.is_internal_client(None) and not a.is_internal_client("")


def test_is_bot_covers_monitors_and_unfurlers() -> None:
    assert a.is_bot("UptimeRobot/2.0 (http://uptimerobot.com)")
    assert a.is_bot("Mozilla/5.0 (compatible; TwitterBot/1.0)")
    assert a.is_bot("Datadog/Synthetics")


def test_bot_name_identifies_crawlers() -> None:
    assert a.bot_name("Mozilla/5.0 (compatible; GPTBot/1.1)") == "GPTBot"
    assert a.bot_name("Mozilla/5.0 (compatible; Googlebot/2.1)") == "Googlebot"
    assert a.bot_name("ClaudeBot/1.0 (+anthropic.com)") == "ClaudeBot"
    assert a.bot_name("facebookexternalhit/1.1") == "Social unfurler"
    assert a.bot_name("python-requests/2.31") == "Generic HTTP client"
    assert a.bot_name("") == "No user-agent"
    # An unknown crawler still buckets, rather than vanishing.
    assert a.bot_name("SomeNewCrawler/9") == "Other bot"


def test_browser_family_is_specific_first() -> None:
    # Edge ships "Edg/" alongside "Chrome/" and "Safari/" — the specific token wins.
    assert a.browser_family(
        "Mozilla/5.0 (Windows NT 10.0) Chrome/120 Safari/537.36 Edg/120"
    ) == "Edge"
    # Plain Chrome ships "Safari/" too, but must classify as Chrome.
    assert a.browser_family(
        "Mozilla/5.0 (Windows NT 10.0) Chrome/120 Safari/537.36"
    ) == "Chrome"
    # Real Safari has no Chrome token.
    assert a.browser_family(
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) Version/17 Safari/605"
    ) == "Safari"
    assert a.browser_family("Mozilla/5.0 (X11; Linux) Firefox/121") == "Firefox"
    assert a.browser_family(None) == "Other"
    assert a.browser_family("MyCustomFetcher/1.0") == "Other"


def test_referrer_category_channels() -> None:
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
    assert a.section_bucket("/") == "Home"
    assert a.section_bucket("/search") == "Search"
    assert a.section_bucket("/section/defi") == "Section · defi"
    assert a.section_bucket(
        "/news/articles/00000000-0000-0000-0000-000000000000"
    ) == "Article"
    assert a.section_bucket("/some/unknown/path") == "Other"


def test_normalize_referrer_url_strips_noise() -> None:
    # Tracking/campaign params and the fragment are dropped, host loses 'www.',
    # the path is kept — so the exact thread/page is preserved without dupes.
    assert a.normalize_referrer_url(
        "https://www.reddit.com/r/algorand/comments/xyz/?utm_source=foo&fbclid=bar#c1"
    ) == "reddit.com/r/algorand/comments/xyz/"
    # A meaningful query param survives.
    assert a.normalize_referrer_url(
        "https://news.example.com/feed?id=42&utm_medium=email"
    ) == "news.example.com/feed?id=42"
    # Self-referrals and blanks never get a URL counter.
    assert a.normalize_referrer_url(None) is None
    assert a.normalize_referrer_url("") is None
    assert a.normalize_referrer_url("https://www.algorand.pxke.me/news") is None
    # Pathless referrer normalizes to a bare "/".
    assert a.normalize_referrer_url("https://t.co") == "t.co/"
    # Output is length-capped.
    assert len(a.normalize_referrer_url("https://x.example/" + "a" * 500)) <= 300


def test_uv_token_is_stable_and_distinct() -> None:
    # Same visitor -> same 16-byte token (so period uniques dedupe across days);
    # different IP or UA -> different token. No raw IP is recoverable.
    t1 = a._uv_token("8.8.8.8", "Mozilla/5.0")
    assert t1 == a._uv_token("8.8.8.8", "Mozilla/5.0")
    assert len(t1) == 16 and isinstance(t1, bytes)
    assert t1 != a._uv_token("8.8.4.4", "Mozilla/5.0")
    assert t1 != a._uv_token("8.8.8.8", "Mozilla/4.0")
    assert b"8.8.8.8" not in t1


def test_record_search_skips_empty_and_bots(monkeypatch) -> None:
    # Should no-op (no Cassandra access) for blank queries and bot UAs.
    called = False

    def _boom():
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

    def exists(self, k):
        return 1 if k in self.store else 0

    def set(self, k, v, ex=None):
        self.store[k] = v


def test_record_session_new_then_returning(monkeypatch) -> None:
    fake = _FakeRedis()
    monkeypatch.setattr(a, "_uv_redis", lambda: fake)
    bumps: list = []

    class _Sess:
        def execute_async(self, stmt, params):
            bumps.append(params)  # (day, vtype)

    sess = _Sess()
    day = "2026-06-27"

    # First hit -> a new session, classified 'new'.
    a.record_session(sess, lambda cql: cql, "8.8.8.8", "Mozilla/5.0", day)
    assert bumps == [(day, "new")]

    # Second hit while the 30-min window lives -> same session, no new bump.
    a.record_session(sess, lambda cql: cql, "8.8.8.8", "Mozilla/5.0", day)
    assert len(bumps) == 1

    # Session window expires (drop sess:) but the seen: marker persists ->
    # the next visit is a returning session.
    token = a._uv_token("8.8.8.8", "Mozilla/5.0").hex()
    del fake.store[f"{a._SESSION_PREFIX}{token}"]
    a.record_session(sess, lambda cql: cql, "8.8.8.8", "Mozilla/5.0", day)
    assert bumps[-1] == (day, "returning")


def test_record_session_skips_without_ip(monkeypatch) -> None:
    # No client IP -> never touches Redis or Cassandra.
    def _boom():
        raise AssertionError("must not touch Redis")

    monkeypatch.setattr(a, "_uv_redis", _boom)
    a.record_session(None, lambda cql: cql, None, "Mozilla/5.0", "2026-06-27")


def test_referrer_host_folds_social_subdomains() -> None:
    # Link-shim / mobile sub-domains collapse to the canonical host.
    assert a.referrer_host("https://m.facebook.com/x") == "facebook.com"
    assert a.referrer_host("https://l.facebook.com/l.php?u=y") == "facebook.com"
    assert a.referrer_host("https://lm.facebook.com/") == "facebook.com"
    assert a.referrer_host("https://out.reddit.com/t3_abc") == "reddit.com"
    # A normal host is unchanged.
    assert a.referrer_host("https://www.ecosia.org/search?q=algorand") == "ecosia.org"


def test_campaign_label() -> None:
    assert a.campaign_label({"utm_source": "facebook", "utm_campaign": "Reti-Launch"}) \
        == "facebook / reti-launch"
    # utm_medium is the fallback partner to utm_source.
    assert a.campaign_label({"utm_source": "newsletter", "utm_medium": "email"}) \
        == "newsletter / email"
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
    # No GeoIP db configured -> '' (never raises, never blocks a pageview).
    a._geoip_reader.cache_clear()
    assert a.country_for_ip("8.8.8.8") == ""
    assert a.country_for_ip(None) == ""
    assert a.country_for_ip("") == ""


def test_ai_crawler_stats_share() -> None:
    rows = [
        {"bot": "GPTBot", "views": 30},
        {"bot": "ClaudeBot", "views": 10},
        {"bot": "Googlebot", "views": 60},  # not an AI crawler
    ]
    s = a.ai_crawler_stats(rows)
    assert s["views"] == 40
    assert abs(s["share_of_bots"] - 0.4) < 1e-9
    # Empty -> zero share, no ZeroDivisionError.
    assert a.ai_crawler_stats([]) == {"views": 0, "share_of_bots": 0.0}


def test_recent_days_window() -> None:
    days = a._recent_days(7)
    assert len(days) == 7
    assert days[0] == a._today()  # most recent first
    assert all(len(d) == 10 for d in days)  # YYYY-MM-DD


def test_recent_days_offset_is_disjoint_prior_window() -> None:
    cur = a._recent_days(7)
    prev = a._recent_days(7, offset=7)
    assert len(prev) == 7
    assert set(cur).isdisjoint(prev)  # prior window doesn't overlap the current one
    assert prev[0] < cur[-1]  # prior window is strictly older


def test_static_label_friendly_names() -> None:
    assert a._static_label("/") == "Home"
    assert a._static_label("/news") == "News index"
    assert a._static_label("/section/defi") == "Section · defi"
    # article paths are resolved against the DB, so the static fallback is the path
    assert a._static_label("/news/articles/abc") == "/news/articles/abc"
