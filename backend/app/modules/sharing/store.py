"""Cassandra store for share_links (+ its by-article reverse index) and draft_comments.

A share link is an opaque bearer token minted by an admin that unlocks
exactly one article_id, bypassing NewsService.get_article's draft gate (see
NewsService.get_article_ignoring_draft_gate). Revocation is a soft delete
(revoked flag) so a revoked token still resolves — the public route can
answer a distinct "revoked" 403 instead of an indistinguishable 404, and
admin keeps a persistent share history.

Comments are a shared thread: everyone holding a valid link for an article
sees every comment on it (product decision), so there is no per-token
partitioning on draft_comments.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.schemas import CommentItem, CommentQuoteAnchor, ShareLinkItem

_TOKEN_BYTES = 32


def _epoch(dt: datetime | None) -> int | None:
    return int(dt.timestamp()) if dt else None


def _row_to_link(row: object) -> ShareLinkItem:
    return ShareLinkItem(
        token=str(getattr(row, "share_token", "") or ""),
        article_id=str(getattr(row, "article_id", "") or ""),
        label=str(getattr(row, "label", "") or ""),
        created_at_epoch=_epoch(getattr(row, "created_at", None)) or 0,
        created_by=str(getattr(row, "created_by", "") or ""),
        revoked=bool(getattr(row, "revoked", False)),
        revoked_at_epoch=_epoch(getattr(row, "revoked_at", None)),
    )


def _row_to_comment(row: object) -> CommentItem:
    return CommentItem(
        comment_id=str(getattr(row, "comment_id", "") or ""),
        article_id=str(getattr(row, "article_id", "") or ""),
        body=str(getattr(row, "body", "") or ""),
        author_name=str(getattr(row, "author_name", "") or ""),
        created_at_epoch=_epoch(getattr(row, "created_at", None)) or 0,
        anchor_quote=getattr(row, "anchor_quote", None),
        anchor_prefix=getattr(row, "anchor_prefix", None),
        anchor_suffix=getattr(row, "anchor_suffix", None),
    )


def create_link(article_id: str, *, label: str, created_by: str) -> ShareLinkItem:
    """Mint a new share link for one article. Dual-writes share_links + share_links_by_article."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ShareLinkStmts

    session = get_cassandra_session()
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    article_uuid = UUID(article_id)
    now = datetime.now(tz=UTC)
    session.execute(ShareLinkStmts.INSERT, (token, article_uuid, label, now, created_by))
    session.execute(
        ShareLinkStmts.INSERT_BY_ARTICLE, (article_uuid, now, token, label, created_by)
    )
    return ShareLinkItem(
        token=token,
        article_id=article_id,
        label=label,
        created_at_epoch=int(now.timestamp()),
        created_by=created_by,
        revoked=False,
        revoked_at_epoch=None,
    )


def get_link(token: str) -> ShareLinkItem | None:
    """Look up a share link by token, regardless of revoked status."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ShareLinkStmts

    if not token:
        return None
    row = get_cassandra_session().execute(ShareLinkStmts.GET, (token,)).one()
    return _row_to_link(row) if row is not None else None


def resolve_active_link(token: str) -> tuple[ShareLinkItem | None, str | None]:
    """Resolve a token for the public route: (link, None) valid, (None, "not_found"), or (None, "revoked")."""
    link = get_link(token)
    if link is None:
        return None, "not_found"
    if link.revoked:
        return None, "revoked"
    return link, None


def list_links_for_article(article_id: str) -> list[ShareLinkItem]:
    """All share links (active and revoked) ever minted for one article, newest first."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ShareLinkStmts

    rows = get_cassandra_session().execute(
        ShareLinkStmts.LIST_BY_ARTICLE, (UUID(article_id),)
    )
    return [_row_to_link(r) for r in rows]


def revoke_link(token: str) -> ShareLinkItem | None:
    """Revoke a share link; returns None if the token doesn't exist. Idempotent-safe on an already-revoked token."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ShareLinkStmts

    session = get_cassandra_session()
    row = session.execute(ShareLinkStmts.GET, (token,)).one()
    if row is None:
        return None
    now = datetime.now(tz=UTC)
    session.execute(ShareLinkStmts.REVOKE, (now, token))
    session.execute(
        ShareLinkStmts.REVOKE_BY_ARTICLE, (now, row.article_id, row.created_at, token)
    )
    link = _row_to_link(row)
    return ShareLinkItem(
        token=link.token,
        article_id=link.article_id,
        label=link.label,
        created_at_epoch=link.created_at_epoch,
        created_by=link.created_by,
        revoked=True,
        revoked_at_epoch=int(now.timestamp()),
    )


def list_comments(article_id: str) -> list[CommentItem]:
    """The full shared comment thread for one article, oldest first (clustering order)."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import DraftCommentStmts

    rows = get_cassandra_session().execute(
        DraftCommentStmts.LIST_BY_ARTICLE, (UUID(article_id),)
    )
    return [_row_to_comment(r) for r in rows]


def add_comment(
    article_id: str,
    *,
    body: str,
    author_name: str,
    anchor: CommentQuoteAnchor | None,
) -> CommentItem:
    """Append a comment to the shared thread, optionally anchored to a text quote."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import DraftCommentStmts

    session = get_cassandra_session()
    article_uuid = UUID(article_id)
    now = datetime.now(tz=UTC)
    comment_id = uuid4()
    session.execute(
        DraftCommentStmts.INSERT,
        (
            article_uuid,
            now,
            comment_id,
            body,
            author_name,
            anchor.quote if anchor else None,
            anchor.prefix if anchor else None,
            anchor.suffix if anchor else None,
        ),
    )
    return CommentItem(
        comment_id=str(comment_id),
        article_id=article_id,
        body=body,
        author_name=author_name,
        created_at_epoch=int(now.timestamp()),
        anchor_quote=anchor.quote if anchor else None,
        anchor_prefix=anchor.prefix if anchor else None,
        anchor_suffix=anchor.suffix if anchor else None,
    )


def delete_comment(article_id: str, comment_id: str) -> bool:
    """Delete one comment (admin moderation); returns False if it was not found."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import DraftCommentStmts

    session = get_cassandra_session()
    article_uuid = UUID(article_id)
    comment_uuid = UUID(comment_id)
    for row in session.execute(DraftCommentStmts.LIST_BY_ARTICLE, (article_uuid,)):
        if row.comment_id == comment_uuid:
            session.execute(DraftCommentStmts.DELETE, (article_uuid, row.created_at, comment_uuid))
            return True
    return False
