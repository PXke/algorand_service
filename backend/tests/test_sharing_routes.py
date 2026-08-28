"""sharing.api.routes: token resolution (404/403/200), comment validation, and the per-token+IP rate limit."""

from __future__ import annotations

from typing import Never
from unittest.mock import patch

import pytest

from app.core.http import Request
from app.modules.sharing.api import routes as sharing_routes
from app.modules.sharing.store import ShareLinkItem
from app.schemas import CommentItem


class _NoopRedis:
    """A Redis stand-in that always succeeds.

    For tests exercising something other than the rate limiter itself, so a
    real fail-closed Redis error doesn't turn every unrelated comment test
    into a 429.
    """

    def incr(self, _key: str) -> int:
        return 1

    def expire(self, _key: str, _ttl: int) -> None:
        return None


def _req(*, token: str = "", body: bytes = b"", client_ip: str = "203.0.113.9") -> Request:
    return Request(
        method="GET",
        headers={"x-real-ip": client_ip} if client_ip else {},
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
    with patch("app.modules.sharing.store.resolve_active_link", return_value=(None, "not_found")):
        resp = sharing_routes.shared_article(_req(token="nope"))
    assert resp.status_code == 404


def test_shared_article_403_revoked_token() -> None:
    """A revoked token gives a distinct 403, not an indistinguishable 404."""
    with patch("app.modules.sharing.store.resolve_active_link", return_value=(None, "revoked")):
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


def test_shared_comments_create_rejects_body_over_max_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A comment body over msgspec's max_length is rejected with 400, before any store write."""
    monkeypatch.setattr(sharing_routes, "_redis", lambda: _NoopRedis())
    link = _link()
    with patch("app.modules.sharing.store.resolve_active_link", return_value=(link, None)):
        body = ('{"body": "%s"}' % ("x" * 3000)).encode()
        resp = sharing_routes.shared_comments_create(_req(token="tok_abc123", body=body))
    assert resp.status_code == 400


def test_shared_comments_create_succeeds_and_stores(monkeypatch: pytest.MonkeyPatch) -> None:
    """A valid comment POST calls add_comment and returns the created item."""
    monkeypatch.setattr(sharing_routes, "_redis", lambda: _NoopRedis())
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


def test_shared_comments_create_429_when_rate_limited() -> None:
    """The route itself surfaces the limiter's verdict as a 429, before touching the store."""
    link = _link()
    with (
        patch("app.modules.sharing.store.resolve_active_link", return_value=(link, None)),
        patch.object(sharing_routes, "_rate_limited", return_value=True),
        patch("app.modules.sharing.store.add_comment") as mock_add,
    ):
        body = b'{"body": "worth checking", "author_name": "Reviewer"}'
        resp = sharing_routes.shared_comments_create(_req(token="tok_abc123", body=body))
    assert resp.status_code == 429
    mock_add.assert_not_called()


def test_rate_limited_counts_per_token_and_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rate-limits a (token, IP) pair after 30 comments within the hour.

    A different IP on the same token, or the same IP on a different token,
    gets its own separate budget.
    """
    counts: dict[str, int] = {}

    class FakeRedis:
        def incr(self, key: str) -> int:
            counts[key] = counts.get(key, 0) + 1
            return counts[key]

        def expire(self, _key: str, ttl: int) -> None:
            assert ttl == 3600

    monkeypatch.setattr(sharing_routes, "_redis", lambda: FakeRedis())
    assert all(not sharing_routes._rate_limited("tok_a", "1.1.1.1") for _ in range(30))
    assert sharing_routes._rate_limited("tok_a", "1.1.1.1")
    assert not sharing_routes._rate_limited("tok_a", "2.2.2.2")  # different IP, same token
    assert not sharing_routes._rate_limited("tok_b", "1.1.1.1")  # different token, same IP


def test_rate_limited_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Redis failure must not silently open the gate for an unauthenticated, token-only endpoint.

    Rate limiting fails closed here (unlike the wallet/session-backed limiters elsewhere).
    """

    def boom() -> Never:
        raise ConnectionError("redis down")

    monkeypatch.setattr(sharing_routes, "_redis", boom)
    assert sharing_routes._rate_limited("tok_a", "1.1.1.1")
    assert sharing_routes._rate_limited("", "1.1.1.1")  # empty token also fails closed
