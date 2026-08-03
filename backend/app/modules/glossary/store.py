"""Admin-curated glossary: term/definition pairs linked to from article bodies (deterministic auto-link, never model-authored) and served as their own public pages.

Translations mirror articles_by_id's own shape (lang -> JSON blob), filled by
the same local translation engine/backfill task articles already use — see
workers/app/modules/newspaper/tasks/publish_tasks.py's translate_article_batch_task
for the article-side twin this reuses the pattern from.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

STATUS_DRAFT = "draft"
STATUS_PUBLISHED = "published"


@dataclass(frozen=True)
class GlossaryTerm:
    """One glossary entry, resolved to a single language."""

    slug: str
    term: str
    definition: str
    aliases: tuple[str, ...]
    status: str
    created_at_epoch: int
    updated_at_epoch: int
    translations: dict[str, str] = field(default_factory=dict)


def _epoch(dt: datetime | None) -> int:
    if dt is None:
        return 0
    return int((dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt).timestamp())


def _row_to_term(row: object, *, lang: str | None = None) -> GlossaryTerm:
    translations = dict(getattr(row, "translations", None) or {})
    term = str(getattr(row, "term", "") or "")
    definition = str(getattr(row, "definition", "") or "")
    aliases = tuple(getattr(row, "aliases", None) or ())
    if lang and lang in translations:
        try:
            blob = json.loads(translations[lang])
            term = blob.get("term") or term
            definition = blob.get("definition") or definition
            aliases = tuple(blob.get("aliases") or aliases)
        except Exception:
            pass
    return GlossaryTerm(
        slug=str(getattr(row, "slug", "") or ""),
        term=term,
        definition=definition,
        aliases=aliases,
        status=str(getattr(row, "status", "") or STATUS_DRAFT),
        created_at_epoch=_epoch(getattr(row, "created_at", None)),
        updated_at_epoch=_epoch(getattr(row, "updated_at", None)),
        translations=translations,
    )


def list_terms(*, published_only: bool = False) -> list[GlossaryTerm]:
    """All glossary entries, English. Small/fully-enumerable table -- one full scan, no paging."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import GlossaryStmts

    rows = get_cassandra_session().execute(GlossaryStmts.LIST_ALL)
    terms = [_row_to_term(r) for r in rows]
    if published_only:
        terms = [t for t in terms if t.status == STATUS_PUBLISHED]
    return sorted(terms, key=lambda t: t.term.lower())


def get_term(slug: str, *, lang: str | None = None) -> GlossaryTerm | None:
    """One glossary entry by slug, resolved to `lang` when a translation exists."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import GlossaryStmts

    if not slug:
        return None
    row = get_cassandra_session().execute(GlossaryStmts.GET, (slug,)).one()
    return _row_to_term(row, lang=lang) if row is not None else None


def upsert_term(
    *,
    slug: str,
    term: str,
    definition: str,
    aliases: list[str] | None = None,
    status: str = STATUS_DRAFT,
    created_by: str = "",
) -> GlossaryTerm:
    """Create or fully replace a glossary entry's own-language fields. Translations are untouched -- update_term_translations owns those."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import GlossaryStmts

    session = get_cassandra_session()
    existing = session.execute(GlossaryStmts.GET, (slug,)).one()
    now = datetime.now(tz=UTC)
    created_at = existing.created_at if existing is not None else now
    session.execute(
        GlossaryStmts.UPSERT,
        (
            slug,
            term,
            definition,
            list(aliases or []),
            status,
            created_at,
            now,
            created_by,
        ),
    )
    term_obj = get_term(slug)
    assert term_obj is not None  # just wrote it
    return term_obj


def delete_term(slug: str) -> bool:
    """Delete a glossary entry; returns False if it did not exist."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import GlossaryStmts

    if not slug:
        return False
    session = get_cassandra_session()
    if session.execute(GlossaryStmts.GET, (slug,)).one() is None:
        return False
    session.execute(GlossaryStmts.DELETE, (slug,))
    return True


def update_term_translations(slug: str, translations: dict[str, str]) -> bool:
    """Merge new language(s) into a term's translations map. Same merge-not-replace shape as articles_by_id."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import GlossaryStmts

    if not slug or not translations:
        return False
    result = get_cassandra_session().execute(GlossaryStmts.UPDATE_TRANSLATIONS, (translations, slug))
    return bool(result.was_applied)
