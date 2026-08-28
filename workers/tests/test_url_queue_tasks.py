"""Sampling same-domain pages while following outbound links."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Never

import pytest

from app.modules.crawler.tasks.url_queue_tasks import (
    _deep_classify_time_budget_seconds,
    _deep_crawl_for_relevance,
    _external_corroboration,
    _sample_domain_pages,
    classify_pending_domains,
    deep_classify_domain,
    drain_url_queue,
    reap_stale_deep_classify_flags,
    reclassify_gray_zone_domains,
)
from app.modules.scraper.core.base import ScrapeResult


class _FakeDriver:
    """Serves canned ScrapeResults for known URLs — no network."""

    def __init__(self, pages: dict[str, ScrapeResult]) -> None:
        self._pages = pages

    def scrape_with_fallback(self, url: str, _source_id: str) -> ScrapeResult:
        if url not in self._pages:
            raise ValueError(f"unexpected fetch: {url}")
        return self._pages[url]


def _result(url: str, text: str, raw_html: str = "") -> ScrapeResult:
    return ScrapeResult(
        source_id="svc", url=url, title="", text=text, content_hash="h", raw_html=raw_html
    )


def test_sample_domain_pages_follows_same_domain_links(monkeypatch: pytest.MonkeyPatch) -> None:
    """Follows same-domain links discovered on the landing page, skipping external ones."""
    monkeypatch.setattr("app.modules.crawler.robots.is_allowed", lambda _url: True)
    landing_html = (
        '<a href="/product">Product</a> '
        '<a href="https://other.example/ad">Ad</a> '
        '<a href="/docs">Docs</a>'
    )
    driver = _FakeDriver(
        {
            "https://svc.example": _result("https://svc.example", "landing text", landing_html),
            "https://svc.example/product": _result("https://svc.example/product", "product text"),
            "https://svc.example/docs": _result("https://svc.example/docs", "docs text"),
        }
    )
    pages, _ = _sample_domain_pages(driver, "https://svc.example", "svc.example", max_pages=3)
    assert [u for u, _, _ in pages] == [
        "https://svc.example",
        "https://svc.example/product",
        "https://svc.example/docs",
    ]


def test_sample_domain_pages_pool_of_one_skips_links(monkeypatch: pytest.MonkeyPatch) -> None:
    """With max_pages=1, returns only the landing page without following any links."""
    monkeypatch.setattr("app.modules.crawler.robots.is_allowed", lambda _url: True)
    driver = _FakeDriver(
        {
            "https://svc.example": _result(
                "https://svc.example", "landing text", "<a href='/x'>x</a>"
            )
        }
    )
    pages, _ = _sample_domain_pages(driver, "https://svc.example", "svc.example", max_pages=1)
    assert pages == [("https://svc.example", "landing text", ())]


def test_sample_domain_pages_skips_a_link_that_fails_to_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skips a linked page whose fetch raises, continuing on to the remaining links."""
    monkeypatch.setattr("app.modules.crawler.robots.is_allowed", lambda _url: True)
    landing_html = '<a href="/broken">Broken</a> <a href="/ok">Ok</a>'
    driver = _FakeDriver(
        {
            "https://svc.example": _result("https://svc.example", "landing text", landing_html),
            "https://svc.example/ok": _result("https://svc.example/ok", "ok text"),
            # /broken deliberately absent — scrape_with_fallback raises for it.
        }
    )
    pages, _ = _sample_domain_pages(driver, "https://svc.example", "svc.example", max_pages=3)
    assert [u for u, _, _ in pages] == ["https://svc.example", "https://svc.example/ok"]


def test_sample_domain_pages_carries_outbound_external_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Carries each page's outbound external links alongside its text for later scoring."""
    monkeypatch.setattr("app.modules.crawler.robots.is_allowed", lambda _url: True)
    # The quantoz.com/EURQ case: a page's own text never says "algorand", but
    # it links straight to an Algorand explorer entry — that link must travel
    # with the page so classify_pending_domains can feed it to score_page.
    landing_html = (
        '<a href="/product">Product</a> '
        '<a href="https://allo.info/asset/123/token">View on explorer</a>'
    )
    product_html = '<a href="https://etherscan.io/address/0x1">Ethereum</a>'
    driver = _FakeDriver(
        {
            "https://svc.example": _result("https://svc.example", "landing text", landing_html),
            "https://svc.example/product": _result(
                "https://svc.example/product", "product text", product_html
            ),
        }
    )
    pages, _ = _sample_domain_pages(driver, "https://svc.example", "svc.example", max_pages=2)
    by_url = {u: links for u, _, links in pages}
    assert by_url["https://svc.example"] == ("https://allo.info/asset/123/token",)
    assert by_url["https://svc.example/product"] == ("https://etherscan.io/address/0x1",)


def test_sample_domain_pages_uses_cache_instead_of_fetching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Serves cached page bodies for landing + linked pages without touching the driver."""
    import app.modules.crawler.tasks.url_queue_tasks as uq

    # Landing page is a cache hit for both itself and its one link — the
    # driver must never be asked to fetch either.
    def fake_cache(url: str) -> str | None:
        return {
            "https://svc.example": "landing text",
            "https://svc.example/product": "product text",
        }.get(url)

    monkeypatch.setattr(uq, "_cached_page_body", fake_cache)
    monkeypatch.setattr(
        uq, "_cached_domain_urls", lambda _domain, _limit=20: ["https://svc.example/product"]
    )
    driver = _FakeDriver({})  # raises on any fetch — proves nothing hit the network
    pages, _ = _sample_domain_pages(driver, "https://svc.example", "svc.example", max_pages=3)
    assert [(u, t) for u, t, _ in pages] == [
        ("https://svc.example", "landing text"),
        ("https://svc.example/product", "product text"),
    ]


def test_sample_domain_pages_cache_hit_has_no_outbound_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cache-hit page carries no outbound links (only live HTML fetches parse links)."""
    import app.modules.crawler.tasks.url_queue_tasks as uq

    monkeypatch.setattr(uq, "_cached_page_body", lambda _url: "landing text")
    driver = _FakeDriver({})
    pages, _ = _sample_domain_pages(driver, "https://svc.example", "svc.example", max_pages=1)
    assert pages == [("https://svc.example", "landing text", ())]


def test_sample_domain_pages_cache_miss_falls_back_to_live_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falls back to a live driver fetch when the page body isn't cached."""
    import app.modules.crawler.tasks.url_queue_tasks as uq

    monkeypatch.setattr("app.modules.crawler.robots.is_allowed", lambda _url: True)
    monkeypatch.setattr(uq, "_cached_page_body", lambda _url: None)
    driver = _FakeDriver(
        {"https://svc.example": _result("https://svc.example", "live text", "<a href='/x'>x</a>")}
    )
    pages, _ = _sample_domain_pages(driver, "https://svc.example", "svc.example", max_pages=1)
    assert pages == [("https://svc.example", "live text", ())]


# --------------------------------------------------------------------------- #
# _sample_domain_pages / _deep_crawl_for_relevance -- robots/budget/cooldown
# gate routing (W3-B, root-caused 2026-08-26: neither function checked the
# domain's crawl budget or cooldown before fetching, and _sample_domain_pages
# had no robots.txt check at all).
# --------------------------------------------------------------------------- #


def test_sample_domain_pages_returns_empty_when_budget_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A domain already over its crawl-page budget gets no fetches at all, not even the landing page."""
    monkeypatch.setattr(
        "app.modules.crawler.domain_tracker.domain_crawl_budget_exhausted", lambda _d: True
    )

    def _boom(_url: str, _source_id: str) -> Never:
        raise AssertionError("must not fetch anything once the domain's budget is exhausted")

    driver = SimpleNamespace(scrape_with_fallback=_boom)
    pages, same_domain_link_count = _sample_domain_pages(
        driver, "https://svc.example", "svc.example", max_pages=3
    )
    assert pages == []
    assert same_domain_link_count == 0


def test_sample_domain_pages_returns_empty_when_domain_in_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A domain currently in its compose/diversity cooldown gets no fetches at all."""
    monkeypatch.setattr("app.modules.crawler.domain_tracker.domain_in_cooldown", lambda _d: True)

    def _boom(_url: str, _source_id: str) -> Never:
        raise AssertionError("must not fetch anything while the domain is in cooldown")

    driver = SimpleNamespace(scrape_with_fallback=_boom)
    pages, same_domain_link_count = _sample_domain_pages(
        driver, "https://svc.example", "svc.example", max_pages=3
    )
    assert pages == []
    assert same_domain_link_count == 0


def test_sample_domain_pages_skips_a_robots_disallowed_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A same-domain link robots.txt disallows is skipped like a fetch failure, while an allowed sibling link still gets fetched."""

    def _fake_is_allowed(url: str) -> bool:
        return url != "https://svc.example/disallowed"

    monkeypatch.setattr("app.modules.crawler.robots.is_allowed", _fake_is_allowed)
    landing_html = '<a href="/disallowed">No</a> <a href="/ok">Ok</a>'
    driver = _FakeDriver(
        {
            "https://svc.example": _result("https://svc.example", "landing text", landing_html),
            "https://svc.example/ok": _result("https://svc.example/ok", "ok text"),
            # /disallowed deliberately absent -- must never be fetched.
        }
    )
    pages, _ = _sample_domain_pages(driver, "https://svc.example", "svc.example", max_pages=3)
    assert [u for u, _, _ in pages] == ["https://svc.example", "https://svc.example/ok"]


def test_sample_domain_pages_robots_disallowed_landing_page_yields_empty_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A robots-disallowed landing page still returns its one placeholder entry (best-effort, like any other unfetchable page) rather than raising or fetching anyway."""
    monkeypatch.setattr("app.modules.crawler.robots.is_allowed", lambda _url: False)

    def _boom(_url: str, _source_id: str) -> Never:
        raise AssertionError("must not fetch a robots-disallowed URL")

    driver = SimpleNamespace(scrape_with_fallback=_boom)
    pages, same_domain_link_count = _sample_domain_pages(
        driver, "https://svc.example", "svc.example", max_pages=3
    )
    assert pages == [("https://svc.example", "", ())]
    assert same_domain_link_count == 0


def test_deep_crawl_for_relevance_stops_when_budget_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The random-order deep crawl stops (non-exhaustive) the moment the domain's crawl budget trips, instead of continuing to burn fetches against it."""
    import app.modules.crawler.tasks.url_queue_tasks as uq

    monkeypatch.setattr("app.modules.crawler.robots.is_allowed", lambda _url: True)
    monkeypatch.setattr(
        "app.modules.crawler.domain_tracker.domain_crawl_budget_exhausted", lambda _d: True
    )

    def _boom(_url: str, _source_id: str) -> Never:
        raise AssertionError("must not fetch anything once the domain's budget is exhausted")

    monkeypatch.setattr(uq, "WebCrawlerDriver", lambda: SimpleNamespace(scrape_with_fallback=_boom))

    found, fetched, exhaustive, link_count = _deep_crawl_for_relevance(
        domain="svc.example", landing_url="https://svc.example", max_pages=200
    )
    assert found is None
    assert fetched == 0
    assert exhaustive is False  # a budget trip is a limit, not proof the site has nothing
    assert link_count == 0


def test_deep_crawl_for_relevance_stops_when_domain_in_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The random-order deep crawl stops the moment the domain enters cooldown, same as a budget trip."""
    import app.modules.crawler.tasks.url_queue_tasks as uq

    monkeypatch.setattr("app.modules.crawler.robots.is_allowed", lambda _url: True)
    monkeypatch.setattr("app.modules.crawler.domain_tracker.domain_in_cooldown", lambda _d: True)

    def _boom(_url: str, _source_id: str) -> Never:
        raise AssertionError("must not fetch anything while the domain is in cooldown")

    monkeypatch.setattr(uq, "WebCrawlerDriver", lambda: SimpleNamespace(scrape_with_fallback=_boom))

    found, fetched, exhaustive, _link_count = _deep_crawl_for_relevance(
        domain="svc.example", landing_url="https://svc.example", max_pages=200
    )
    assert found is None
    assert fetched == 0
    assert exhaustive is False


def _patch_common(
    monkeypatch: pytest.MonkeyPatch,
    driver: _FakeDriver,
    *,
    service_calls: list[tuple] | None = None,
) -> list[tuple]:
    import app.modules.crawler.tasks.url_queue_tasks as uq

    monkeypatch.setattr(uq, "WebCrawlerDriver", lambda: driver)
    monkeypatch.setattr("app.modules.crawler.robots.is_allowed", lambda _url: True)
    calls: list[tuple] = []
    monkeypatch.setattr(
        "app.modules.crawler.domain_tracker.update_domain_status",
        lambda domain, **kw: calls.append((domain, kw)),
    )
    # ensure_monitored_service does real Cassandra/session work with no
    # network available in tests — stub it out (and, when the caller wants
    # to assert on it, record the calls).
    sink = service_calls if service_calls is not None else []
    monkeypatch.setattr(
        "app.modules.crawler.domain_tracker.ensure_monitored_service",
        lambda domain, **kw: sink.append((domain, kw)) or True,
    )
    return calls


def test_deep_classify_domain_celery_name_resolves_to_the_real_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Celery task registered under "app.tasks.crawler.deep_classify_domain" must be the real deep_classify_domain (the one that writes the verdict back), not the internal crawl-only helper.

    Regression test for a real bug (root-caused 2026-08-25): the
    ``@celery_app.task(name=...)`` decorator used to sit on
    ``_deep_crawl_for_relevance`` instead, so every escalation dispatched via
    ``_classify_and_store_domain``'s ``send_task("app.tasks.crawler.
    deep_classify_domain", kwargs={"domain": ..., "seed_url": ..., "max_pages":
    ...})`` would resolve to a function that doesn't even accept ``seed_url``
    (it takes ``landing_url``) and never calls update_domain_status — every
    real escalation silently failed at the worker, and the domain stayed
    stuck at frontier_status="pending" with deep_classify_queued="true"
    forever (see _classify_and_store_domain's no-op-when-already-queued
    check). Importing ``deep_classify_domain`` directly (as the other tests
    in this file do) can't catch this — it has to go through the registered
    task name the way production dispatch actually does.
    """
    from app.celery_app import celery_app

    driver = _FakeDriver(
        {
            "https://svc.example": _result(
                "https://svc.example", "algorand mainnet testnet asa" * 5, ""
            ),
        }
    )
    service_calls: list[tuple] = []
    calls = _patch_common(monkeypatch, driver, service_calls=service_calls)

    task = celery_app.tasks["app.tasks.crawler.deep_classify_domain"]
    # The broken registration would raise TypeError here (unexpected keyword
    # argument 'seed_url') before ever reaching the assertions below.
    out = task(domain="svc.example", seed_url="https://svc.example", max_pages=200)

    assert out["verdict"] == "approved"
    assert calls, (
        "the real deep_classify_domain must write the verdict back via update_domain_status"
    )
    assert calls[0][1]["frontier_status_override"] == "approved"
    assert service_calls == [("svc.example", {"scrape_url": "https://svc.example"})]


def test_classify_pending_domains_celery_name_resolves_to_the_real_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Celery task registered under "app.tasks.crawler.classify_pending_domains" must be the real orchestrator, not the small _pending_domains_to_classify fetch-helper (same misplaced-decorator bug class as deep_classify_domain, root-caused 2026-08-25).

    Not currently reachable in production (nothing dispatches this task by
    name today — the daily beat calls the real function directly, in-process,
    from reevaluate_pending_domains), but a live trap for the next caller
    that does. The broken registration takes ``(session, limit)`` positionally
    and knows nothing of ``dry_run``/``auto_reject``, so calling it the way a
    caller would reasonably expect (matching classify_pending_domains'
    documented kwargs) would raise TypeError.
    """
    import app.modules.crawler.tasks.url_queue_tasks as uq
    from app.celery_app import celery_app

    monkeypatch.setattr(
        "app.core.cassandra.get_cassandra_session",
        lambda: SimpleNamespace(execute=lambda _stmt, _params: [], prepare=lambda cql: cql),
    )
    monkeypatch.setattr(uq, "WebCrawlerDriver", lambda: object())

    task = celery_app.tasks["app.tasks.crawler.classify_pending_domains"]
    out = task(limit=10, dry_run=True, auto_reject=False)

    # Only the real classify_pending_domains returns this shape; the
    # mis-registered helper returns a bare list of (domain, meta) tuples.
    assert out["status"] == "ok"
    assert out["dry_run"] is True
    assert "scored" in out
    assert "rejected" in out


def test_deep_classify_domain_approves_on_first_relevant_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Approves a domain once the crawl walks deep enough to find a relevant page."""
    # No links anywhere on the landing page — the ONLY way this resolves is by
    # the frontier discovering /eurq-usdq from a later page and it clearing
    # the bar there, proving the crawl actually walks beyond page one.
    driver = _FakeDriver(
        {
            "https://quantoz.example": _result(
                "https://quantoz.example",
                "digital euros, landing page" * 10,
                '<a href="/products">Products</a>',
            ),
            "https://quantoz.example/products": _result(
                "https://quantoz.example/products",
                "our products" * 10,
                '<a href="/products/eurq-usdq">EURQ</a>',
            ),
            "https://quantoz.example/products/eurq-usdq": _result(
                "https://quantoz.example/products/eurq-usdq",
                "digital euro stablecoin" * 10,
                '<a href="https://allo.info/asset/123/token">Explorer</a>',
            ),
        }
    )
    service_calls: list[tuple] = []
    calls = _patch_common(monkeypatch, driver, service_calls=service_calls)
    out = deep_classify_domain(domain="quantoz.example", max_pages=200)
    assert out["verdict"] == "approved"
    assert out["found_at"] == "https://quantoz.example/products/eurq-usdq"
    assert calls[0][1]["is_relevant"] is True
    assert calls[0][1]["frontier_status_override"] == "approved"
    # relevance_score must NOT be passed -- the 0-1 verdict belongs only in
    # metadata's content_relevance, or it clobbers the domain's keyword-scale
    # score with an incompatible number (root-caused 2026-08-25).
    assert "relevance_score" not in calls[0][1]
    assert calls[0][1]["metadata"]["content_relevance"] == f"{out['score']:.3f}"
    # An automated approve must also register the monitored source (mirrors
    # the discovery-time auto-approve in link_extractor.py) — otherwise this
    # domain gets crawled into the research corpus forever without ever
    # producing a publish candidate.
    assert service_calls == [
        ("quantoz.example", {"scrape_url": "https://quantoz.example/products/eurq-usdq"})
    ]


def test_deep_classify_domain_enqueues_the_found_page_into_the_crawl_frontier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test (root-caused 2026-08-26, ulam.io): when ensure_monitored_service is a no-op because a curated ecosystem-directory sync already registered this domain's service pointed at the bare landing page, the confirmed-relevant page this task found must still be queued into the ordinary crawl frontier -- else it is fetched once here, recorded only as a metadata pointer, and never harvested into `crawled_pages_by_domain`, so it can never enter the service's aggregated context (service_context.build_service_context) or feed the priority sweep.

    Mirrors ulam.io exactly: ecosystem_sync's `_ingest_domain` had already
    called `ensure_monitored_service(domain, scrape_url="https://ulam.io/")`
    before this task ran, so this task's own
    `ensure_monitored_service(domain, scrape_url=found_url)` returned False
    (owner already set) -- yet `ulam.io/case-studies/pact-fi` (the page that
    actually proves the domain's relevance) was never queued anywhere and
    stayed permanently unharvested.
    """
    import app.modules.crawler.tasks.url_queue_tasks as uq

    # The relevant page must be reachable via the frontier's own link
    # discovery (not the landing page itself), same as a real deep crawl.
    driver = _FakeDriver(
        {
            "https://svc.example": _result(
                "https://svc.example",
                "landing page, nothing chain-related" * 5,
                '<a href="/case-studies/pact-fi">Pact case study</a>',
            ),
            "https://svc.example/case-studies/pact-fi": _result(
                "https://svc.example/case-studies/pact-fi",
                "algorand mainnet asa testnet" * 5,
                "",
            ),
        }
    )
    # Simulate ecosystem_sync having already claimed this domain for a
    # different (bare landing page) URL -- ensure_monitored_service is a
    # real no-op in that case, but _patch_common's stub always returns True,
    # so return False explicitly here to match production.
    service_calls: list[tuple] = []
    monkeypatch.setattr(uq, "WebCrawlerDriver", lambda: driver)
    monkeypatch.setattr("app.modules.crawler.robots.is_allowed", lambda _url: True)
    monkeypatch.setattr(
        "app.modules.crawler.domain_tracker.update_domain_status", lambda _domain, **_kw: None
    )
    monkeypatch.setattr(
        "app.modules.crawler.domain_tracker.ensure_monitored_service",
        lambda domain, **kw: service_calls.append((domain, kw)) or False,
    )
    enqueue_calls: list[tuple] = []
    monkeypatch.setattr(
        "app.modules.crawler.url_queue.enqueue_url",
        lambda url, **kw: enqueue_calls.append((url, kw)) or ("qid", True),
    )

    out = deep_classify_domain(domain="svc.example", max_pages=200)

    assert out["verdict"] == "approved"
    assert out["found_at"] == "https://svc.example/case-studies/pact-fi"
    # ensure_monitored_service really was a no-op (production shape).
    assert service_calls == [
        ("svc.example", {"scrape_url": "https://svc.example/case-studies/pact-fi"})
    ]
    assert enqueue_calls == [
        (
            "https://svc.example/case-studies/pact-fi",
            {"source": "deep_classify_relevant_page", "priority": 20},
        )
    ]


def test_deep_classify_domain_rejects_when_nothing_found_within_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Marks a domain dead_end and exhaustive when the frontier runs dry with no relevant page."""
    driver = _FakeDriver(
        {
            "https://offtopic.example": _result(
                "https://offtopic.example", "we sell shoes online" * 10, ""
            ),
        }
    )
    service_calls: list[tuple] = []
    calls = _patch_common(monkeypatch, driver, service_calls=service_calls)
    out = deep_classify_domain(domain="offtopic.example", max_pages=200)
    assert out["verdict"] == "dead_end"
    assert out["pages_fetched"] == 1
    # No links at all on the one page fetched — frontier ran dry naturally,
    # well under the budget, so this is the exhaustive/conclusive case.
    assert out["exhaustive"] is True
    assert calls[0][1]["is_relevant"] is False
    assert calls[0][1]["frontier_status_override"] == "dead_end"
    assert calls[0][1]["metadata"]["deep_classify_exhaustive"] == "true"
    assert "relevance_score" not in calls[0][1]
    # A reject must never register a monitored service.
    assert service_calls == []


def test_deep_classify_domain_stops_at_max_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stops at exactly max_pages and marks the result non-exhaustive when the budget runs out."""
    # A chain of pages each linking only to the next, none ever relevant —
    # the crawl must give up at exactly max_pages, not run away forever.
    pages = {}
    for i in range(10):
        url = f"https://big.example/p{i}"
        next_html = f'<a href="/p{i + 1}">next</a>' if i < 9 else ""
        pages[url] = _result(url, "generic content, nothing chain-related" * 10, next_html)
    pages["https://big.example"] = _result(
        "https://big.example", "landing, nothing here" * 10, '<a href="/p0">start</a>'
    )
    driver = _FakeDriver(pages)
    calls = _patch_common(monkeypatch, driver)
    out = deep_classify_domain(domain="big.example", max_pages=5)
    assert out["verdict"] == "dead_end"
    assert out["pages_fetched"] == 5
    # p4 was queued (from p3's link) but never reached — budget ran out with
    # pages still unexplored, so this is the non-exhaustive/budget-limited case.
    assert out["exhaustive"] is False
    assert calls[0][1]["frontier_status_override"] == "dead_end"
    assert calls[0][1]["metadata"]["deep_classify_exhaustive"] == "false"


# --------------------------------------------------------------------------- #
# _deep_crawl_for_relevance -- wall-clock time-budget stop (2026-08-28 perf
# audit): a genuinely slow/large domain must stop itself, with margin, before
# this task's own celery hard task_time_limit SIGKILLs it mid-crawl and
# discards all the work done so far (nothing is stored until the verdict at
# the very end of _run_deep_classify).
# --------------------------------------------------------------------------- #


def _make_chain_pages(domain: str, count: int) -> dict[str, ScrapeResult]:
    """A chain of `count` same-domain pages, each linking only to the next, none of them ever relevant -- so the crawl only ever stops via max_pages or the time budget, never by finding something or by the frontier running dry early."""
    pages: dict[str, ScrapeResult] = {}
    for i in range(count):
        url = f"https://{domain}/p{i}"
        next_html = f'<a href="/p{i + 1}">next</a>' if i < count - 1 else ""
        pages[url] = _result(url, "generic content, nothing chain-related" * 10, next_html)
    pages[f"https://{domain}"] = _result(
        f"https://{domain}", "landing, nothing here" * 10, '<a href="/p0">start</a>'
    )
    return pages


def test_deep_classify_time_budget_derives_from_task_time_limit_with_margin() -> None:
    """The default time budget is celery's own hard task_time_limit minus the documented safety margin, mirroring reap_stale_deep_classify_flags' own reasoning for reading task_time_limit directly instead of a new config setting."""
    from app.celery_app import celery_app
    from app.modules.crawler.tasks import url_queue_tasks as uq

    expected = celery_app.conf.task_time_limit - uq._DEEP_CLASSIFY_TIME_BUDGET_MARGIN_SECONDS
    assert _deep_classify_time_budget_seconds() == expected
    # And it must leave real margin below the hard limit, not shave it to zero.
    assert _deep_classify_time_budget_seconds() < celery_app.conf.task_time_limit


def test_deep_crawl_for_relevance_stops_within_time_budget_on_a_slow_crawl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow/large domain (each page fetch simulated as costing real wall-clock time, e.g. repeatedly hitting the Playwright SPA fallback) stops itself once the time budget's deadline is reached -- with margin, well short of max_pages -- instead of running until this task's celery hard time limit SIGKILLs it."""
    import app.modules.crawler.tasks.url_queue_tasks as uq

    monkeypatch.setattr("app.modules.crawler.robots.is_allowed", lambda _url: True)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)  # no real sleeping in tests

    # Fake monotonic clock advanced only by the fetch itself (mirrors a real
    # per-page cost), not by a fixed number of loop ticks.
    clock = {"now": 0.0}
    monkeypatch.setattr("time.monotonic", lambda: clock["now"])

    pages = _make_chain_pages("slow.example", 20)

    def slow_scrape(url: str, _source_id: str) -> ScrapeResult:
        clock["now"] += 100.0  # each page fetch "takes" 100s of simulated time
        return pages[url]

    driver = SimpleNamespace(scrape_with_fallback=slow_scrape)
    monkeypatch.setattr(uq, "WebCrawlerDriver", lambda: driver)

    found, fetched, exhaustive, _link_count = _deep_crawl_for_relevance(
        domain="slow.example",
        landing_url="https://slow.example",
        max_pages=200,  # far more than the time budget will allow it to reach
        time_budget_seconds=250.0,  # ~2 fetches' worth before the deadline trips
    )

    assert found is None
    assert 1 <= fetched < 200  # stopped well short of max_pages, but did real work
    # Stopped by the deadline with the frontier still non-empty -- the exact
    # same "budget hit, not exhaustive" shape a max_pages stop produces, not
    # a silently-empty or falsely-conclusive result.
    assert exhaustive is False


def test_deep_crawl_for_relevance_time_budget_does_not_cut_off_a_fast_crawl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A small domain that finishes well within the time budget is completely unaffected -- it still runs to a normal, exhaustive verdict, not an early time-based cutoff."""
    import app.modules.crawler.tasks.url_queue_tasks as uq

    monkeypatch.setattr("app.modules.crawler.robots.is_allowed", lambda _url: True)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    clock = {"now": 0.0}
    monkeypatch.setattr("time.monotonic", lambda: clock["now"])

    # Only 3 pages total, each fetch "costs" 1s -- nowhere near a 250s budget.
    pages = _make_chain_pages("fast.example", 2)

    def fast_scrape(url: str, _source_id: str) -> ScrapeResult:
        clock["now"] += 1.0
        return pages[url]

    driver = SimpleNamespace(scrape_with_fallback=fast_scrape)
    monkeypatch.setattr(uq, "WebCrawlerDriver", lambda: driver)

    found, fetched, exhaustive, _link_count = _deep_crawl_for_relevance(
        domain="fast.example",
        landing_url="https://fast.example",
        max_pages=200,
        time_budget_seconds=250.0,
    )

    assert found is None
    assert fetched == 3  # landing + p0 + p1, then the frontier ran dry naturally
    assert exhaustive is True  # frontier exhaustion, not a time-budget cutoff


def test_deep_classify_domain_produces_a_sensible_verdict_when_stopped_by_time_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: when the crawl loop stops itself on the time budget (using the real default-derived budget, not an injected override), deep_classify_domain still writes a normal, honest dead_end/non-exhaustive verdict -- not a silently empty or wrong one -- exactly like the existing max_pages-limited case."""
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    clock = {"now": 0.0}
    monkeypatch.setattr("time.monotonic", lambda: clock["now"])
    # Each fetch consumes 1/16th of the default budget, so the loop trips the
    # deadline partway through a 200-page max_pages ceiling it would otherwise
    # have run to (proving the fix, not just the override parameter, is what
    # stops it).
    per_fetch_seconds = _deep_classify_time_budget_seconds() / 16

    pages = _make_chain_pages("slowreal.example", 200)

    def slow_scrape(url: str, _source_id: str) -> ScrapeResult:
        clock["now"] += per_fetch_seconds
        return pages[url]

    driver = SimpleNamespace(scrape_with_fallback=slow_scrape)
    calls = _patch_common(monkeypatch, driver)

    out = deep_classify_domain(domain="slowreal.example", max_pages=200)

    assert out["verdict"] == "dead_end"
    assert isinstance(out["pages_fetched"], int)
    assert 1 <= out["pages_fetched"] < 200
    assert out["exhaustive"] is False
    assert calls[0][1]["frontier_status_override"] == "dead_end"
    assert calls[0][1]["metadata"]["deep_classify_exhaustive"] == "false"
    # The note must still be an honest, non-empty explanation, not blank.
    assert calls[0][1]["metadata"]["note"]


def test_classify_pending_domains_escalates_instead_of_rejecting_outright(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Escalates a below-threshold domain to deep_classify_domain instead of hard-rejecting it."""
    # A domain the shallow sample scores below the reject threshold must be
    # escalated to deep_classify_domain, not rejected outright — the shallow
    # sample's reject is a lead, not a verdict (quantoz.com/EURQ, 2026-07-21).
    import app.modules.crawler.tasks.url_queue_tasks as uq

    monkeypatch.setattr("app.core.config.FRONTIER_DEEP_CLASSIFY_ENABLED", True)
    monkeypatch.setattr("app.core.config.FRONTIER_DEEP_CLASSIFY_MAX_PAGES", 200)

    rows = [
        SimpleNamespace(
            domain="offtopic.example",
            frontier_status="pending",
            metadata={},
        ),
    ]
    executed = []
    monkeypatch.setattr(
        "app.core.cassandra.get_cassandra_session",
        lambda: SimpleNamespace(
            execute=lambda stmt, params: executed.append((stmt, params)) or rows,
            prepare=lambda cql: cql,
        ),
    )
    monkeypatch.setattr(
        uq,
        "_sample_domain_pages",
        lambda _driver, url, _domain, _n: ([(url, "off-topic content" * 10, ())], 0),
    )
    monkeypatch.setattr(uq, "WebCrawlerDriver", lambda: object())

    sent = []
    monkeypatch.setattr(
        "app.celery_app.celery_app.send_task",
        lambda name, kwargs=None, queue=None: sent.append((name, kwargs, queue)),
    )
    rejected_calls = []
    monkeypatch.setattr(
        "app.modules.crawler.domain_tracker.update_domain_status",
        lambda d, **kw: rejected_calls.append((d, kw)),
    )
    monkeypatch.setattr("app.modules.crawler.domain_tracker.is_protected_domain", lambda _d: False)

    out = classify_pending_domains(limit=10, dry_run=False, auto_reject=True)

    assert out["rejected"] == 0
    assert out["escalated_to_deep_classify"] == 1
    assert rejected_calls == []  # never hard-rejected — only the escalation path ran
    assert len(sent) == 1
    name, kwargs, queue = sent[0]
    assert name == "app.tasks.crawler.deep_classify_domain"
    assert kwargs["domain"] == "offtopic.example"
    assert kwargs["max_pages"] == 200
    assert queue == "scrape"
    # The UPDATE_METADATA call must mark it queued and leave it pending, not
    # dead_end, while the deep check is in flight.
    update_calls = [p for _, p in executed if isinstance(p, tuple) and len(p) == 2]
    new_meta = update_calls[-1][0]
    assert new_meta["deep_classify_queued"] == "true"
    assert new_meta["frontier_status"] == "pending"


def test_classify_pending_domains_rejects_outright_when_deep_classify_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falls back to an outright reject when deep-classify escalation is disabled."""
    import app.modules.crawler.tasks.url_queue_tasks as uq

    monkeypatch.setattr("app.core.config.FRONTIER_DEEP_CLASSIFY_ENABLED", False)

    rows = [SimpleNamespace(domain="offtopic.example", frontier_status="pending", metadata={})]
    monkeypatch.setattr(
        "app.core.cassandra.get_cassandra_session",
        lambda: SimpleNamespace(execute=lambda _stmt, _params: rows, prepare=lambda cql: cql),
    )
    monkeypatch.setattr(
        uq,
        "_sample_domain_pages",
        lambda _driver, url, _domain, _n: ([(url, "off-topic content" * 10, ())], 0),
    )
    monkeypatch.setattr(uq, "WebCrawlerDriver", lambda: object())
    monkeypatch.setattr("app.modules.crawler.domain_tracker.is_protected_domain", lambda _d: False)
    rejected_calls = []
    monkeypatch.setattr(
        "app.modules.crawler.domain_tracker.update_domain_status",
        lambda d, **kw: rejected_calls.append((d, kw)),
    )

    out = classify_pending_domains(limit=10, dry_run=False, auto_reject=True)

    assert out["rejected"] == 1
    assert out["escalated_to_deep_classify"] == 0
    assert rejected_calls[0][1]["frontier_status_override"] == "dead_end"
    assert "relevance_score" not in rejected_calls[0][1]
    assert rejected_calls[0][1]["metadata"]["content_relevance"] is not None


def test_classify_pending_domains_marks_fetch_errors_so_they_stop_recurring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Marks a domain whose sample fetch raised as a fetch_error instead of reject/escalate, so it stops recurring."""
    # Root cause 2026-07-22: a domain whose sample fetch raises (DNS failure,
    # 5xx, dead site) never got a metadata write, so it stayed "unscored"
    # forever and got retried on every single future batch. It must be
    # marked (without going through reject/escalate — a fetch failure isn't
    # an off-topic verdict) so it stops recurring.
    import app.modules.crawler.tasks.url_queue_tasks as uq

    rows = [SimpleNamespace(domain="dead.example", frontier_status="pending", metadata={})]
    executed = []
    monkeypatch.setattr(
        "app.core.cassandra.get_cassandra_session",
        lambda: SimpleNamespace(
            execute=lambda stmt, params: executed.append((stmt, params)) or rows,
            prepare=lambda cql: cql,
        ),
    )

    def _raise(_driver: Any, _url: str, _domain: str, _n: int) -> Never:  # noqa: ANN401 -- duck-typed fake driver
        raise ValueError("dns resolution failed")

    monkeypatch.setattr(uq, "_sample_domain_pages", _raise)
    monkeypatch.setattr(uq, "WebCrawlerDriver", lambda: object())
    sent = []
    monkeypatch.setattr(
        "app.celery_app.celery_app.send_task",
        lambda name, kwargs=None, queue=None: sent.append((name, kwargs, queue)),
    )
    rejected_calls = []
    monkeypatch.setattr(
        "app.modules.crawler.domain_tracker.update_domain_status",
        lambda d, **kw: rejected_calls.append((d, kw)),
    )

    out = classify_pending_domains(limit=10, dry_run=False, auto_reject=True)

    assert out["errors"] == 1
    assert out["rejected"] == 0
    assert out["escalated_to_deep_classify"] == 0
    assert sent == []  # a fetch failure must never trigger a deep 200-page crawl
    assert rejected_calls == []
    update_calls = [p for _, p in executed if isinstance(p, tuple) and len(p) == 2]
    new_meta = update_calls[-1][0]
    assert new_meta["content_relevance_reasons"] == "fetch_error"
    assert new_meta["content_relevance"] == "0.000"


def test_classify_pending_domains_marks_unreadable_so_it_stops_recurring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Marks a domain whose sample text is too short as unreadable, so it stops recurring."""
    import app.modules.crawler.tasks.url_queue_tasks as uq

    rows = [SimpleNamespace(domain="thin.example", frontier_status="pending", metadata={})]
    executed = []
    monkeypatch.setattr(
        "app.core.cassandra.get_cassandra_session",
        lambda: SimpleNamespace(
            execute=lambda stmt, params: executed.append((stmt, params)) or rows,
            prepare=lambda cql: cql,
        ),
    )
    monkeypatch.setattr(
        uq, "_sample_domain_pages", lambda _driver, url, _domain, _n: ([(url, "too short", ())], 0)
    )
    monkeypatch.setattr(uq, "WebCrawlerDriver", lambda: object())

    out = classify_pending_domains(limit=10, dry_run=False, auto_reject=True)

    assert out["unreadable"] == 1
    update_calls = [p for _, p in executed if isinstance(p, tuple) and len(p) == 2]
    new_meta = update_calls[-1][0]
    assert new_meta["content_relevance_reasons"] == "unreadable"


def _fake_search_result(*hits: dict) -> dict:
    return {"query": "q", "results": list(hits)}


def test_external_corroboration_hits_on_matching_snippet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Finds an external corroboration hit when a single search result's own title/snippet pairs the service with algorand."""
    import app.modules.ai.research_tools as rt

    # The exact real-world pattern: the result's OWN title pairs the service
    # with "algorand" — it doesn't matter which domain it's hosted on.
    monkeypatch.setattr(
        rt,
        "_tool_search_web",
        lambda query, limit=6: _fake_search_result(  # noqa: ARG005 -- name must match the real callee's keyword arg
            {
                "title": "Welcome Sow & Reap to Algorand",
                "url": "https://www.reddit.com/r/AlgorandOfficial/comments/188a1y1/",
                "snippet": "The community welcomes sowandreap.in to the ecosystem.",
            },
        ),
    )
    hit = _external_corroboration("sowandreap.in")
    assert hit is not None
    url, snippet = hit
    assert url == "https://www.reddit.com/r/AlgorandOfficial/comments/188a1y1/"
    assert "algorand" in snippet


def test_external_corroboration_ignores_loose_keyword_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Does not count a hit when the two terms appear across different results, never together."""
    import app.modules.ai.research_tools as rt

    # Both terms appear in the result set, but never together in the SAME
    # result's own title/snippet — must not count as a hit.
    monkeypatch.setattr(
        rt,
        "_tool_search_web",
        lambda query, limit=6: _fake_search_result(  # noqa: ARG005 -- name must match the real callee's keyword arg
            {
                "title": "Algorand price prediction 2030",
                "url": "https://spam.example/a",
                "snippet": "",
            },
            {
                "title": "sowandreap.in raises seed round",
                "url": "https://vc.example/b",
                "snippet": "unrelated to any chain",
            },
        ),
    )
    assert _external_corroboration("sowandreap.in") is None


def test_external_corroboration_fails_closed_on_search_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returns None (no corroboration) when the underlying web search raises."""
    import app.modules.ai.research_tools as rt

    monkeypatch.setattr(
        rt,
        "_tool_search_web",
        lambda query, limit=6: (_ for _ in ()).throw(RuntimeError("boom")),  # noqa: ARG005 -- name must match the real callee's keyword arg
    )
    assert _external_corroboration("sowandreap.in") is None


def test_deep_classify_domain_approves_via_external_corroboration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Approves a chain-silent domain via external corroboration when no page mentions the chain."""
    driver = _FakeDriver(
        {
            "https://chainsilent.example": _result(
                "https://chainsilent.example", "no chain mentions here" * 10, ""
            )
        }
    )
    service_calls: list[tuple] = []
    calls = _patch_common(monkeypatch, driver, service_calls=service_calls)
    import app.modules.crawler.tasks.url_queue_tasks as uq

    monkeypatch.setattr(
        uq,
        "_external_corroboration",
        lambda _domain: (
            "https://reddit.com/r/AlgorandOfficial/x",
            "welcome chainsilent to algorand",
        ),
    )
    out = deep_classify_domain(domain="chainsilent.example", max_pages=200)
    assert out["verdict"] == "approved"
    assert out["via"] == "external_corroboration"
    assert calls[0][1]["is_relevant"] is True
    assert "relevance_score" not in calls[0][1]
    assert (
        calls[0][1]["metadata"]["content_relevance_url"]
        == "https://reddit.com/r/AlgorandOfficial/x"
    )
    # Registers the monitored source at the domain's OWN landing page, not
    # the outside corroborating URL (a Reddit post isn't on this domain).
    assert service_calls == [("chainsilent.example", {"scrape_url": "https://chainsilent.example"})]


# --------------------------------------------------------------------------- #
# deep_classify_domain's try/finally -- deep_classify_queued must never get
# stuck "true" (W3-B, root-caused 2026-08-26: none of the three verdict
# branches ever cleared it, so every completed run stayed permanently
# excluded from _gray_zone_rows'/_classify_and_store_domain's in-flight
# dedup check).
# --------------------------------------------------------------------------- #


def test_deep_classify_domain_clears_queued_flag_on_approve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The approve path's own update_domain_status write is followed by a second call clearing deep_classify_queued -- the try/finally fires after a normal return too, not just on failure."""
    driver = _FakeDriver(
        {
            "https://svc.example": _result(
                "https://svc.example", "algorand mainnet testnet asa" * 5, ""
            ),
        }
    )
    calls = _patch_common(monkeypatch, driver)
    deep_classify_domain(domain="svc.example", max_pages=200)
    assert calls[-1] == ("svc.example", {"metadata": {"deep_classify_queued": "false"}})


def test_deep_classify_domain_clears_queued_flag_on_reject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dead_end reject path also gets the flag cleared afterward."""
    driver = _FakeDriver(
        {
            "https://offtopic.example": _result(
                "https://offtopic.example", "we sell shoes online" * 10, ""
            ),
        }
    )
    calls = _patch_common(monkeypatch, driver)
    deep_classify_domain(domain="offtopic.example", max_pages=200)
    assert calls[-1] == ("offtopic.example", {"metadata": {"deep_classify_queued": "false"}})


def test_deep_classify_domain_clears_queued_flag_even_when_crawl_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hard failure mid-crawl (e.g. a Cassandra hiccup surfacing out of _deep_crawl_for_relevance) still clears the flag on the way out -- the finally block, not the three verdict branches, is what guarantees this."""
    import app.modules.crawler.tasks.url_queue_tasks as uq

    calls: list[tuple] = []
    monkeypatch.setattr(
        "app.modules.crawler.domain_tracker.update_domain_status",
        lambda domain, **kw: calls.append((domain, kw)),
    )

    def _boom(**_kw: object) -> Never:
        raise RuntimeError("boom")

    monkeypatch.setattr(uq, "_deep_crawl_for_relevance", _boom)

    with pytest.raises(RuntimeError, match="boom"):
        deep_classify_domain(domain="svc.example", max_pages=200)

    # No verdict was ever written (the crawl itself raised before either
    # branch), but the finally-only clear must still have run.
    assert calls == [("svc.example", {"metadata": {"deep_classify_queued": "false"}})]


# --------------------------------------------------------------------------- #
# _classify_and_store_domain's escalation path -- deep_classify_queued_at
# stamping (W3-B: needed so reap_stale_deep_classify_flags can judge
# staleness for domains escalated from here, not just gray_zone_
# reconciliation.dispatch_gray_zone_deep_classify's own dispatch path).
# --------------------------------------------------------------------------- #


def test_classify_pending_domains_escalation_stamps_deep_classify_queued_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The escalation write includes a parseable deep_classify_queued_at timestamp, not just the deep_classify_queued flag."""
    from datetime import UTC, datetime

    import app.modules.crawler.tasks.url_queue_tasks as uq

    monkeypatch.setattr("app.core.config.FRONTIER_DEEP_CLASSIFY_ENABLED", True)
    monkeypatch.setattr("app.core.config.FRONTIER_DEEP_CLASSIFY_MAX_PAGES", 200)

    rows = [SimpleNamespace(domain="offtopic.example", frontier_status="pending", metadata={})]
    executed = []
    monkeypatch.setattr(
        "app.core.cassandra.get_cassandra_session",
        lambda: SimpleNamespace(
            execute=lambda stmt, params: executed.append((stmt, params)) or rows,
            prepare=lambda cql: cql,
        ),
    )
    monkeypatch.setattr(
        uq,
        "_sample_domain_pages",
        lambda _driver, url, _domain, _n: ([(url, "off-topic content" * 10, ())], 0),
    )
    monkeypatch.setattr(uq, "WebCrawlerDriver", lambda: object())
    monkeypatch.setattr("app.celery_app.celery_app.send_task", lambda *_a, **_kw: None)
    monkeypatch.setattr("app.modules.crawler.domain_tracker.is_protected_domain", lambda _d: False)

    before = datetime.now(tz=UTC)
    classify_pending_domains(limit=10, dry_run=False, auto_reject=True)
    after = datetime.now(tz=UTC)

    update_calls = [p for _, p in executed if isinstance(p, tuple) and len(p) == 2]
    new_meta = update_calls[-1][0]
    assert new_meta["deep_classify_queued"] == "true"
    stamped = datetime.fromisoformat(new_meta["deep_classify_queued_at"])
    assert before <= stamped <= after


# --------------------------------------------------------------------------- #
# reap_stale_deep_classify_flags -- the sweep that catches what the try/
# finally above structurally can't (a hard SIGKILL past task_time_limit).
# --------------------------------------------------------------------------- #


def test_reap_stale_deep_classify_flags_clears_only_rows_past_the_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clears deep_classify_queued on rows stamped well before the cutoff, leaves a recently-queued row and a row with no timestamp untouched, and counts the no-timestamp row separately rather than guessing at it."""
    from datetime import UTC, datetime, timedelta

    from app.core.statements import DomainTrackingStmts

    now = datetime.now(tz=UTC)
    stale_at = (now - timedelta(seconds=10_000)).isoformat()
    fresh_at = (now - timedelta(seconds=5)).isoformat()
    rows = [
        SimpleNamespace(
            domain="stale.example",
            metadata={"deep_classify_queued": "true", "deep_classify_queued_at": stale_at},
        ),
        SimpleNamespace(
            domain="fresh.example",
            metadata={"deep_classify_queued": "true", "deep_classify_queued_at": fresh_at},
        ),
        SimpleNamespace(
            domain="no_timestamp.example",
            metadata={"deep_classify_queued": "true"},
        ),
        SimpleNamespace(domain="untouched.example", metadata={}),
    ]
    executed: list[tuple] = []
    monkeypatch.setattr(
        "app.core.cassandra.get_cassandra_session",
        lambda: SimpleNamespace(
            execute=lambda stmt, params: executed.append((stmt, params)) or rows,
            prepare=lambda cql: cql,
        ),
    )

    out = reap_stale_deep_classify_flags(stale_seconds=3600)

    assert out["reaped"] == 1
    assert out["reaped_domains"] == ["stale.example"]
    assert out["skipped_no_timestamp"] == 1
    update_calls = [
        params for stmt, params in executed if stmt == DomainTrackingStmts.UPDATE_METADATA
    ]
    assert len(update_calls) == 1
    new_meta, domain = update_calls[0]
    assert domain == "stale.example"
    assert new_meta["deep_classify_queued"] == "false"


def test_reap_stale_deep_classify_flags_default_threshold_derives_from_task_time_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no explicit stale_seconds, the default threshold is a generous multiple of the celery-wide hard task_time_limit, not a magic number -- a row stamped well inside that default window must survive untouched."""
    from datetime import UTC, datetime, timedelta

    from app.celery_app import celery_app

    hard_limit = celery_app.conf.task_time_limit
    # Comfortably inside even a 1x margin above the hard limit -- must never
    # be reaped regardless of the exact multiplier this module chooses.
    recent_at = (datetime.now(tz=UTC) - timedelta(seconds=min(hard_limit - 5, 60))).isoformat()
    rows = [
        SimpleNamespace(
            domain="recent.example",
            metadata={"deep_classify_queued": "true", "deep_classify_queued_at": recent_at},
        ),
    ]
    monkeypatch.setattr(
        "app.core.cassandra.get_cassandra_session",
        lambda: SimpleNamespace(execute=lambda _stmt, _params: rows, prepare=lambda cql: cql),
    )

    out = reap_stale_deep_classify_flags()

    assert out["reaped"] == 0
    assert out["stale_seconds"] > hard_limit


def test_reap_stale_deep_classify_flags_task_resolves_to_the_real_function(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Celery task registered under "app.tasks.crawler.reap_stale_deep_classify_flags" must delegate to the real sweep function, mirroring reclaim_stale_processing_urls_task's own regression test."""
    from app.celery_app import celery_app

    called: list[bool] = []
    monkeypatch.setattr(
        "app.modules.crawler.tasks.url_queue_tasks.reap_stale_deep_classify_flags",
        lambda: called.append(True) or {"status": "ok", "reaped": 0},
    )

    task = celery_app.tasks["app.tasks.crawler.reap_stale_deep_classify_flags"]
    out = task()

    assert out == {"status": "ok", "reaped": 0}
    assert called == [True]


# --------------------------------------------------------------------------- #
# reclassify_gray_zone_domains -- the gray-zone-approved-domain beat task
# --------------------------------------------------------------------------- #


def test_reclassify_gray_zone_domains_is_a_noop_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Off by default: the master flag must gate the task itself too, not just the beat schedule entry, so a direct/manual trigger before the feature is deliberately enabled still no-ops."""
    monkeypatch.setattr("app.core.config.FRONTIER_GRAY_ZONE_RECLASSIFY_ENABLED", False)
    called = []
    monkeypatch.setattr(
        "app.modules.crawler.gray_zone_reconciliation.dispatch_gray_zone_deep_classify",
        lambda **kw: called.append(kw) or {},
    )

    out = reclassify_gray_zone_domains()

    assert out == {"status": "skipped", "reason": "gray_zone_reclassify_disabled"}
    assert called == []


def test_reclassify_gray_zone_domains_dispatches_for_real_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When enabled, calls dispatch_gray_zone_deep_classify with dry_run=False (this is the live beat path, not a dry preview) and the configured default limit."""
    monkeypatch.setattr("app.core.config.FRONTIER_GRAY_ZONE_RECLASSIFY_ENABLED", True)
    monkeypatch.setattr("app.core.config.FRONTIER_GRAY_ZONE_RECLASSIFY_LIMIT", 5)
    called = []
    monkeypatch.setattr(
        "app.modules.crawler.gray_zone_reconciliation.dispatch_gray_zone_deep_classify",
        lambda **kw: called.append(kw) or {"dispatched_count": 5},
    )

    out = reclassify_gray_zone_domains()

    assert out == {"dispatched_count": 5}
    assert called == [{"limit": 5, "dry_run": False}]


def test_reclassify_gray_zone_domains_explicit_limit_overrides_the_configured_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit `limit` kwarg (e.g. a coordinating session running a manual small chunk) wins over the env-configured default."""
    monkeypatch.setattr("app.core.config.FRONTIER_GRAY_ZONE_RECLASSIFY_ENABLED", True)
    monkeypatch.setattr("app.core.config.FRONTIER_GRAY_ZONE_RECLASSIFY_LIMIT", 5)
    called = []
    monkeypatch.setattr(
        "app.modules.crawler.gray_zone_reconciliation.dispatch_gray_zone_deep_classify",
        lambda **kw: called.append(kw) or {},
    )

    reclassify_gray_zone_domains(limit=2)

    assert called == [{"limit": 2, "dry_run": False}]


# --------------------------------------------------------------------------- #
# drain_url_queue single_flight lock + reclaim_stale_processing_urls beat (W3-A)
# --------------------------------------------------------------------------- #


def test_drain_url_queue_is_single_flight_locked(monkeypatch: pytest.MonkeyPatch) -> None:
    """A concurrent drain_url_queue invocation must not race the first run -- a slow tick (real network fetches) overlapping the next beat tick would otherwise drain the same pending pool twice in parallel (2x the work, external sites hit 2x). Must return `already_running` without ever entering the drain body."""
    monkeypatch.setattr("app.core.redis_lock.acquire", lambda _key, _ttl: None)
    import app.modules.crawler.tasks.url_queue_tasks as uq

    def _boom() -> None:
        raise AssertionError("drain body must not run while the lock is held")

    monkeypatch.setattr(uq, "WebCrawlerDriver", _boom)

    result = drain_url_queue()

    assert result == {"status": "already_running", "key": "crawler:drain_url_queue"}


def test_drain_url_queue_lock_ttl_covers_the_hard_task_time_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lock TTL must be at least the task's soft time limit (CLAUDE.md invariant 5). drain_url_queue has no per-task time-limit override, so it inherits the celery-wide task_time_limit -- pinning the lock to that HARD bound (not just the soft one) mirrors drain_to_compose's precedent so the lock always outlives the run even if the soft-limit interrupt doesn't land and celery has to hard-kill the worker."""
    from app.celery_app import celery_app

    seen_ttls: list[int] = []

    def _spy_acquire(_key: str, ttl: int) -> str:
        seen_ttls.append(ttl)
        return "token"

    monkeypatch.setattr("app.core.redis_lock.acquire", _spy_acquire)
    monkeypatch.setattr("app.core.redis_lock.release", lambda _key, _token: None)
    # URL_QUEUE_ENABLED=False short-circuits the drain body immediately after
    # the lock is acquired -- this test only cares about the ttl bound to
    # acquire(), not the drain logic itself. Patched on the tasks module
    # (where `from app.core.config import URL_QUEUE_ENABLED` already bound
    # the name at import time), not app.core.config itself, which the
    # already-bound name never looks back at.
    monkeypatch.setattr("app.modules.crawler.tasks.url_queue_tasks.URL_QUEUE_ENABLED", False)

    drain_url_queue()

    assert seen_ttls == [celery_app.conf.task_time_limit]


def test_reclaim_stale_processing_urls_task_resolves_to_the_real_function(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Celery task registered under "app.tasks.crawler.reclaim_stale_processing_urls" (the name the beat schedule dispatches by) must delegate to url_queue.reclaim_stale_processing_urls -- the real self-healing sweep, not a stray duplicate."""
    from app.celery_app import celery_app

    called: list[bool] = []
    # The task wrapper calls the name bound into its own module's namespace
    # at import time (from ... import reclaim_stale_processing_urls) -- patch
    # that binding, not url_queue's own module attribute, which the wrapper
    # never looks up again after import.
    monkeypatch.setattr(
        "app.modules.crawler.tasks.url_queue_tasks.reclaim_stale_processing_urls",
        lambda: called.append(True) or {"status": "ok", "reclaimed": 0},
    )

    task = celery_app.tasks["app.tasks.crawler.reclaim_stale_processing_urls"]
    out = task()

    assert out == {"status": "ok", "reclaimed": 0}
    assert called == [True]
