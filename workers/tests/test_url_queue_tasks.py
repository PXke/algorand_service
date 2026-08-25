"""Sampling same-domain pages while following outbound links."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Never

import pytest

from app.modules.crawler.tasks.url_queue_tasks import (
    _external_corroboration,
    _sample_domain_pages,
    classify_pending_domains,
    deep_classify_domain,
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


def test_sample_domain_pages_follows_same_domain_links() -> None:
    """Follows same-domain links discovered on the landing page, skipping external ones."""
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


def test_sample_domain_pages_pool_of_one_skips_links() -> None:
    """With max_pages=1, returns only the landing page without following any links."""
    driver = _FakeDriver(
        {
            "https://svc.example": _result(
                "https://svc.example", "landing text", "<a href='/x'>x</a>"
            )
        }
    )
    pages, _ = _sample_domain_pages(driver, "https://svc.example", "svc.example", max_pages=1)
    assert pages == [("https://svc.example", "landing text", ())]


def test_sample_domain_pages_skips_a_link_that_fails_to_fetch() -> None:
    """Skips a linked page whose fetch raises, continuing on to the remaining links."""
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


def test_sample_domain_pages_carries_outbound_external_links() -> None:
    """Carries each page's outbound external links alongside its text for later scoring."""
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

    monkeypatch.setattr(uq, "_cached_page_body", lambda _url: None)
    driver = _FakeDriver(
        {"https://svc.example": _result("https://svc.example", "live text", "<a href='/x'>x</a>")}
    )
    pages, _ = _sample_domain_pages(driver, "https://svc.example", "svc.example", max_pages=1)
    assert pages == [("https://svc.example", "live text", ())]


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
    # An automated approve must also register the monitored source (mirrors
    # the discovery-time auto-approve in link_extractor.py) — otherwise this
    # domain gets crawled into the research corpus forever without ever
    # producing a publish candidate.
    assert service_calls == [
        ("quantoz.example", {"scrape_url": "https://quantoz.example/products/eurq-usdq"})
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
    assert (
        calls[0][1]["metadata"]["content_relevance_url"]
        == "https://reddit.com/r/AlgorandOfficial/x"
    )
    # Registers the monitored source at the domain's OWN landing page, not
    # the outside corroborating URL (a Reddit post isn't on this domain).
    assert service_calls == [("chainsilent.example", {"scrape_url": "https://chainsilent.example"})]
