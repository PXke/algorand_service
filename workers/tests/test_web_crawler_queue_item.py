"""Regression tests for WebCrawlerDriver.scrape_from_queue_item.

Covers the 2026-08-28 fetch/index consistency fix (W3-C):
  - record_domain_crawl must count every successfully FETCHED page against
    the domain's rolling budget, even when the content-quality gate then
    rejects the page (it was previously called only after the gate, so a
    domain that kept returning thin/off-topic pages never used up its
    budget).
  - index_crawled_page.delay must be called with the fetched page's
    outbound_links and published_at, matching what score_page/the search
    index need (previously silently dropped -> published_at always fell
    back to indexing time, and score_page never saw the page's own
    explorer-link signal).
"""

from __future__ import annotations

from typing import Any

import pytest

import app.modules.ai.publish_classifier as publish_classifier
import app.modules.crawler.discovery_store as discovery_store
import app.modules.crawler.domain_tracker as domain_tracker
import app.modules.crawler.robots as robots
import app.modules.crawler.url_queue as url_queue
import app.modules.newspaper.service_sources as service_sources
import app.modules.search.tasks.index_tasks as index_tasks
from app.modules.scraper.core.base import ScrapeResult
from app.modules.scraper.crawlers.web_crawler import WebCrawlerDriver


class _FakeTask:
    """Stand-in for a Celery task object: records .delay(**kwargs) calls."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def delay(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


def _wire_common_gates(monkeypatch: pytest.MonkeyPatch, *, admin_approved: bool) -> None:
    """Patch every function-local import scrape_from_queue_item pulls in.

    Covers _pre_fetch_gate's helpers too, so the pipeline runs with no real
    Redis/Cassandra/network access.
    """
    monkeypatch.setattr(domain_tracker, "domain_from_url", lambda _url: "svc.example")
    monkeypatch.setattr(domain_tracker, "should_recrawl_domain", lambda _domain: True)
    monkeypatch.setattr(domain_tracker, "domain_crawl_budget_exhausted", lambda _domain: False)
    monkeypatch.setattr(domain_tracker, "is_admin_approved_domain", lambda _domain: admin_approved)
    monkeypatch.setattr(robots, "is_allowed", lambda _url: True)
    monkeypatch.setattr(url_queue, "recently_crawled", lambda _url: False)
    monkeypatch.setattr(url_queue, "mark_url_crawled", lambda _url: None)
    monkeypatch.setattr(url_queue, "mark_url_done", lambda *_a, **_kw: None)
    monkeypatch.setattr(publish_classifier, "service_id_for_url", lambda _url: "discovered-web-x")
    # Default: the domain is unclaimed by any real service (the ordinary
    # case for these pre-existing tests) -- see the dedicated venue-owner
    # tests below for the claimed-domain path.
    monkeypatch.setattr(service_sources, "venue_owner_for_url", lambda _url, **_kw: "")


def _item(url: str) -> dict[str, Any]:
    return {"url": url, "queue_id": "q1", "source": "web", "metadata": {"no_follow_links": "true"}}


def test_scrape_from_queue_item_counts_domain_crawl_before_quality_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A low-quality page still counts against the domain's crawl budget.

    Before the fix, record_domain_crawl() was only called AFTER the quality
    gate, so a domain returning nothing but thin/off-topic pages never hit
    its per-domain page budget.
    """
    _wire_common_gates(monkeypatch, admin_approved=False)
    recorded: list[str] = []
    monkeypatch.setattr(
        domain_tracker, "record_domain_crawl", lambda domain: recorded.append(domain)
    )
    monkeypatch.setattr(publish_classifier, "is_content_quality_sufficient", lambda _text: False)
    store_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        discovery_store,
        "store_discovery_content",
        lambda **kw: store_calls.append(kw),
    )
    fake_task = _FakeTask()
    monkeypatch.setattr(index_tasks, "index_crawled_page", fake_task)

    driver = WebCrawlerDriver()
    result = ScrapeResult(
        source_id="discovered-web-x",
        url="https://svc.example/thin",
        title="Thin page",
        text="not enough on-topic content",
        content_hash="h1",
        published_at="2026-08-20T00:00:00Z",
        links=[{"text": "explorer", "url": "https://allo.info/asset/1/token"}],
    )
    monkeypatch.setattr(driver, "scrape_with_fallback", lambda _url, _source_id, **_kw: result)

    outcome = driver.scrape_from_queue_item(_item("https://svc.example/thin"))

    assert outcome["status"] == "skipped"
    assert outcome["reason"] == "low_content_quality"
    # The core regression: the fetch was counted even though the gate rejected it.
    assert recorded == ["svc.example"]
    # And a rejected page never reaches storage or the search index.
    assert store_calls == []
    assert fake_task.calls == []


def test_scrape_from_queue_item_forwards_outbound_links_and_published_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A page that clears the quality gate forwards outbound links + publish date.

    Both land in index_crawled_page.delay(...).
    """
    _wire_common_gates(monkeypatch, admin_approved=False)
    monkeypatch.setattr(domain_tracker, "record_domain_crawl", lambda _domain: None)
    monkeypatch.setattr(publish_classifier, "is_content_quality_sufficient", lambda _text: True)
    monkeypatch.setattr(
        discovery_store,
        "store_discovery_content",
        lambda **_kw: discovery_store.DiscoveryStoreOutcome(
            status="stored", url="https://svc.example/ok"
        ),
    )
    fake_task = _FakeTask()
    monkeypatch.setattr(index_tasks, "index_crawled_page", fake_task)

    driver = WebCrawlerDriver()
    result = ScrapeResult(
        source_id="discovered-web-x",
        url="https://svc.example/ok",
        title="Real page",
        text="algorand ecosystem partner content, plenty of it",
        content_hash="h2",
        published_at="2026-08-21T12:30:00Z",
        links=[
            {"text": "explorer", "url": "https://allo.info/asset/2/token"},
            {"text": "docs", "url": "https://docs.svc.example/"},
        ],
    )
    monkeypatch.setattr(driver, "scrape_with_fallback", lambda _url, _source_id, **_kw: result)

    driver.scrape_from_queue_item(_item("https://svc.example/ok"))

    assert len(fake_task.calls) == 1
    call = fake_task.calls[0]
    assert call["outbound_links"] == (
        "https://allo.info/asset/2/token",
        "https://docs.svc.example/",
    )
    assert call["published_at"] == "2026-08-21T12:30:00Z"
    assert call["url"] == "https://svc.example/ok"
    assert call["service_id"] == "discovered-web-x"


# --------------------------------------------------------------------------- #
# A domain already claimed by a real service_registry entry (2026-08-31,
# algochess.org): a crawled page must use that service_id, not a fresh
# fragmented "discovered-web-<hash>" one.
# --------------------------------------------------------------------------- #
def test_a_page_on_an_already_claimed_domain_uses_the_owning_service_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """venue_owner_for_url overrides the synthetic per-page id when the domain is claimed.

    Before this fix, scrape_from_queue_item never consulted the reverse
    domain->service_id index at all, so every page of an already-registered
    service's site still got its own orphaned discovered-web-* id and never
    contributed to the real service -- exactly what happened to
    algochess.org (registered as "algochess-org" minutes before its crawl,
    but all 17 crawled pages still fragmented).
    """
    _wire_common_gates(monkeypatch, admin_approved=False)
    monkeypatch.setattr(domain_tracker, "record_domain_crawl", lambda _domain: None)
    monkeypatch.setattr(publish_classifier, "is_content_quality_sufficient", lambda _text: True)
    monkeypatch.setattr(
        publish_classifier, "service_id_for_url", lambda _url: "discovered-web-orphan"
    )
    monkeypatch.setattr(
        service_sources,
        "venue_owner_for_url",
        lambda _url, **_kw: "algochess-org",
    )
    monkeypatch.setattr(
        discovery_store,
        "store_discovery_content",
        lambda **_kw: discovery_store.DiscoveryStoreOutcome(
            status="stored", url="https://algochess.org/play"
        ),
    )
    fake_task = _FakeTask()
    monkeypatch.setattr(index_tasks, "index_crawled_page", fake_task)

    driver = WebCrawlerDriver()
    scrape_with_fallback_calls: list[str] = []

    def _fake_scrape(_url: str, source_id: str, **_kw: object) -> ScrapeResult:
        scrape_with_fallback_calls.append(source_id)
        return ScrapeResult(
            source_id=source_id,
            url="https://algochess.org/play",
            title="AlgoChess",
            text="play chess for real ALGO, settled on-chain on Algorand",
            content_hash="h3",
            published_at="2026-08-30T21:29:00Z",
            links=[],
        )

    monkeypatch.setattr(driver, "scrape_with_fallback", _fake_scrape)

    driver.scrape_from_queue_item(_item("https://algochess.org/play"))

    # The fetch itself, and the index write it feeds, both use the CANONICAL
    # service_id -- the fresh discovered-web-orphan id never reaches storage.
    assert scrape_with_fallback_calls == ["algochess-org"]
    assert fake_task.calls[0]["service_id"] == "algochess-org"


def test_a_page_on_an_unclaimed_domain_still_gets_a_fresh_synthetic_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No regression on the ordinary case: an unclaimed domain still mints its own per-page id."""
    _wire_common_gates(monkeypatch, admin_approved=False)
    monkeypatch.setattr(domain_tracker, "record_domain_crawl", lambda _domain: None)
    monkeypatch.setattr(publish_classifier, "is_content_quality_sufficient", lambda _text: True)
    monkeypatch.setattr(
        discovery_store,
        "store_discovery_content",
        lambda **_kw: discovery_store.DiscoveryStoreOutcome(
            status="stored", url="https://svc.example/ok"
        ),
    )
    fake_task = _FakeTask()
    monkeypatch.setattr(index_tasks, "index_crawled_page", fake_task)

    driver = WebCrawlerDriver()
    scrape_with_fallback_calls: list[str] = []

    def _fake_scrape(_url: str, source_id: str, **_kw: object) -> ScrapeResult:
        scrape_with_fallback_calls.append(source_id)
        return ScrapeResult(
            source_id=source_id,
            url="https://svc.example/ok",
            title="Real page",
            text="algorand ecosystem partner content, plenty of it",
            content_hash="h4",
            published_at="2026-08-21T12:30:00Z",
            links=[],
        )

    monkeypatch.setattr(driver, "scrape_with_fallback", _fake_scrape)

    driver.scrape_from_queue_item(_item("https://svc.example/ok"))

    assert scrape_with_fallback_calls == ["discovered-web-x"]
    assert fake_task.calls[0]["service_id"] == "discovered-web-x"
