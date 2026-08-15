"""sharing.api.routes: token resolution (404/403/200), comment validation, and the per-token rate limit."""

from __future__ import annotations

from typing import Never
from unittest.mock import patch

import pytest

from app.core.http import Request
from app.modules.sharing.api import routes as sharing_routes
from app.modules.sharing.store import ShareLinkItem
from app.schemas import CommentItem


def _req(*, token: str = "", body: bytes = b"") -> Request:
    return Request(
        method="GET",
        headers={},
        query_params={},  # type: ignore[arg-type]
        path_params={"token": token},
        body=body,
    )


def _link(*, revoked: bool = False) -> ShareLinkItem:
    return ShareLinkItem(
        token="tok_abc123",
        article_id="00000000-0000-0000-0000-000000000001",
        label="reviewer copy",
        created_at_epoch=1_700_000_000,
        created_by="0xADMIN",
        revoked=revoked,
        revoked_at_epoch=None,
    )


def test_shared_article_404_unknown_token() -> None:
    """An unknown token never reaches NewsService -- it 404s at the resolve step."""
    with patch(
        "app.modules.sharing.store.resolve_active_link", return_value=(None, "not_found")
    ):
        resp = sharing_routes.shared_article(_req(token="nope"))
    assert resp.status_code == 404


def test_shared_article_403_revoked_token() -> None:
    """A revoked token gives a distinct 403, not an indistinguishable 404."""
    with patch(
        "app.modules.sharing.store.resolve_active_link", return_value=(None, "revoked")
    ):
        resp = sharing_routes.shared_article(_req(token="tok_abc123"))
    assert resp.status_code == 403


def test_shared_article_200_valid_token_against_draft() -> None:
    """A valid token returns the article even though it's still a draft -- the token bypasses the gate."""
    link = _link()
    from app.schemas import ArticleDetail

    detail = ArticleDetail(
        article_id=link.article_id,
        service_id="svc",
        title="Draft headline",
        summary="s",
        body="b",
        published_at_epoch=1_700_000_000,
    )
    with (
        patch("app.modules.sharing.store.resolve_active_link", return_value=(link, None)),
        patch(
            "app.modules.news.services.news_service.NewsService.get_article_ignoring_draft_gate",
            return_value=(detail, True),
        ),
    ):
        sharing_routes._news_service.cache_clear()
        result = sharing_routes.shared_article(_req(token="tok_abc123"))
    assert isinstance(result, dict)
    assert result["is_draft"] is True
    assert result["article"]["title"] == "Draft headline"


def test_shared_comments_create_rejects_body_over_max_length() -> None:
    """A comment body over msgspec's max_length is rejected with 400, before any store write."""
    link = _link()
    with patch("app.modules.sharing.store.resolve_active_link", return_value=(link, None)):
        body = ('{"body": "%s"}' % ("x" * 3000)).encode()
        resp = sharing_routes.shared_comments_create(_req(token="tok_abc123", body=body))
    assert resp.status_code == 400


def test_shared_comments_create_succeeds_and_stores() -> None:
    """A valid comment POST calls add_comment and returns the created item."""
    link = _link()
    created = CommentItem(
        comment_id="c1",
        article_id=link.article_id,
        body="worth checking",
        author_name="Reviewer",
        created_at_epoch=1_700_000_000,
    )
    with (
        patch("app.modules.sharing.store.resolve_active_link", return_value=(link, None)),
        patch("app.modules.sharing.store.add_comment", return_value=created) as mock_add,
    ):
        body = b'{"body": "worth checking", "author_name": "Reviewer"}'
        result = sharing_routes.shared_comments_create(_req(token="tok_abc123", body=body))
    assert isinstance(result, dict)
    assert result["comment_id"] == "c1"
    mock_add.assert_called_once()


def test_rate_limited_counts_per_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rate-limits a token after 30 comments within the hour, leaving other tokens unaffected."""
    counts: dict[str, int] = {}

    class FakeRedis:
        def incr(self, key: str) -> int:
            counts[key] = counts.get(key, 0) + 1
            return counts[key]

        def expire(self, _key: str, ttl: int) -> None:
            assert ttl == 3600

    monkeypatch.setattr(sharing_routes, "_redis", lambda: FakeRedis())
    assert all(not sharing_routes._rate_limited("tok_a") for _ in range(30))
    assert sharing_routes._rate_limited("tok_a")
    assert not sharing_routes._rate_limited("tok_b")  # separate token has its own budget


def test_rate_limited_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Redis failure must never block a legitimate comment -- rate limiting fails open."""

    def boom() -> Never:
        raise ConnectionError("redis down")

    monkeypatch.setattr(sharing_routes, "_redis", boom)
    assert not sharing_routes._rate_limited("tok_a")
    assert not sharing_routes._rate_limited("")
