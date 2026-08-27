"""Article: the shared domain object wrapping status transitions on the `articles` table.

The whole point of this class is `publish()`'s new invariant -- refuse (raise
DuplicateArticleError) when a DIFFERENT article_id already owns a live
published article for the same service_id. Root-caused 2026-08-27: three
separate production incidents (HesabPay, AlgoRank, Al Goanna) each let a
duplicate go live because no single write path enforced this.

Tests mock at the function-delegation boundary (published_rows_for_service,
transition_article_status, ensure_article_slug) rather than faking a full
Cassandra session -- Article is deliberately a thin facade, and each
delegated function already has its own dedicated test coverage elsewhere.
"""

from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from algorand_shared.article import Article, DuplicateArticleError


def _row(**overrides: object) -> SimpleNamespace:
    """A full articles-row shape (every Article field), overridable per test."""
    base = {
        "status": "on_hold",
        "year": 2026,
        "published_at": datetime(2026, 8, 27, tzinfo=UTC),
        "article_id": uuid4(),
        "service_id": "svc-a",
        "title": "A Headline",
        "summary": "Summary",
        "body": "Body",
        "image_url": None,
        "tags": ["nft"],
        "source_url": "https://example.com",
        "trigger_txid": None,
        "trigger_round": None,
        "slug": None,
        "translations": None,
        "first_published_at": None,
        "updated_at": None,
        "prompt_version": None,
        "composed_by_model": None,
        "deleted_at": None,
        "status_updated_at": None,
        "interest_score": None,
        "approved_at": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _article(**overrides: object) -> Article:
    row = _row(**overrides)
    return Article(**{f.name: getattr(row, f.name) for f in fields(Article)})


def test_from_row_maps_every_dataclass_field(fake_cassandra_session: MagicMock) -> None:
    """Article.load reads GET_FULL_BY_ID and maps every column onto the dataclass."""
    row = _row(title="Loaded Title")
    fake_cassandra_session.execute.return_value.one.return_value = row

    loaded = Article.load(row.article_id)

    assert loaded is not None
    assert loaded.title == "Loaded Title"
    assert loaded.article_id == row.article_id


def test_load_returns_none_for_unknown_id(fake_cassandra_session: MagicMock) -> None:
    """Article.load returns None when GET_FULL_BY_ID finds no row."""
    fake_cassandra_session.execute.return_value.one.return_value = None
    assert Article.load(uuid4()) is None


def test_publish_refuses_when_a_different_article_already_owns_the_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The actual point of this class: a second article for an already-covered service must be refused, not silently published."""
    article = _article(service_id="algoanna-com")
    existing_id = uuid4()
    monkeypatch.setattr(
        "algorand_shared.article_matching.published_rows_for_service",
        lambda _sid: [SimpleNamespace(article_id=existing_id)],
    )
    transition_called = {"n": 0}
    monkeypatch.setattr(
        "algorand_shared.article_transitions.transition_article_status",
        lambda *_a, **_k: transition_called.__setitem__("n", transition_called["n"] + 1),
    )

    with pytest.raises(DuplicateArticleError) as exc_info:
        article.publish()

    assert exc_info.value.existing_article_id == str(existing_id)
    assert exc_info.value.service_id == "algoanna-com"
    assert transition_called["n"] == 0  # never attempted the write


def test_publish_succeeds_when_no_conflict_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    """No conflicting service_id -- publish proceeds normally."""
    article = _article(service_id="svc-fresh")
    monkeypatch.setattr(
        "algorand_shared.article_matching.published_rows_for_service", lambda _sid: []
    )
    transition_calls: list[dict] = []
    monkeypatch.setattr(
        "algorand_shared.article_transitions.transition_article_status",
        lambda article_id, **kw: transition_calls.append({"article_id": article_id, **kw}),
    )
    monkeypatch.setattr(Article, "ensure_slug", lambda self: "svc-fresh-slug")  # noqa: ARG005

    article.publish()

    assert len(transition_calls) == 1
    assert transition_calls[0]["article_id"] == article.article_id
    assert transition_calls[0]["new_status"] == "published"


def test_publish_does_not_conflict_with_its_own_existing_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-publishing (a recompose re-publish, same article_id already 'published') must not trip the duplicate guard against itself."""
    article = _article(service_id="svc-a", status="published")
    monkeypatch.setattr(
        "algorand_shared.article_matching.published_rows_for_service",
        lambda _sid: [SimpleNamespace(article_id=article.article_id)],
    )
    transition_calls: list[dict] = []
    monkeypatch.setattr(
        "algorand_shared.article_transitions.transition_article_status",
        lambda article_id, **kw: transition_calls.append({"article_id": article_id, **kw}),
    )
    monkeypatch.setattr(Article, "ensure_slug", lambda self: None)  # noqa: ARG005

    article.publish()  # must not raise

    assert len(transition_calls) == 1


def test_publish_fails_open_when_the_conflict_lookup_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient store blip in the conflict check must never itself block a legitimate publish -- matches service_has_article's own fail-open posture."""
    article = _article(service_id="svc-a")

    def _boom(_sid: str) -> None:
        raise RuntimeError("cassandra blip")

    monkeypatch.setattr("algorand_shared.article_matching.published_rows_for_service", _boom)
    transition_calls: list[dict] = []
    monkeypatch.setattr(
        "algorand_shared.article_transitions.transition_article_status",
        lambda article_id, **kw: transition_calls.append({"article_id": article_id, **kw}),
    )
    monkeypatch.setattr(Article, "ensure_slug", lambda self: None)  # noqa: ARG005

    article.publish()  # must not raise

    assert len(transition_calls) == 1


def test_publish_skips_conflict_check_for_a_falsy_service_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No service_id (a brief/mail-derived article) has nothing to deduplicate against -- the lookup must not even run."""
    article = _article(service_id=None)
    called = {"n": 0}
    monkeypatch.setattr(
        "algorand_shared.article_matching.published_rows_for_service",
        lambda _sid: called.__setitem__("n", called["n"] + 1) or [],
    )
    monkeypatch.setattr(
        "algorand_shared.article_transitions.transition_article_status", lambda *_a, **_k: None
    )
    monkeypatch.setattr(Article, "ensure_slug", lambda self: None)  # noqa: ARG005

    article.publish()

    assert called["n"] == 0


def test_hold_transitions_to_on_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    """.hold() transitions the article to status='on_hold'."""
    article = _article()
    calls: list[dict] = []
    monkeypatch.setattr(
        "algorand_shared.article_transitions.transition_article_status",
        lambda article_id, **kw: calls.append({"article_id": article_id, **kw}),
    )
    article.hold()
    assert calls == [{"article_id": article.article_id, "new_status": "on_hold"}]


def test_delete_transitions_to_deleted_with_a_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
    """.delete() transitions the article to status='deleted' with a deleted_at timestamp."""
    article = _article()
    calls: list[dict] = []
    monkeypatch.setattr(
        "algorand_shared.article_transitions.transition_article_status",
        lambda article_id, **kw: calls.append({"article_id": article_id, **kw}),
    )
    article.delete()
    assert calls[0]["new_status"] == "deleted"
    assert calls[0]["deleted_at"] is not None


def test_ensure_slug_claims_and_writes_it_back(
    fake_cassandra_session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """.ensure_slug() claims a slug, writes it back, and syncs the tag index."""
    article = _article(title="Fresh Title")
    monkeypatch.setattr("algorand_shared.slugs.ensure_article_slug", lambda *_a: "fresh-title")
    current = _row(status="published", year=2026, published_at=article.published_at)
    fake_cassandra_session.execute.return_value.one.return_value = current
    tag_index_calls: list[dict] = []
    monkeypatch.setattr(
        "algorand_shared.article_tag_index.set_slug_in_tag_index",
        lambda article_id, **kw: tag_index_calls.append({"article_id": article_id, **kw}),
    )

    slug = article.ensure_slug()

    assert slug == "fresh-title"
    assert article.slug == "fresh-title"
    assert len(tag_index_calls) == 1
    assert tag_index_calls[0]["slug"] == "fresh-title"


def test_ensure_slug_is_a_noop_when_none_claimed(
    fake_cassandra_session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No slug claimed (already has one) -- no further Cassandra calls made."""
    article = _article()
    monkeypatch.setattr("algorand_shared.slugs.ensure_article_slug", lambda *_a: None)

    assert article.ensure_slug() is None
    fake_cassandra_session.execute.assert_not_called()
