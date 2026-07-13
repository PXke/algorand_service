from __future__ import annotations

from types import SimpleNamespace

from app.modules.seo import analytics_store as a


def test_is_bot_detection() -> None:
    assert a.is_bot("Googlebot/2.1 (+http://www.google.com/bot.html)")
    assert a.is_bot("Mozilla/5.0 (compatible; bingbot/2.0)")
    assert a.is_bot(None) and a.is_bot("")  # no UA -> treat as bot
    assert a.is_bot("python-requests/2.31")
    assert not a.is_bot(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
    )


def test_is_missing_fetch_metadata_flags_modern_chrome_and_firefox() -> None:
    chrome_ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    firefox_ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.3; rv:132.0) Gecko/20100101 Firefox/132.0"
    # No Sec-Fetch-Mode at all -> both would auto-send it since Chrome 76 / Firefox 90.
    assert a.is_missing_fetch_metadata(chrome_ua, None)
    assert a.is_missing_fetch_metadata(firefox_ua, "")
    # Header present (any value — only presence is checked) -> not flagged.
    assert not a.is_missing_fetch_metadata(chrome_ua, "navigate")
    assert not a.is_missing_fetch_metadata(firefox_ua, "cors")


def test_is_missing_fetch_metadata_ignores_old_and_non_qualifying_uas() -> None:
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


def test_primary_language_parses_accept_language_header() -> None:
    assert a.primary_language("en-US,en;q=0.9,fa;q=0.8") == "en"
    assert a.primary_language("fr-FR") == "fr"
    assert a.primary_language("zh-Hans-CN;q=1.0") == "zh"
    assert a.primary_language(None) is None
    assert a.primary_language("") is None
    assert a.primary_language("***") is None


def test_is_bot_flags_self_identifying_url_convention() -> None:
    # Polite crawlers (but never real browsers) embed a "+https://..." info
    # link in their UA by convention. Found leaking into "human (direct)"
    # traffic 2026-07-13: a Bluesky automod bot, and two third-party SEO
    # preview scrapers — none named in the token denylist.
    assert a.is_bot(
        "Mozilla/5.0 (compatible; SkyWatch/1.0; "
        "+https://github.com/skywatch-bsky/skywatch-automod)"
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


def test_recent_duplicate_pageview_dedupes_burst(monkeypatch) -> None:
    store: dict[str, str] = {}

    class _FakeRedis:
        def set(self, key, value, *, nx=False, ex=None):
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

    def incr(self, k):
        self.store[k] = int(self.store.get(k, 0)) + 1
        return self.store[k]

    def expire(self, k, ttl):
        pass


def test_record_session_new_then_returning(monkeypatch) -> None:
    fake = _FakeRedis()
    monkeypatch.setattr(a, "_uv_redis", lambda: fake)
    bumps: list = []

    class _Sess:
        # `prepare` lets the statement registry resolve AnalyticsStmts.SESSION_BUMP
        # (the descriptor calls get_cassandra_session().prepare(cql)); the resolved
        # statement is ignored by execute_async, which just records the params.
        def prepare(self, cql):
            return cql

        def execute_async(self, stmt, params):
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


def test_record_session_skips_without_ip(monkeypatch) -> None:
    # No client IP -> never touches Redis or Cassandra.
    def _boom():
        raise AssertionError("must not touch Redis")

    monkeypatch.setattr(a, "_uv_redis", _boom)
    a.record_session(None, None, "Mozilla/5.0", "2026-06-27")


def test_is_bot_flags_internet_scanners() -> None:
    # Scanners send a browser-ish Mozilla/ UA + no Referer, so they used to slip
    # into human "(direct)". They must now classify as bots.
    assert a.is_bot("Mozilla/5.0 zgrab/0.x")
    assert a.is_bot("Mozilla/5.0 (compatible; pathscan/1.0)")
    assert a.is_bot("visionheight.com/scan Mozilla/5.0 (Macintosh) Chrome/120")
    assert a.is_bot("Mozilla/5.0 (compatible; CensysInspect/1.1)")
    # A genuine browser is still human.
    assert not a.is_bot(
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605 Mobile Safari/604"
    )


def test_is_bot_flags_self_identifying_tools_found_in_direct_traffic() -> None:
    # 2026-07-12: a 500-row sample of "human direct" pageviews was ~35%
    # IPScanner and ~10% GoogleOther, both sending Mozilla/-prefixed UAs so
    # they slipped the "mozilla/" fallback check like the internet scanners
    # above. Both self-identify in parens like the rest of the denylist.
    assert a.is_bot("Mozilla/5.0 (compatible; IPScanner/1.0)")
    assert a.is_bot(
        "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/150.0.7871.46 Mobile Safari/537.36 (compatible; GoogleOther)"
    )
    assert a.bot_name("Mozilla/5.0 (compatible; IPScanner/1.0)") == "IPScanner"
    assert a.bot_name("Mozilla/5.0 (compatible; GoogleOther)") == "Googlebot"


def test_is_repeated_ua_flags_only_past_threshold(monkeypatch) -> None:
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


def test_is_repeated_ua_counts_each_distinct_ua_separately(monkeypatch) -> None:
    fake = _FakeRedis()
    monkeypatch.setattr(a, "_uv_redis", lambda: fake)
    day = "2026-07-12"
    for _ in range(a._UA_FREQ_THRESHOLD + 5):
        a.is_repeated_ua("Mozilla/5.0 (Windows NT 10.0) Chrome/133", day=day)
    # A different UA on the same day starts its own fresh count, unaffected by
    # how many times the first UA was seen.
    assert not a.is_repeated_ua("Mozilla/5.0 (Macintosh) Safari/605", day=day)


def test_is_repeated_ua_ignores_empty_and_fails_open(monkeypatch) -> None:
    assert not a.is_repeated_ua(None)
    assert not a.is_repeated_ua("")

    def _boom():
        raise ConnectionError("redis down")

    monkeypatch.setattr(a, "_uv_redis", _boom)
    assert not a.is_repeated_ua("Mozilla/5.0 (Windows NT 10.0) Chrome/133")


def test_is_malformed_ua_flags_real_fake_found_in_prod_sample() -> None:
    # 2026-07-12: this exact string was in a live pageview_direct_sample pull —
    # three independent violations in one UA (typo'd "live Gecko", a 3-part
    # AppleWebKit version, and a Chrome UA whose Safari/ token doesn't match
    # Chrome's permanently-frozen 537.36). Any one of them alone is enough.
    assert a.is_malformed_ua(
        "Mozilla/5.0 (Windows NT 6.2;en-US) AppleWebKit/537.32.36 "
        "(KHTML, live Gecko) Chrome/55.0.3103.66 Safari/537.32"
    )


def test_is_malformed_ua_flags_bad_khtml_phrase_alone() -> None:
    assert a.is_malformed_ua("Mozilla/5.0 (X11; Linux x86_64) KHTML, liek Gecko Safari/537.36")


def test_is_malformed_ua_flags_chrome_with_wrong_webkit_version() -> None:
    # Real Chrome has kept AppleWebKit frozen at exactly 537.36 since ~2013,
    # regardless of actual Chrome version — this is impossible from a real
    # install no matter how old or new.
    assert a.is_malformed_ua(
        "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/601.1 "
        "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/601.1"
    )


def test_is_malformed_ua_flags_firefox_rv_mismatch() -> None:
    # rv: and the trailing Firefox/ version are always identical by
    # construction in a real Firefox — a mismatch can't happen organically.
    assert a.is_malformed_ua(
        "Mozilla/5.0 (Windows NT 10.0; rv:47.0) Gecko/20100101 Firefox/128.0"
    )


def test_is_malformed_ua_flags_desktop_firefox_unfrozen_gecko() -> None:
    # Desktop Firefox has kept Gecko/ frozen at "20100101" since ~2012 — a
    # real Gecko/128.0 desktop UA is impossible. Mobile (Android) Firefox is
    # explicitly NOT held to this, since it genuinely doesn't freeze this token.
    assert a.is_malformed_ua("Mozilla/5.0 (Windows NT 10.0; rv:128.0) Gecko/128.0 Firefox/128.0")
    assert not a.is_malformed_ua(
        "Mozilla/5.0 (Android 15; Mobile; rv:152.0) Gecko/152.0 Firefox/152.0"
    )


def test_is_malformed_ua_allows_genuine_modern_browsers() -> None:
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
    assert not a.is_malformed_ua(None)
    assert not a.is_malformed_ua("")


def test_bot_name_labels_malformed_ua_distinctly() -> None:
    assert (
        a.bot_name(
            "Mozilla/5.0 (Windows NT 6.2;en-US) AppleWebKit/537.32.36 "
            "(KHTML, live Gecko) Chrome/55.0.3103.66 Safari/537.32"
        )
        == "Malformed UA"
    )
    # A named bot still wins even if it also happens to have odd syntax.
    assert a.bot_name("Mozilla/5.0 (compatible; GPTBot/1.1; live Gecko)") == "GPTBot"


def test_session_counts_excludes_multipage_from_total() -> None:
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
    # The site also answers on these hostnames -> in-site navigation, '(internal)'.
    assert a.referrer_host("https://pxke.me/x") == "(internal)"
    assert a.referrer_host("https://wordpress.pxke.me/y") == "(internal)"
    assert a.referrer_host("https://algosearch.pxke.me/z") == "(internal)"
    # An unrelated sub-domain on the same apex stays a real external referrer.
    assert a.referrer_host("https://blog.pxke.me/post") == "blog.pxke.me"


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
