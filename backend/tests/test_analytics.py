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
