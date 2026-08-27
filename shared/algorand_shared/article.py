"""Article domain object: one shared checkpoint for the transition into status='published'.

Root-caused 2026-08-27 (three separate incidents in one night -- HesabPay
08-22, AlgoRank 08-26, Al Goanna 08-27): nothing in this codebase enforces
"an article publish must not silently duplicate an already-live article for
the same service" or "a published row must have a slug" as a SINGLE rule --
each of ~6 call sites across backend/ and workers/ that transition a row
into status='published' re-implements these invariants ad hoc, and each
incident was a different call site missing a different piece.

This class does NOT reimplement article persistence -- `insert_stored_article`/
`replace_article_content` (workers-only; article *creation* and content
*rewriting* are exclusively pipeline operations, backend never creates a row
from scratch) stay exactly where they are. `Article` wraps only the
genuinely cross-service primitives (`transition_article_status`,
`ensure_article_slug`, `article_matching.published_rows_for_service`) that
both services already call independently for status transitions, and adds
the one thing neither had: `publish()` refuses outright, raising
`DuplicateArticleError`, when a DIFFERENT article_id already owns a live
published article for the same service_id. Every existing call site that
currently calls `transition_article_status(..., new_status="published", ...)`
should call `Article(...).publish()` instead -- same effect, plus the guard.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

# Mirrors article_transitions._ARTICLES_COLUMNS exactly -- this IS that
# table's row shape, not a second, possibly-drifting definition of it.
_ARTICLES_COLUMNS = (
    "status", "year", "published_at", "article_id", "service_id", "title", "summary", "body",
    "image_url", "tags", "source_url", "trigger_txid", "trigger_round", "slug", "translations",
    "first_published_at", "updated_at", "prompt_version", "composed_by_model",
    "deleted_at", "status_updated_at", "interest_score", "approved_at",
)  # fmt: skip


class DuplicateArticleError(Exception):
    """Raised by `Article.publish()` when a DIFFERENT article_id already owns a live published article for the same service_id.

    This is the escalation: a caller hitting this almost certainly meant to
    edit/replace the existing article (see `replace_article_content` in
    workers' article_store.py, or `apply_recomposed_article`), not create a
    second one. Never silently downgraded to a log line -- every `logger.error`
    in this codebase is already wired to Bugsnag on prod (confirmed
    2026-08-27), so raising here both stops the bad write AND pages someone.
    """

    def __init__(self, *, service_id: str, article_id: str, existing_article_id: str) -> None:
        """Store the conflicting ids for a caller to catch and route to an edit/replace path."""
        self.service_id = service_id
        self.article_id = article_id
        self.existing_article_id = existing_article_id
        super().__init__(
            f"service {service_id!r} already has a published article "
            f"({existing_article_id}) -- refusing to also publish {article_id} "
            "as a second one; use replace_content/apply_recomposed_article instead"
        )


@dataclass
class Article:
    """One `articles` row. Load via `Article.load(article_id)`; transition via `.publish()`/`.hold()`/`.delete()`."""

    status: str
    year: int
    published_at: datetime
    article_id: UUID
    service_id: str | None
    title: str
    summary: str
    body: str
    image_url: str | None
    tags: list[str]
    source_url: str | None
    trigger_txid: str | None
    trigger_round: int | None
    slug: str | None
    translations: dict[str, str] | None
    first_published_at: datetime | None
    updated_at: datetime | None
    prompt_version: str | None
    composed_by_model: str | None
    deleted_at: datetime | None
    status_updated_at: datetime | None
    interest_score: float | None
    approved_at: datetime | None

    @classmethod
    def _from_row(cls, row: Any) -> Article:  # noqa: ANN401 -- duck-typed Cassandra driver row
        return cls(**{f.name: getattr(row, f.name) for f in fields(cls)})

    @classmethod
    def load(cls, article_id: UUID | str) -> Article | None:
        """Read the current state of one article by id, or None if it doesn't exist."""
        from app.core.cassandra import get_cassandra_session

        from algorand_shared.article_statements import ArticlesStmts

        aid = article_id if isinstance(article_id, UUID) else UUID(str(article_id))
        row = get_cassandra_session().execute(ArticlesStmts.GET_FULL_BY_ID, (aid,)).one()
        return cls._from_row(row) if row is not None else None

    def _conflicting_published_article_id(self) -> str | None:
        """Another article_id, currently status='published', for this same service_id -- or None. Fails open (None) on any lookup error, matching `service_has_article`'s own posture: a transient store blip must never itself block a legitimate publish."""
        sid = (self.service_id or "").strip().lower()
        if not sid:
            return None
        try:
            from algorand_shared.article_matching import published_rows_for_service

            for row in published_rows_for_service(sid):
                if str(row.article_id) != str(self.article_id):
                    return str(row.article_id)
        except Exception:
            logger.warning(
                "Article._conflicting_published_article_id: lookup failed for %s -- "
                "failing open (no conflict detected)",
                sid,
                exc_info=True,
            )
        return None

    def publish(self, *, new_published_at: datetime | None = None) -> None:
        """Transition to status='published' and ensure a slug -- refusing if a different article already owns this service_id's live slot.

        Raises `DuplicateArticleError` on conflict; raises nothing else
        (mirrors `transition_article_status`'s own contract) but returns
        without effect if this article_id no longer exists.
        """
        conflict = self._conflicting_published_article_id()
        if conflict is not None:
            logger.error(
                "Article.publish: refusing -- service %r already has a published "
                "article (%s); %s would be a duplicate",
                self.service_id,
                conflict,
                self.article_id,
            )
            raise DuplicateArticleError(
                service_id=str(self.service_id),
                article_id=str(self.article_id),
                existing_article_id=conflict,
            )

        from algorand_shared.article_transitions import transition_article_status

        transition_article_status(
            self.article_id,
            new_status="published",
            new_published_at=new_published_at or datetime.now(tz=UTC),
        )
        self.ensure_slug()

    def hold(self) -> None:
        """Transition to status='on_hold' -- an unlisted candidate, never on the public feed."""
        from algorand_shared.article_transitions import transition_article_status

        transition_article_status(self.article_id, new_status="on_hold")

    def delete(self, *, deleted_at: datetime | None = None) -> None:
        """Transition to status='deleted' (410 tombstone, not a hard delete)."""
        from algorand_shared.article_transitions import transition_article_status

        transition_article_status(
            self.article_id, new_status="deleted", deleted_at=deleted_at or datetime.now(tz=UTC)
        )

    def ensure_slug(self) -> str | None:
        """Claim a permanent slug if this article doesn't already have one. No-op read when it does."""
        from app.core.cassandra import get_cassandra_session

        from algorand_shared.article_statements import ArticlesStmts
        from algorand_shared.slugs import ensure_article_slug

        slug = ensure_article_slug(self.article_id, self.title)
        if not slug:
            return None
        session = get_cassandra_session()
        current = session.execute(ArticlesStmts.GET_BY_ID, (self.article_id,)).one()
        if current is None:
            return slug
        session.execute(
            ArticlesStmts.SET_SLUG,
            (slug, current.status, current.year, current.published_at, self.article_id),
        )
        from algorand_shared.article_tag_index import set_slug_in_tag_index

        set_slug_in_tag_index(
            self.article_id, tags=list(self.tags or []), published_at=current.published_at, slug=slug
        )
        self.slug = slug
        return slug
