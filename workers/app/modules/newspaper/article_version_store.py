"""Store a version snapshot each time an article's content changes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID


@dataclass(frozen=True)
class ArticleVersionRow:
    """One stored version snapshot of an article's content."""
    version: int
    title: str
    summary: str
    body: str
    edit_reason: str
    editor: str
    edited_at_epoch: int


def next_version_number(article_id: str) -> int:
    """Return the next version number for an article, defaulting to 1 on any lookup failure."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ArticleVersionStmts

    try:
        aid = UUID(article_id)
    except ValueError:
        return 1
    session = get_cassandra_session()
    try:
        row = session.execute(ArticleVersionStmts.LATEST, (aid,)).one()
    except Exception:
        return 1
    if row is None or row.version is None:
        return 1
    return int(row.version) + 1


def save_article_version(
    *,
    article_id: str,
    title: str,
    summary: str,
    body: str,
    edit_reason: str,
    editor: str = "agent",
) -> int:
    """Save a new version snapshot of an article's content and return its version number."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ArticleVersionStmts

    version = next_version_number(article_id)
    aid = UUID(article_id)
    now = datetime.now(tz=UTC)
    session = get_cassandra_session()
    session.execute(
        ArticleVersionStmts.INSERT,
        (aid, version, title, summary, body, edit_reason, editor, now),
    )
    return version


def list_article_versions(article_id: str, *, limit: int = 20) -> list[ArticleVersionRow]:
    """List an article's stored version snapshots, newest first, empty on any lookup failure."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ArticleVersionStmts

    try:
        aid = UUID(article_id)
    except ValueError:
        return []
    session = get_cassandra_session()
    try:
        rows = session.execute(ArticleVersionStmts.LIST, (aid, limit))
    except Exception:
        return []
    out: list[ArticleVersionRow] = []
    for row in rows:
        edited = row.edited_at
        epoch = int(edited.timestamp()) if edited else 0
        out.append(
            ArticleVersionRow(
                version=int(row.version),
                title=row.title or "",
                summary=row.summary or "",
                body=row.body or "",
                edit_reason=row.edit_reason or "",
                editor=row.editor or "",
                edited_at_epoch=epoch,
            )
        )
    return out
