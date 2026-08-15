"""Public JSON API for token-gated draft shares.

The share token itself is the entire authorization — there is no wallet or
session check anywhere in this file. Every route resolves its :token path
param through sharing.store.resolve_active_link before doing anything else.
"""

from __future__ import annotations

import logging
from contextlib import suppress
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis

from app.core import serialization
from app.core.http import Request, Response, Router
from app.core.http_errors import json_error_response
from app.modules.sharing.store import ShareLinkItem
from app.schemas import CreateCommentRequest, SharedArticleResponse

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _news_service():  # noqa: ANN202 -- avoids importing NewsService at module load (lazy Cassandra store factory)
    from app.modules.news.services.news_service import NewsService

    return NewsService()

# A real review pass can legitimately leave many comments in quick
# succession -- well above contact form's 5/hour, still far below anything
# an automated spammer needs.
_COMMENT_RATE_LIMIT_PER_HOUR = 30


@lru_cache(maxsize=1)
def _redis() -> redis.Redis:
    import redis

    from app.core.config import settings

    return redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=2)


def _rate_limited(token: str) -> bool:
    """Fail OPEN: a Redis hiccup must never block a legitimate reviewer's comment.

    Keyed by TOKEN, not IP -- a reviewer's office IP may be shared with
    others, and IP-keying would cross-contaminate unrelated share links
    behind the same NAT.
    """
    if not token:
        return False
    with suppress(Exception):
        key = f"algorand:sharing:comment_rl:{token}"
        client = _redis()
        count = client.incr(key)
        if count == 1:
            client.expire(key, 3600)
        return int(count) > _COMMENT_RATE_LIMIT_PER_HOUR
    return False


def _require_link(request: Request) -> tuple[ShareLinkItem | None, Response | None]:
    """Resolve the :token path param. Returns (link, None) valid, or (None, error_response)."""
    from app.modules.sharing.store import resolve_active_link

    token = request.path_params.get("token", "")
    link, err = resolve_active_link(token)
    if err == "not_found":
        return None, json_error_response(404, "not_found", "Share link not found")
    if err == "revoked":
        return None, json_error_response(403, "revoked", "This share link has been revoked")
    return link, None


def shared_article(request: Request) -> Response | dict:
    """The shared article's full detail -- bypasses the draft gate via the validated token."""
    link, err = _require_link(request)
    if err is not None or link is None:
        return err  # type: ignore[return-value]

    result = _news_service().get_article_ignoring_draft_gate(link.article_id)
    if result is None:
        return json_error_response(404, "not_found", "Article not found")
    detail, was_draft = result
    return serialization.to_builtins(
        SharedArticleResponse(article=detail, is_draft=was_draft, link_label=link.label)
    )


def shared_comments_list(request: Request) -> Response | dict:
    """The full shared comment thread for the linked article."""
    link, err = _require_link(request)
    if err is not None or link is None:
        return err  # type: ignore[return-value]

    from app.modules.sharing.store import list_comments

    return {"items": serialization.to_builtins(list_comments(link.article_id))}


def shared_comments_create(request: Request) -> Response | dict:
    """Add a comment to the shared thread, optionally anchored to a highlighted text quote."""
    link, err = _require_link(request)
    if err is not None or link is None:
        return err  # type: ignore[return-value]

    if _rate_limited(link.token):
        return json_error_response(
            429, "rate_limited", "Too many comments — please slow down"
        )

    try:
        payload = serialization.decode(request.body, CreateCommentRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))

    from app.modules.sharing.store import add_comment

    item = add_comment(
        link.article_id,
        body=payload.body.strip(),
        author_name=payload.author_name.strip(),
        anchor=payload.anchor,
    )
    return serialization.to_builtins(item)


def register_sharing_routes(app: Router) -> None:
    """Attach the public, token-gated draft-share JSON API to the app."""
    app.get("/api/v1/shared/:token")(shared_article)
    app.get("/api/v1/shared/:token/comments")(shared_comments_list)
    app.post("/api/v1/shared/:token/comments")(shared_comments_create)
