"""Resolving new-article-vs-edit publish mode, and looking up a service's own coverage."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from app.modules.newspaper.article_matching import (
    find_latest_service_article,
    resolve_publish_mode,
    service_has_article,
)


def test_resolve_publish_mode_edit_when_requested_and_window_open() -> None:
    """An explicit requested_mode="edit" + article id (e.g. an editorial-brief refresh) resolves to edit while the edit window is open."""
    with patch(
        "app.modules.newspaper.article_matching.is_edit_window_open", return_value=True
    ):
        info = resolve_publish_mode(
            requested_mode="edit",
            requested_article_id="article-123",
        )
    assert info["publish_mode"] == "edit"
    assert info["linked_article_id"] == "article-123"
    assert info["edit_window_open"] is True


def test_resolve_publish_mode_create_when_requested_edit_window_closed() -> None:
    """A requested edit whose window has already closed falls through to create, not a stale edit."""
    with patch(
        "app.modules.newspaper.article_matching.is_edit_window_open", return_value=False
    ):
        info = resolve_publish_mode(
            requested_mode="edit",
            requested_article_id="article-123",
        )
    assert info["publish_mode"] == "create"
    assert info["linked_article_id"] is None


def test_resolve_publish_mode_create_with_no_request() -> None:
    """No explicit edit request (the ordinary crawl path, 2026-08-24: no more match-key follow-up lookup) always resolves to create."""
    info = resolve_publish_mode()
    assert info["publish_mode"] == "create"
    assert info["linked_article_id"] is None
    assert info["edit_window_open"] is False


class _Row:
    def __init__(
        self,
        article_id: str,
        *,
        status: str = "published",
        published_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> None:
        self.article_id = article_id
        self.status = status
        self.published_at = published_at
        self.updated_at = updated_at


def test_find_latest_service_article_picks_the_newest_linked_at() -> None:
    """Among several `articles` rows for a service, returns the one with the latest updated_at (falling back to published_at when never edited), not the first/last by scan order."""
    now = datetime.now(tz=UTC)
    rows = [
        _Row("older-article", published_at=now - timedelta(days=21)),
        _Row("newest-article", published_at=now - timedelta(days=10), updated_at=now - timedelta(hours=2)),
        _Row("middle-article", published_at=now - timedelta(days=5)),
    ]
    with patch("app.core.cassandra.get_cassandra_session") as mock_session:
        mock_session.return_value.execute.return_value = rows
        result = find_latest_service_article("algostakepool-com")
    assert result == "newest-article"


def test_find_latest_service_article_ignores_non_published_rows() -> None:
    """A draft/backlog/deleted row for the same service_id must not win over a published one -- mirrors service_has_article's "publish/edit paths only" semantics."""
    now = datetime.now(tz=UTC)
    rows = [
        _Row("draft-article", status="draft", published_at=None, updated_at=now),
        _Row("real-article", status="published", published_at=now - timedelta(days=1)),
    ]
    with patch("app.core.cassandra.get_cassandra_session") as mock_session:
        mock_session.return_value.execute.return_value = rows
        result = find_latest_service_article("algostakepool-com")
    assert result == "real-article"


def test_find_latest_service_article_none_when_service_never_published() -> None:
    """Returns None (not an exception) when the service has no match-key rows at all."""
    with patch("app.core.cassandra.get_cassandra_session") as mock_session:
        mock_session.return_value.execute.return_value = []
        assert find_latest_service_article("brand-new-service") is None


def test_service_has_article_true_when_a_published_row_exists() -> None:
    """A published `articles` row for this service_id is enough."""
    rows = [_Row("real-article", status="published")]
    with patch("app.core.cassandra.get_cassandra_session") as mock_session:
        mock_session.return_value.execute.return_value = rows
        assert service_has_article("algostakepool-com") is True


def test_service_has_article_false_when_only_a_draft_row_exists() -> None:
    """A held/review draft must not count as "readers have been introduced to the service" -- the whole reason the status filter exists."""
    rows = [_Row("draft-article", status="draft")]
    with patch("app.core.cassandra.get_cassandra_session") as mock_session:
        mock_session.return_value.execute.return_value = rows
        assert service_has_article("algostakepool-com") is False


def test_service_has_article_fails_open_on_store_error() -> None:
    """A Cassandra error returns True rather than raising -- the safe default is the normal update framing."""
    with patch("app.core.cassandra.get_cassandra_session", side_effect=RuntimeError("boom")):
        assert service_has_article("algostakepool-com") is True


def test_find_latest_service_article_fails_open_on_store_error() -> None:
    """A Cassandra error returns None rather than raising -- the safe default is no comparison baseline."""
    with patch("app.core.cassandra.get_cassandra_session", side_effect=RuntimeError("boom")):
        assert find_latest_service_article("algostakepool-com") is None
