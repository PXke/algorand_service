"""Admin-curated glossary: term/definition pairs linked to from article bodies (deterministic auto-link, never model-authored) and served as their own public pages.

Translations mirror articles_by_id's own shape (lang -> JSON blob). Unlike
articles (translated via the heavy local engines, worth the multi-GB model
load for a full body), a glossary entry is a name plus 1-3 sentences — fired
via a single Mistral call per language instead
(workers/app/modules/newspaper/tasks/publish_tasks.py's
translate_glossary_term_task, mirroring the article-side legacy
translate_article_task). Found 2026-08-03: this plumbing (translations
column, ?lang= read resolution) existed but nothing ever populated it —
glossary terms were never translated at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.core import serialization

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
            blob = serialization.loads(translations[lang])
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


def enqueue_glossary_term_translations(slug: str) -> None:
    """Publish-time only — a held draft is not translated (same rule as articles: enqueue_article_translations).

    Fires one workers-side Celery task per target language, same task-name
    dispatch over the shared broker as _enqueue_article_translations
    (backend and workers are separate services/venvs, so this can't import
    the task directly).

    queue="translate" (root-caused 2026-08-11): a bulk glossary-review pass
    that published 105 drafts at once fanned out ~840 of these tasks onto
    "pipeline" — the SAME queue as compose/recompose — and stalled an
    unrelated recompose behind the whole batch. translate_glossary_term
    itself is a Mistral API call, not local-model work, so it isn't a
    perfect fit for "translate" (sized/concurrency-1 for the local engine),
    but it's explicitly "never on the article's critical path" (see the
    task's own docstring) — running slowly on a queue that never competes
    with compose is the right tradeoff, not running fast on one that does.
    """
    try:
        from celery import Celery

        from app.core.article_translation_langs import ARTICLE_TRANSLATION_LANGS
        from app.core.config import settings

        app = Celery(broker=settings.celery_broker_url)
        for lang in ARTICLE_TRANSLATION_LANGS:
            app.send_task(
                "app.tasks.newspaper.translate_glossary_term",
                args=[slug, lang],
                queue="translate",
            )
    except Exception:
        import logging

        logging.getLogger(__name__).warning(
            "failed to enqueue glossary translation tasks for %s", slug, exc_info=True
        )
