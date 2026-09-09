"""move_review_to_draft: park an on-hold review's article in draft instead of approving or rejecting it -- e.g. the admin wants to reach out to the subject with questions before this goes live."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from app.modules.admin.stores.cassandra import AdminCassandraStore

# `articles`' column order (see algorand_shared.article_transitions._ARTICLES_COLUMNS),
# same as test_admin_article_draft.py.
_ARTICLES_COLUMNS = (
    "status", "year", "published_at", "article_id", "service_id", "title", "summary", "body",
    "image_url", "tags", "source_url", "trigger_txid", "trigger_round", "slug", "translations",
    "translated_titles", "first_published_at", "updated_at", "prompt_version", "composed_by_model",
    "deleted_at", "status_updated_at", "interest_score", "approved_at", "views",
)  # fmt: skip


class _Result:
    def __init__(self, row: Any = None) -> None:  # noqa: ANN401 -- duck-typed Cassandra row
        self._row = row

    def one(self) -> Any:  # noqa: ANN401 -- duck-typed Cassandra row
        return self._row


class _FakeSession:
    """Handles both classifier_review_queue (metadata read, resolution insert/delete) and articles (draft toggle) queries -- move_review_to_draft touches both tables."""

    def __init__(self, *, review_metadata: dict | None, article_row: Any) -> None:  # noqa: ANN401
        self.review_metadata = review_metadata
        self.article_row = article_row
        self.review_resolutions: list[tuple] = []
        self.articles_inserts: list[tuple] = []

    def prepare(self, cql: str) -> str:
        return cql

    def _review_metadata_row(self) -> _Result:
        """move_review_to_draft's own GET_METADATA lookup."""
        if self.review_metadata is None:
            return _Result(None)
        from types import SimpleNamespace

        return _Result(SimpleNamespace(metadata=self.review_metadata))

    def _review_full_row(self) -> _Result:
        """_complete_classifier_review's OWN separate GET_FULL re-fetch -- a different, wider query than the GET_METADATA lookup above, both against the same table."""
        if self.review_metadata is None:
            return _Result(None)
        from types import SimpleNamespace

        return _Result(
            SimpleNamespace(
                url="https://example.com/",
                page_text="text",
                page_title="title",
                category="generic",
                storage_score=1.0,
                created_at=datetime.now(tz=UTC),
                metadata=self.review_metadata,
            )
        )

    def execute(self, query: str, params: tuple = ()) -> _Result:
        q = " ".join(str(query).split())
        if "SELECT metadata FROM algorand_platform.classifier_review_queue" in q:
            return self._review_metadata_row()
        if (
            q.startswith("SELECT review_id, url, page_text")
            and "FROM algorand_platform.classifier_review_queue" in q
        ):
            return self._review_full_row()
        if q.startswith("INSERT INTO algorand_platform.classifier_review_queue ("):
            self.review_resolutions.append(tuple(params))
            return _Result(None)
        if "DELETE FROM algorand_platform.classifier_review_pending" in q:
            return _Result(None)
        if q.startswith("SELECT status, year, published_at FROM algorand_platform.articles"):
            return _Result(self.article_row)
        if q.startswith("SELECT") and "FROM algorand_platform.articles WHERE article_id = ?" in q:
            return _Result(self.article_row)
        if q.startswith("INSERT INTO algorand_platform.articles ("):
            self.articles_inserts.append(tuple(params))
            return _Result(None)
        if q.startswith("DELETE FROM algorand_platform.articles "):
            return _Result(None)
        return _Result(None)


def _patch(monkeypatch: pytest.MonkeyPatch, fake: Any) -> None:  # noqa: ANN401
    import app.core.cassandra as c

    monkeypatch.setattr(c, "get_cassandra_session", lambda: fake)
    c.prepare_cached.cache_clear()


def _article_row(article_id: Any, *, status: str = "on_hold") -> Any:  # noqa: ANN401
    from types import SimpleNamespace

    published_at = datetime.now(tz=UTC)
    values: dict[str, object] = dict.fromkeys(_ARTICLES_COLUMNS)
    values.update(
        status=status,
        year=published_at.year,
        published_at=published_at,
        article_id=article_id,
        service_id="svc",
        title="Title",
        summary="Summary",
        body="",
        tags=[],
        source_url="https://example.com/",
        trigger_txid="",
        trigger_round=0,
        slug="a-slug",
    )
    return SimpleNamespace(**values)


def test_move_review_to_draft_resolves_article_id_and_drafts_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolves article_id from the review row's metadata (same raw-JSON shape recompose_review reads), transitions the article to draft, and resolves the review with a distinct resolution -- not published, not rejected."""
    article_id = uuid4()
    review_id = str(uuid4())
    row = _article_row(article_id, status="on_hold")
    fake = _FakeSession(
        review_metadata={"raw": json.dumps({"article_id": str(article_id)})},
        article_row=row,
    )
    _patch(monkeypatch, fake)
    monkeypatch.setattr("app.modules.seo.indexnow.ping_article", lambda *a, **kw: None)  # noqa: ARG005
    monkeypatch.setattr(
        "app.core.typesense_client.delete_article_document",
        lambda *a, **kw: None,  # noqa: ARG005
    )
    monkeypatch.setattr(AdminCassandraStore, "get_article", lambda self, aid: object())  # noqa: ARG005
    monkeypatch.setattr(AdminCassandraStore, "_trigger_compose_next", staticmethod(lambda: None))

    result = AdminCassandraStore().move_review_to_draft(review_id)

    assert result == {"article_id": str(article_id)}
    assert len(fake.articles_inserts) == 1
    values = dict(zip(_ARTICLES_COLUMNS, fake.articles_inserts[0], strict=True))
    assert values["status"] == "draft"
    assert len(fake.review_resolutions) == 1
    # INSERT_QUEUE column order: review_id, url, page_text, page_title,
    # category, storage_score, status(resolution), created_at, metadata.
    assert fake.review_resolutions[0][6] == "drafted"


def test_move_review_to_draft_missing_review_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """No such review -- nothing to resolve, no article touched."""
    fake = _FakeSession(review_metadata=None, article_row=None)
    _patch(monkeypatch, fake)

    result = AdminCassandraStore().move_review_to_draft(str(uuid4()))

    assert result is None
    assert fake.articles_inserts == []
    assert fake.review_resolutions == []


def test_move_review_to_draft_bad_review_id_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """A malformed review_id fails closed instead of raising."""
    fake = _FakeSession(review_metadata=None, article_row=None)
    _patch(monkeypatch, fake)

    assert AdminCassandraStore().move_review_to_draft("not-a-uuid") is None


def test_move_review_to_draft_missing_article_id_in_metadata_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A review row whose metadata carries no article_id (e.g. corrupted or a non-compose-produced review) can't be resolved -- fails closed rather than drafting an arbitrary article."""
    fake = _FakeSession(review_metadata={"raw": json.dumps({})}, article_row=None)
    _patch(monkeypatch, fake)

    result = AdminCassandraStore().move_review_to_draft(str(uuid4()))

    assert result is None
    assert fake.articles_inserts == []
    assert fake.review_resolutions == []


def test_move_review_to_draft_missing_article_row_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The review points at an article_id that no longer resolves to a real article row -- set_article_draft returns None, and that propagates rather than resolving the review anyway."""
    article_id = uuid4()
    fake = _FakeSession(
        review_metadata={"raw": json.dumps({"article_id": str(article_id)})},
        article_row=None,
    )
    _patch(monkeypatch, fake)

    result = AdminCassandraStore().move_review_to_draft(str(uuid4()))

    assert result is None
    assert fake.review_resolutions == []
