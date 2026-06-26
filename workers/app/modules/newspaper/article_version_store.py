from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID


@dataclass(frozen=True)
class ArticleVersionRow:
    version: int
    title: str
    summary: str
    body: str
    edit_reason: str
    editor: str
    edited_at_epoch: int


def next_version_number(article_id: str) -> int:
    from app.core.cassandra import get_cassandra_session

    try:
        aid = UUID(article_id)
    except ValueError:
        return 1
    session = get_cassandra_session()
    try:
        row = session.execute(
            """
            SELECT version FROM article_versions
            WHERE article_id = %s
            ORDER BY version DESC
            LIMIT 1
            """,
            (aid,),
        ).one()
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
    from app.core.cassandra import get_cassandra_session

    version = next_version_number(article_id)
    aid = UUID(article_id)
    now = datetime.now(tz=UTC)
    session = get_cassandra_session()
    session.execute(
        """
        INSERT INTO article_versions (
          article_id, version, title, summary, body, edit_reason, editor, edited_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (aid, version, title, summary, body, edit_reason, editor, now),
    )
    return version


def list_article_versions(article_id: str, *, limit: int = 20) -> list[ArticleVersionRow]:
    from app.core.cassandra import get_cassandra_session

    try:
        aid = UUID(article_id)
    except ValueError:
        return []
    session = get_cassandra_session()
    try:
        rows = session.execute(
            """
            SELECT version, title, summary, body, edit_reason, editor, edited_at
            FROM article_versions
            WHERE article_id = %s
            LIMIT %s
            """,
            (aid, limit),
        )
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
