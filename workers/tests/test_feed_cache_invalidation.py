"""Write-path invalidation of the reader feed's cached first page (backend/app/core/cache.py, keyed news:feed:first:...).

article_store.py's insert_stored_article (publish_to_feed=True), update_article,
and update_article_image all call algorand_shared.feed_cache.invalidate_feed_first_page()
so a write is visible on the next request rather than waiting out the cache's
60s TTL. transition_article_status (shared/algorand_shared/article_transitions.py,
covering delete/draft-toggle/recompose-republish in both services) is covered
separately -- see test_transition_invalidates_feed_cache below.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Never
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

_PUBLISHED_AT = datetime(2026, 7, 14, 18, 52, 10, 629000, tzinfo=UTC)


def _row(aid: UUID) -> MagicMock:
    row = MagicMock()
    row.article_id = aid
    row.status = "published"
    row.year = 2026
    row.service_id = "svc-1"
    row.source_url = "https://example.com/src"
    row.image_url = "https://example.com/hero.png"
    row.title = "T"
    row.summary = "S"
    row.body = "B"
    row.published_at = _PUBLISHED_AT
    row.trigger_txid = ""
    row.trigger_round = 0
    row.prompt_version = ""
    row.translations = None
    row.tags = ["tag"]
    row.first_published_at = None
    row.slug = "existing-slug"
    row.image_url = "https://example.com/hero.png"
    row.updated_at = None
    row.composed_by_model = None
    row.deleted_at = None
    row.status_updated_at = None
    row.interest_score = None
    row.approved_at = None
    return row


def test_insert_stored_article_invalidates_when_published(
    fake_cassandra_session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new article going straight to the feed (publish_to_feed=True) must bust the first-page cache."""
    from app.modules.newspaper import article_store

    fake_cassandra_session.execute.return_value.one.return_value = None  # no stale partition
    called = []
    monkeypatch.setattr(article_store, "invalidate_feed_first_page", lambda: called.append(True))
    monkeypatch.setattr(article_store, "_claim_slug_for_feed", lambda *_a, **_k: None)

    article_store.insert_stored_article(
        service_id="svc",
        title="T",
        summary="S",
        body="B",
        trigger_txid="tx",
        trigger_round=1,
        source_url="https://example.com",
        publish_to_feed=True,
    )

    assert called == [True]


def test_insert_stored_article_skips_invalidation_when_not_published(
    fake_cassandra_session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A held draft/backlog row (publish_to_feed=False) never appears on the feed, so no invalidation is needed."""
    from app.modules.newspaper import article_store

    fake_cassandra_session.execute.return_value.one.return_value = None
    called = []
    monkeypatch.setattr(article_store, "invalidate_feed_first_page", lambda: called.append(True))

    article_store.insert_stored_article(
        service_id="svc",
        title="T",
        summary="S",
        body="B",
        trigger_txid="tx",
        trigger_round=1,
        source_url="https://example.com",
        publish_to_feed=False,
        status="draft",
    )

    assert called == []


def test_update_article_invalidates_feed_cache(
    fake_cassandra_session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An in-place content edit (admin correction / writer revision) must bust the first-page cache."""
    from app.modules.newspaper import article_store

    aid = uuid4()
    fake_cassandra_session.execute.return_value.one.return_value = _row(aid)
    called = []
    monkeypatch.setattr(article_store, "invalidate_feed_first_page", lambda: called.append(True))

    assert article_store.update_article(article_id=str(aid), title="New", summary="NS", body="NB")
    assert called == [True]


def test_update_article_image_invalidates_feed_cache(
    fake_cassandra_session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hero-image backfill changes what the feed card shows, so it must also bust the cache."""
    from app.modules.newspaper import article_store

    aid = uuid4()
    fake_cassandra_session.execute.return_value.one.return_value = _row(aid)
    called = []
    monkeypatch.setattr(article_store, "invalidate_feed_first_page", lambda: called.append(True))

    assert article_store.update_article_image(str(aid), "https://example.com/new.png")
    assert called == [True]


def test_transition_invalidates_feed_cache(
    fake_cassandra_session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """transition_article_status (delete/draft-toggle/recompose-republish, shared by both services) must also bust the cache on every successful transition."""
    from algorand_shared import article_transitions, feed_cache

    aid = uuid4()
    fake_cassandra_session.execute.return_value.one.return_value = _row(aid)
    called = []
    # transition_article_status imports invalidate_feed_first_page lazily
    # (inside the function, matching this module's existing lazy-import
    # convention for get_cassandra_session/ArticlesStmts), so the patch target
    # is feed_cache's own attribute, not a name bound on article_transitions.
    monkeypatch.setattr(feed_cache, "invalidate_feed_first_page", lambda: called.append(True))

    assert article_transitions.transition_article_status(aid, new_status="deleted")
    assert called == [True]


def test_transition_skips_invalidation_when_article_missing(
    fake_cassandra_session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No existing row -> no write happened -> nothing to invalidate."""
    from algorand_shared import article_transitions, feed_cache

    fake_cassandra_session.execute.return_value.one.return_value = None
    called = []
    monkeypatch.setattr(feed_cache, "invalidate_feed_first_page", lambda: called.append(True))

    assert not article_transitions.transition_article_status(uuid4(), new_status="deleted")
    assert called == []


class _BoomRedis:
    """A redis client stand-in whose every call raises, simulating a Redis outage."""

    def scan_iter(self, match: str, count: int = 200) -> Never:  # noqa: ARG002
        raise ConnectionError("redis down")

    def delete(self, *keys: str) -> Never:  # noqa: ARG002
        raise ConnectionError("redis down")


def test_invalidate_feed_first_page_swallows_redis_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Redis blip during invalidation must never propagate into the caller -- the write it's cleaning up after is already committed to Cassandra by this point."""
    import redis
    from algorand_shared.feed_cache import invalidate_feed_first_page

    monkeypatch.setattr(redis, "from_url", lambda *_a, **_k: _BoomRedis())

    invalidate_feed_first_page()  # must not raise


def test_invalidate_feed_first_page_fails_open_with_no_redis_available() -> None:
    """Belt-and-suspenders: with the test suite's process-wide socket block in effect and no monkeypatch, a real connection attempt is refused -- invalidation must still not raise."""
    from algorand_shared.feed_cache import invalidate_feed_first_page

    invalidate_feed_first_page()  # must not raise even against a real (blocked) connect
