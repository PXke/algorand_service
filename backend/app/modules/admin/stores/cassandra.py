"""Cassandra-backed reads/writes for the admin dashboard (review queue, publish queue, domains, feedback)."""

from __future__ import annotations

import contextlib
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse
from uuid import UUID

from app.core import serialization

if TYPE_CHECKING:
    from cassandra.cluster import Session as CassandraSession
from app.modules.admin.classifier_constants import (
    CONTENT_CATEGORIES,
    is_content_category,
    normalize_content_category,
)
from app.modules.news.stores.base import StoredArticle

logger = logging.getLogger(__name__)

# Max review items a single source domain may occupy in the admin window, so a
# burst of pages from one site can't crowd out everything else. Over-cap items
# stay pending and resurface once higher-ranked ones are cleared.
_MAX_REVIEWS_PER_SOURCE = 3


def _review_domain(url: str) -> str:
    host = (urlparse(url or "").hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _rank_reviews(
    items: list[dict], *, limit: int, per_source: int = _MAX_REVIEWS_PER_SOURCE
) -> list[dict]:
    """Order pending reviews by classifier promise score (desc), capping how many items any one source domain may occupy so a flood of pages from a single site can't dominate the window. Over-cap items are intentionally held back (the window may be shorter than ``limit``) and resurface once the higher-ranked ones ahead of them are cleared."""
    ordered = sorted(items, key=lambda d: d.get("storage_score") or 0.0, reverse=True)
    result: list[dict] = []
    taken: dict[str, int] = {}
    for item in ordered:
        if len(result) >= limit:
            break
        dom = _review_domain(item.get("url", ""))
        if taken.get(dom, 0) >= per_source:
            continue
        result.append(item)
        taken[dom] = taken.get(dom, 0) + 1
    return result


class AdminCassandraStore:
    """Cassandra reads/writes for the admin dashboard."""

    def get_article(self, article_id: str) -> StoredArticle | None:
        """Fetch one article by id, or None if it does not exist."""
        from app.modules.news.stores.cassandra import CassandraArticleStore

        return CassandraArticleStore().get(article_id)

    def update_article(
        self,
        article_id: str,
        *,
        title: str | None = None,
        summary: str | None = None,
        body: str | None = None,
        editor: str = "admin",
    ) -> StoredArticle | None:
        """Patch an article's fields and re-publish it, re-stamping published_at.

        Clears every stored translation when the content actually changes and
        re-enqueues all languages fresh -- an admin correction previously left
        each language's translation exactly as it was BEFORE the correction,
        silently wrong in every non-English locale with no way to detect it
        short of a manual audit (found live 2026-08 on more than one article).
        Mirrors what workers' replace_article_content already does on every
        recompose; this path just never had the same fix.
        """
        current = self.get_article(article_id)
        if current is None:
            return None
        new_title = title if title is not None else current.title
        new_summary = summary if summary is not None else current.summary
        new_body = body if body is not None else current.body
        content_changed = (
            new_title != current.title
            or new_summary != current.summary
            or new_body != current.body
        )
        self._save_version_snapshot(current, editor=editor)
        self._write_article(current, new_title, new_summary, new_body, tag_extra="updated")
        if content_changed and current.translations:
            self._clear_and_reenqueue_translations(article_id)
        updated = self.get_article(article_id)
        if updated is not None:
            # Re-index the edited content so search reflects the new
            # title/summary/body immediately, instead of waiting on the
            # once-daily reindex_articles safety net. Best-effort, never
            # blocks the admin edit.
            with contextlib.suppress(Exception):
                from app.core.typesense_client import upsert_article_document

                upsert_article_document(
                    article_id=article_id,
                    title=updated.title,
                    summary=updated.summary,
                    body=updated.body,
                    service_id=updated.service_id,
                    published_at_epoch=updated.published_at_epoch,
                )
            # Content changed at its existing URL — notify IndexNow (Bing asks
            # for update pings, not just adds). Best-effort, never blocks.
            with contextlib.suppress(Exception):
                from app.modules.seo.indexnow import ping_article, translation_lang_codes

                ping_article(
                    article_id,
                    translation_langs=translation_lang_codes(updated.translations),
                    slug=updated.slug,
                )
        return updated

    @staticmethod
    def _clear_and_reenqueue_translations(article_id: str) -> None:
        """Wipe every stored translation and re-enqueue all languages via the modern batch task (app.tasks.newspaper.translate_article_batch) -- NOT the legacy per-language translate_article shim _enqueue_article_translations uses, which calls a paid LLM directly and skips the local-engine/DeepSeek-per-language routing the batch task has (see workers' DEEPSEEK_TRANSLATE_LANGS)."""
        try:
            from celery import Celery

            from app.core.article_translation_langs import ARTICLE_TRANSLATION_LANGS
            from app.core.cassandra import get_cassandra_session
            from app.core.config import settings

            session = get_cassandra_session()
            # `articles` table clear. Best-effort.
            with contextlib.suppress(Exception):
                from algorand_shared.article_statements import ArticlesStmts

                aid = UUID(article_id)
                new_row = session.execute(ArticlesStmts.GET_BY_ID, (aid,)).one()
                if new_row is not None:
                    session.execute(
                        ArticlesStmts.CLEAR_TRANSLATIONS,
                        (new_row.status, new_row.year, new_row.published_at, aid),
                    )
            Celery(broker=settings.celery_broker_url).send_task(
                "app.tasks.newspaper.translate_article_batch",
                args=[str(article_id), list(ARTICLE_TRANSLATION_LANGS)],
                queue="translate",
            )
        except Exception:
            logger.warning(
                "failed to clear/re-enqueue translations for %s", article_id, exc_info=True
            )

    def _save_version_snapshot(self, article: StoredArticle, *, editor: str) -> None:
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ArticleVersionStmts

        try:
            aid = UUID(article.article_id)
        except ValueError:
            return
        session = get_cassandra_session()
        version = 1
        try:
            row = session.execute(ArticleVersionStmts.LATEST, (aid,)).one()
            if row and row.version is not None:
                version = int(row.version) + 1
        except Exception:
            logger.warning("failed to read latest version for article %s", aid, exc_info=True)
        now = datetime.now(tz=UTC)
        with contextlib.suppress(Exception):
            session.execute(
                ArticleVersionStmts.INSERT,
                (
                    aid,
                    version,
                    article.title,
                    article.summary,
                    article.body,
                    "before_admin_edit",
                    editor,
                    now,
                ),
            )

    def list_article_versions(self, article_id: str) -> list[dict]:
        """Version history for one article, newest first -- title/editor/reason/date only (no body; that's a separate fetch per version, since bodies can be large and a list view never needs more than one at a time)."""
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ArticleVersionStmts

        try:
            aid = UUID(article_id)
        except ValueError:
            return []
        session = get_cassandra_session()
        rows = session.execute(ArticleVersionStmts.LIST, (aid, 200))
        items = [
            {
                "version": int(row.version),
                "title": row.title,
                "edit_reason": row.edit_reason,
                "editor": row.editor,
                "edited_at": row.edited_at.isoformat() if row.edited_at else None,
            }
            for row in rows
        ]
        items.sort(key=lambda it: it["version"], reverse=True)
        return items

    def get_article_version(self, article_id: str, version: int) -> dict | None:
        """Full content (title/summary/body) of one prior version, for the admin diff view."""
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ArticleVersionStmts

        try:
            aid = UUID(article_id)
        except ValueError:
            return None
        session = get_cassandra_session()
        row = session.execute(ArticleVersionStmts.GET_ONE, (aid, int(version))).one()
        if row is None:
            return None
        return {
            "version": int(row.version),
            "title": row.title,
            "summary": row.summary,
            "body": row.body,
            "edit_reason": row.edit_reason,
            "editor": row.editor,
            "edited_at": row.edited_at.isoformat() if row.edited_at else None,
        }

    def _write_article(
        self,
        current: StoredArticle,
        title: str,
        summary: str,
        body: str,
        *,
        tag_extra: str = "",
    ) -> None:
        from algorand_shared.article_statements import ArticlesStmts

        from app.core.cassandra import get_cassandra_session

        aid = UUID(current.article_id)
        tags = list(current.tags or [])
        if tag_extra and tag_extra not in tags:
            tags.append(tag_extra)
        session = get_cassandra_session()
        # Content-only `articles` table update: status/published_at (the
        # partition key) are left untouched, so this naturally preserves feed
        # membership exactly as before -- a draft's row stays status='draft'
        # (invisible to the public feed, which only reads status='published'
        # rows), a published article's row stays visible with its content
        # refreshed in place. No separate feed-projection sync step needed
        # (article-table consolidation Phase 5: `articles` IS the feed
        # projection for status='published' rows).
        with contextlib.suppress(Exception):
            old_row = session.execute(ArticlesStmts.GET_FULL_BY_ID, (aid,)).one()
            if old_row is not None:
                session.execute(
                    ArticlesStmts.UPDATE_CONTENT,
                    (
                        title,
                        summary,
                        body,
                        tags,
                        old_row.image_url,
                        old_row.updated_at,
                        old_row.status,
                        old_row.year,
                        old_row.published_at,
                        aid,
                    ),
                )
                # This is a raw UPDATE, not a transition_article_status() call
                # (published_at/status don't move for an in-place content
                # edit), so it doesn't get invalidation for free the way
                # delete_article/set_article_draft/review-approve do below —
                # bust the feed's first-page cache explicitly.
                from algorand_shared.feed_cache import invalidate_feed_first_page

                invalidate_feed_first_page()
                # articles_by_tag dual-write: published_at (part of the
                # `articles` partition key) is untouched by this in-place
                # edit, but tags can change -- reconcile the tag index too.
                from algorand_shared.article_tag_index import sync_tag_index

                sync_tag_index(
                    aid,
                    old_status=old_row.status,
                    old_tags=list(old_row.tags or []),
                    old_published_at=old_row.published_at,
                    new_status=old_row.status,
                    new_tags=tags,
                    new_published_at=old_row.published_at,
                    service_id=old_row.service_id,
                    title=title,
                    summary=summary,
                    image_url=old_row.image_url,
                    source_url=old_row.source_url,
                    slug=old_row.slug,
                    translations=dict(old_row.translations) if old_row.translations else None,
                    first_published_at=old_row.first_published_at,
                    updated_at=old_row.updated_at,
                )

    def delete_article(self, article_id: str) -> bool:
        """Delete an article; returns False if it did not exist."""
        current = self.get_article(article_id)
        if current is None:
            return False

        from datetime import UTC, datetime

        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ArticleVersionStmts

        try:
            aid = UUID(article_id)
        except ValueError:
            return False

        session = get_cassandra_session()
        with contextlib.suppress(Exception):
            from app.modules.seo.sitemap import bust_tombstone_cache

            bust_tombstone_cache()

        try:
            version_rows = session.execute(ArticleVersionStmts.LIST_VERSIONS, (aid,))
            for row in version_rows:
                session.execute(ArticleVersionStmts.DELETE, (aid, row.version))
        except Exception:
            logger.warning("failed to delete version rows for article %s", aid, exc_info=True)

        # Deletion is an `articles` status transition ('deleted', tombstoned +
        # deleted_at set) not a hard row delete -- the SSR route serves 410
        # Gone for tombstoned ids so search engines drop the URL fast instead
        # of retrying a 404 for months. Best-effort, doesn't affect the
        # delete's success either way.
        with contextlib.suppress(Exception):
            from algorand_shared.article_transitions import transition_article_status

            transition_article_status(aid, new_status="deleted", deleted_at=datetime.now(tz=UTC))
        with contextlib.suppress(Exception):
            from app.core.typesense_client import delete_article_document

            delete_article_document(article_id)
        # Bing's guidelines: notify IndexNow on REMOVAL too — the submitted URL
        # gets recrawled, hits the 410 tombstone above, and drops out of the
        # index instead of lingering. Best-effort, never blocks the delete.
        with contextlib.suppress(Exception):
            from app.modules.seo.indexnow import ping_article, translation_lang_codes

            ping_article(
                article_id,
                translation_langs=translation_lang_codes(current.translations),
                slug=current.slug,
            )
        return True

    def set_article_draft(self, article_id: str, draft: bool) -> StoredArticle | None:
        """Toggle an article's admin-only draft flag, reversibly: status flips between 'draft' and 'published' on the `articles` row, published_at unchanged -- restoring makes the SAME row visible again (status='published' is what the public feed reads), not a re-publish. Returns None if the article does not exist."""
        from algorand_shared.article_statements import ArticlesStmts

        from app.core.cassandra import get_cassandra_session

        try:
            aid = UUID(article_id)
        except ValueError:
            return None
        session = get_cassandra_session()
        if session.execute(ArticlesStmts.GET_BY_ID, (aid,)).one() is None:
            return None

        with contextlib.suppress(Exception):
            from algorand_shared.article_transitions import transition_article_status

            transition_article_status(aid, new_status="draft" if draft else "published")

        updated = self.get_article(article_id)

        # Site search must not be able to find (or show a snippet of) a
        # drafted article either, independent of the direct-URL gate.
        with contextlib.suppress(Exception):
            from app.core.typesense_client import delete_article_document, upsert_article_document

            if draft:
                delete_article_document(article_id)
            elif updated is not None:
                upsert_article_document(
                    article_id=article_id,
                    title=updated.title,
                    summary=updated.summary,
                    body=updated.body,
                    service_id=updated.service_id,
                    published_at_epoch=updated.published_at_epoch,
                )

        with contextlib.suppress(Exception):
            from app.modules.seo.indexnow import ping_article, translation_lang_codes

            # Draft on: recrawl finds nothing at the URL (404) and drops it.
            # Draft off: recrawl finds the restored article again.
            if updated is not None:
                ping_article(
                    article_id,
                    translation_langs=translation_lang_codes(updated.translations),
                    slug=updated.slug,
                )
        return updated

    def list_draft_articles(self) -> list[dict]:
        """Currently-drafted articles, for the admin UI's restore list -- these are absent from articles_feed by design, so the normal feed listing can never surface them.

        2026-08-24: enumeration reads `articles` directly (was
        `draft_articles`, which could hold ghost rows left behind by a
        delete that hadn't cleaned up the index -- authoritative status
        enumeration makes that class of bug impossible here). status_updated_at
        (migration 070) is the drafted_at equivalent -- the old table's own
        drafted_at column had no counterpart on `articles` until this.
        """
        from datetime import UTC, datetime

        from algorand_shared.article_statements import ArticlesStmts

        from app.core.cassandra import get_cassandra_session
        from app.modules.news.stores.cassandra import CassandraArticleStore

        session = get_cassandra_session()
        current_year = datetime.now(tz=UTC).year
        rows = []
        for year in range(current_year, current_year - 3, -1):
            rows.extend(session.execute(ArticlesStmts.LIST_IDS_BY_STATUS, ("draft", year)))
        if not rows:
            return []
        stored = CassandraArticleStore().get_many([str(row.article_id) for row in rows])
        items = []
        for row in rows:
            aid = str(row.article_id)
            article = stored.get(aid)
            if article is None or not article.draft:
                continue
            drafted_at = row.status_updated_at
            items.append(
                {
                    "article_id": aid,
                    "title": article.title,
                    "source_url": article.source_url or "",
                    "drafted_at": drafted_at.isoformat() if drafted_at else None,
                }
            )
        items.sort(key=lambda it: it["drafted_at"] or "", reverse=True)
        return items

    def create_brief(
        self,
        *,
        title: str,
        body_markdown: str,
        keywords: str,
        status: str,
        wallet_address: str,
        refresh_every_days: int = 0,
        is_special_edition: bool = False,
    ) -> dict:
        """Insert a new editorial brief."""
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import EditorialBriefStmts

        brief_id = uuid.uuid4()
        now = datetime.now(tz=UTC)
        session = get_cassandra_session()
        session.execute(
            EditorialBriefStmts.INSERT,
            (
                brief_id,
                title,
                body_markdown,
                keywords,
                status,
                wallet_address,
                now,
                now,
                refresh_every_days,
                is_special_edition,
            ),
        )
        return {
            "brief_id": str(brief_id),
            "title": title,
            "status": status,
            "refresh_every_days": refresh_every_days,
            "created_at_epoch": int(now.timestamp()),
        }

    def list_briefs(self, *, limit: int = 50) -> list[dict]:
        """List editorial briefs, newest first."""
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import EditorialBriefStmts

        session = get_cassandra_session()
        try:
            rows = session.execute(EditorialBriefStmts.LIST, (limit,))
        except Exception:
            return []
        items = []
        for row in rows:
            created = row.created_at
            last_run = row.last_run_at
            items.append(
                {
                    "brief_id": str(row.brief_id),
                    "title": row.title,
                    "keywords": row.keywords or "",
                    "status": row.status or "draft",
                    "wallet_address": row.wallet_address or "",
                    "created_at_epoch": int(created.timestamp()) if created else 0,
                    "refresh_every_days": int(row.refresh_every_days or 0),
                    "last_run_at_epoch": int(last_run.timestamp()) if last_run else 0,
                    "linked_article_id": (
                        str(row.linked_article_id) if row.linked_article_id else ""
                    ),
                    "is_special_edition": bool(row.is_special_edition),
                }
            )
        return items

    def get_brief(self, brief_id: str) -> dict | None:
        """Fetch one editorial brief by id, or None if it does not exist."""
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import EditorialBriefStmts

        try:
            bid = UUID(brief_id)
        except ValueError:
            return None
        session = get_cassandra_session()
        row = session.execute(EditorialBriefStmts.GET, (bid,)).one()
        if row is None:
            return None
        created = row.created_at
        updated = row.updated_at
        last_run = row.last_run_at
        return {
            "brief_id": str(row.brief_id),
            "title": row.title,
            "body_markdown": row.body_markdown or "",
            "keywords": row.keywords or "",
            "status": row.status or "",
            "wallet_address": row.wallet_address or "",
            "created_at_epoch": int(created.timestamp()) if created else 0,
            "updated_at_epoch": int(updated.timestamp()) if updated else 0,
            "refresh_every_days": int(row.refresh_every_days or 0),
            "last_run_at_epoch": int(last_run.timestamp()) if last_run else 0,
            "linked_article_id": str(row.linked_article_id) if row.linked_article_id else "",
            "is_special_edition": bool(row.is_special_edition),
        }

    def _grade_meta_for_review(self, review_id: str) -> dict[str, str]:
        """Pull the article grade + subscores from a review item so they're stored alongside the accept/reject label (trainable features)."""
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ClassifierReviewStmts

        out: dict[str, str] = {}
        try:
            row = (
                get_cassandra_session()
                .execute(ClassifierReviewStmts.GET_METADATA, (UUID(review_id),))
                .one()
            )
            raw = dict(row.metadata or {}).get("raw") if row else None
            if raw:
                parsed = serialization.loads(raw)
                if parsed.get("grade") is not None:
                    out["grade"] = str(parsed["grade"])
                gd = parsed.get("grade_detail")
                if gd is not None:
                    out["grade_detail"] = (
                        gd if isinstance(gd, str) else serialization.dumps(gd)
                    )
        except Exception:
            logger.debug("failed to parse review metadata for %s", review_id, exc_info=True)
        return out

    def training_stats(self) -> dict:
        """Labelled-data volume + balance + grader readiness for the Training tab.

        `graded_*` are rows that captured grade dimensions (only since the capture
        was added) — those are what the learned grader trains on.
        """
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ClassifierFeedbackStmts

        session = get_cassandra_session()
        rows = list(session.execute(ClassifierFeedbackStmts.LIST_BY_TIME, ("main",)))
        total = len(rows)
        approved = sum(1 for r in rows if r.approved)
        graded = graded_pos = graded_neg = 0
        # Fetch the grade-dimension detail rows in ONE concurrent batch instead of
        # up to 400 sequential round-trips (this was the Training tab's slow point).
        from app.core.cassandra import execute_parallel_with_args

        for ok, res in execute_parallel_with_args(
            ClassifierFeedbackStmts.GET_GRADE,
            [(r.feedback_id,) for r in rows[:400]],
            concurrency=64,
            raise_on_error=False,
        ):
            if not ok:
                continue
            detail = res.one()
            if detail is None or not detail.metadata:
                continue
            if dict(detail.metadata).get("grade_detail"):
                graded += 1
                if detail.approved:
                    graded_pos += 1
                else:
                    graded_neg += 1
        min_samples = 40  # matches workers GRADER_MIN_SAMPLES
        return {
            "total_labeled": total,
            "approved": approved,
            "rejected": total - approved,
            "graded_trainable": graded,
            "graded_approved": graded_pos,
            "graded_rejected": graded_neg,
            "min_samples": min_samples,
            "ready_to_train": graded >= min_samples and graded_pos > 0 and graded_neg > 0,
        }

    def record_classifier_feedback(
        self,
        *,
        url: str,
        text_sample: str,
        category: str,
        predicted_category: str | None,
        quality: str,
        predicted_publish: bool,
        approved: bool,
        admin_wallet: str,
        review_id: str | None = None,
        article_id: str | None = None,
        source_relevant: bool = True,
        categories: list | None = None,
        training_only: bool = False,
        corrected_scores: dict | None = None,
        anchor: bool = False,
        factuality_fail: bool = False,
        tone_fail: bool = False,
        error_types: list | None = None,
    ) -> dict:
        """Record a human correction to a classifier verdict for later retraining."""
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ClassifierFeedbackStmts

        predicted = (predicted_category or category).strip().lower()
        corrected = category.strip().lower()
        cats = [c.strip().lower() for c in (categories or []) if c and c.strip()]
        if corrected and corrected not in cats:
            cats.insert(0, corrected)
        feedback_id = uuid.uuid4()
        now = datetime.now(tz=UTC)
        feedback_meta = self._classifier_feedback_meta(
            review_id=review_id, corrected_scores=corrected_scores, article_id=article_id
        )
        # Gatekeeper validation anchor: the human ground truth the annotator is
        # checked against. Written to the dedicated gatekeeper_anchors table (the
        # single source of truth), isolated from all model training.
        if anchor:
            with contextlib.suppress(Exception):
                self.record_gatekeeper_anchor(
                    article_id=article_id or "",
                    url=url,
                    source_text=text_sample,
                    article_text=feedback_meta.get("article_text", ""),
                    factuality_fail=bool(factuality_fail),
                    tone_fail=bool(tone_fail),
                    error_types=[str(t) for t in (error_types or [])],
                    admin_wallet=admin_wallet,
                )

        session = get_cassandra_session()
        session.execute(
            ClassifierFeedbackStmts.INSERT,
            (
                feedback_id,
                url,
                text_sample[:8000],
                corrected,
                predicted,
                quality,
                predicted_publish,
                approved,
                admin_wallet,
                now,
                feedback_meta,
            ),
        )
        session.execute(
            ClassifierFeedbackStmts.INSERT_BY_TIME,
            ("main", now, feedback_id, url, approved),
        )
        self._apply_classifier_corrections(
            url=url,
            corrected_category=corrected,
            predicted_category=predicted,
            quality=quality,
            article_id=article_id,
            _approved=approved,
            source_relevant=source_relevant,
        )
        self._apply_classifier_feedback_effects(
            url=url,
            article_id=article_id,
            review_id=review_id,
            approved=approved,
            training_only=training_only,
        )
        return {
            "feedback_id": str(feedback_id),
            "approved": approved,
            "category": corrected,
            "predicted_category": predicted,
            "quality": quality,
        }

    def _classifier_feedback_meta(
        self, *, review_id: str | None, corrected_scores: dict | None, article_id: str | None
    ) -> dict:
        """Build the trainable feedback_meta blob: point-in-time grade dimensions (novelty/recency can't be recomputed later, so must be snapshotted here), any human-corrected scores (ground truth the grader prefers over auto-scores), and a text snapshot of the graded article (text-aware grader training pairs)."""
        feedback_meta = self._grade_meta_for_review(review_id) if review_id else {}
        if corrected_scores:
            feedback_meta["corrected_scores"] = serialization.dumps(
                {k: float(v) for k, v in corrected_scores.items()}
            )
        if article_id:
            try:
                art = self.get_article(article_id)
                if art is not None and getattr(art, "body", ""):
                    feedback_meta["article_text"] = (f"{getattr(art, 'title', '')}\n{art.body}")[
                        :8000
                    ]
            except Exception:
                logger.warning(
                    "failed to snapshot article text for feedback on %s",
                    article_id,
                    exc_info=True,
                )
        return feedback_meta

    def _apply_classifier_feedback_effects(
        self,
        *,
        url: str,
        article_id: str | None,
        review_id: str | None,
        approved: bool,
        training_only: bool,
    ) -> None:
        """Post-write side effects of a classifier-feedback decision: resolve the pending review, blocklist a rejected URL, publish/apply-recompose an approved draft, and kick the next candidate."""
        if review_id:
            resolution = "approved" if approved else "rejected"
            self._complete_classifier_review(review_id, resolution=resolution)
        # A rejected URL is a strong "not relevant" signal — keep the worker from
        # re-enqueueing it until the classifier has a chance to learn from this.
        if not approved:
            self._record_url_rejected(url)
        # Training mode records the label (above) but never publishes — the
        # bootstrap sprint's low-quality articles must not reach the live feed.
        if approved and article_id and not training_only:
            # Archive-refresh reviews (recompose_published) don't publish the
            # draft as a NEW article: the draft's content replaces the live
            # article in place (same URL/published_at, updated_at stamped).
            replaces = self._review_replaces_article_id(review_id) if review_id else ""
            if replaces:
                self._trigger_apply_recompose(article_id, replaces)
            else:
                self._publish_or_queue_article(article_id)
        # A review slot just freed — generate the next-highest-interest
        # candidate now instead of waiting for the next scheduled drain.
        self._trigger_compose_next()

    def record_gatekeeper_anchor(
        self,
        *,
        article_id: str,
        url: str,
        source_text: str,
        article_text: str,
        factuality_fail: bool,
        tone_fail: bool,
        error_types: list,
        admin_wallet: str,
    ) -> str:
        """Write a validation anchor (immutable ground truth for the annotator).

        article_text is snapshotted so the anchor is fixed even if the article
        later changes. Returns the anchor id.
        """
        from cassandra.util import uuid_from_time

        from app.core.cassandra import get_cassandra_session
        from app.core.statements import GatekeeperStmts

        # If an article_text was not passed in, snapshot it now from the article.
        if not article_text and article_id:
            try:
                art = self.get_article(article_id)
                if art is not None and getattr(art, "body", ""):
                    article_text = f"{getattr(art, 'title', '')}\n{art.body}"[:8000]
            except Exception:
                logger.warning(
                    "failed to snapshot article text for anchor on %s",
                    article_id,
                    exc_info=True,
                )
        now = datetime.now(tz=UTC)
        anchor_id = uuid_from_time(now)
        get_cassandra_session().execute(
            GatekeeperStmts.INSERT_ANCHOR,
            (
                now,
                anchor_id,
                article_id or "",
                url[:512],
                (source_text or "")[:8000],
                (article_text or "")[:8000],
                bool(factuality_fail),
                bool(tone_fail),
                [str(t) for t in (error_types or [])],
                admin_wallet,
            ),
        )
        return str(anchor_id)

    def list_gatekeeper_anchors(self, *, limit: int = 200) -> dict:
        """List anchors (newest-first, deduped to the latest tag per article).

        Returns {count, target, items}.
        """
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import GatekeeperStmts

        rows = get_cassandra_session().execute(GatekeeperStmts.LIST_ANCHORS, (limit,))
        seen: set[str] = set()
        items: list[dict] = []
        for r in rows:
            key = r.article_id or str(r.anchor_id)
            if key in seen:
                continue
            seen.add(key)
            items.append(
                {
                    "anchor_id": str(r.anchor_id),
                    "article_id": r.article_id or "",
                    "url": r.url or "",
                    "factuality_fail": bool(r.factuality_fail),
                    "tone_fail": bool(r.tone_fail),
                    "error_types": list(r.error_types or []),
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
            )
        return {"count": len(items), "target": 40, "items": items}

    def get_gatekeeper_validation_report(self) -> dict | None:
        """Latest annotator-validation report, or None if never run."""
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import GatekeeperStmts

        row = get_cassandra_session().execute(GatekeeperStmts.GET_REPORT).one()
        if row is None or not row.report_json:
            return None
        try:
            report = serialization.loads(row.report_json)
        except Exception:
            report = {}
        return {
            "computed_at": row.computed_at.isoformat() if row.computed_at else None,
            "n_anchors": row.n_anchors,
            "trusted_count": row.trusted_count,
            "report": report,
        }

    # Multi-label public suffixes where eTLD+1 needs three labels (foo.co.uk).
    # Mirrors workers' domain_tracker._MULTI_LABEL_SUFFIXES — keep in sync.
    _MULTI_LABEL_SUFFIXES = frozenset(
        {
            "co.uk",
            "org.uk",
            "gov.uk",
            "ac.uk",
            "me.uk",
            "ltd.uk",
            "plc.uk",
            "co.jp",
            "co.kr",
            "co.za",
            "co.nz",
            "co.in",
            "co.il",
            "co.id",
            "co.th",
            "com.au",
            "com.br",
            "com.mx",
            "com.tr",
            "com.cn",
            "com.sg",
            "com.hk",
            "com.tw",
            "com.ar",
            "com.co",
            "com.ua",
            "com.pl",
            "com.ng",
            "ac.in",
            "edu.in",
            "gov.in",
            "res.in",
            "nic.in",
            "org.in",
            "net.in",
            "or.jp",
            "ne.jp",
            "ac.jp",
            "ad.jp",
            "ed.jp",
            "go.jp",
            "gr.jp",
            "lg.jp",
        }
    )

    # Platform / hosting suffixes where the SUBDOMAIN is the real identity
    # (foo.medium.com != bar.medium.com). Keep in sync with workers'
    # domain_tracker._PLATFORM_SUFFIXES; parity guarded by
    # test_domain_from_url_parity.py in both services.
    _PLATFORM_SUFFIXES = frozenset(
        {
            "medium.com",
            "substack.com",
            "blogspot.com",
            "wordpress.com",
            "ghost.io",
            "github.io",
            "gitbook.io",
            "gitbook.com",
            "notion.site",
            "super.site",
            "netlify.app",
            "vercel.app",
            "pages.dev",
            "web.app",
            "firebaseapp.com",
            "herokuapp.com",
            "onrender.com",
            "readthedocs.io",
            "ipfs.io",
            "w3s.link",
            "fleek.co",
            "surge.sh",
            "webflow.io",
            "wixsite.com",
            "replit.app",
            "repl.co",
        }
    )

    @staticmethod
    def _domain_from_url(url: str) -> str:
        """Registrable domain (eTLD+1) — collapses subdomains so accepting e.g.

        blog.perawallet.app keys the frontier on perawallet.app, matching the
        workers' domain_from_url. Platform/public suffixes keep the subdomain
        (foo.medium.com stays distinct) so unrelated sources don't merge.
        """
        raw = url.strip()
        # A bare hostname with no scheme reads as a PATH to urlparse, not a
        # netloc -- .hostname comes back empty. Kept in parity with workers'
        # domain_from_url (see its comment, 2026-08-07); the "." guard keeps
        # a genuinely non-URL string like "not-a-url" still returning "".
        if "://" not in raw and "." in raw:
            raw = f"https://{raw}"
        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower().strip(".")
        if not host:
            return ""
        labels = host.split(".")
        if len(labels) <= 2:
            return host
        last_two = ".".join(labels[-2:])
        if (
            last_two in AdminCassandraStore._MULTI_LABEL_SUFFIXES
            or last_two in AdminCassandraStore._PLATFORM_SUFFIXES
        ):
            return ".".join(labels[-3:]) if len(labels) >= 3 else host
        return last_two

    @staticmethod
    def _normalize_domain_input(domain: str) -> str:
        """Normalize an admin-supplied domain (bare, e.g. "www.urvote.ca", or a full URL) to the SAME eTLD+1 key ``_domain_from_url`` derives from a real URL during crawling — that function requires a scheme to parse a hostname at all, so passing a bare domain straight to it returns "" and silently skips normalization.

        Root-caused 2026-07-24 (urvote.ca): an admin approved "www.urvote.ca"
        (the real canonical/redirect host, not a mistake) and it was written
        to domain_tracking verbatim. The crawler's own is_admin_approved_domain
        check always looks up domain_from_url(page_url), which collapses
        "www.urvote.ca" -> "urvote.ca" — so the approval never matched, the
        admin-approved bypass never fired, and the page was rejected by the
        ordinary thin-content quality gate despite being explicitly approved.
        Normalizing at write time means the write key always matches what
        every read path derives from a URL, regardless of which form (bare
        domain, with/without www, or a full URL) an admin/user enters.
        """
        raw = domain.strip()
        if "://" not in raw:
            raw = f"https://{raw}"
        return AdminCassandraStore._domain_from_url(raw) or domain.strip().lower()

    def _apply_classifier_corrections(
        self,
        *,
        url: str,
        corrected_category: str,
        predicted_category: str,
        quality: str,
        article_id: str | None,
        _approved: bool = True,
        source_relevant: bool = True,
    ) -> None:
        from algorand_shared.article_statements import ArticlesStmts

        from app.core.cassandra import get_cassandra_session
        from app.core.statements import DomainTrackingStmts

        domain = self._domain_from_url(url)
        if domain:
            session = get_cassandra_session()
            row = session.execute(DomainTrackingStmts.GET_FOR_CORRECTION, (domain,)).one()
            metadata = dict(row.metadata or {}) if row is not None else {}
            metadata["quality"] = quality
            # Ground truth from the admin — future categorization of pages on
            # this domain prefers it over Mistral/keyword predictions.
            metadata["category_admin"] = corrected_category
            if corrected_category != predicted_category:
                metadata["category_corrected_from"] = predicted_category
            # Frontier training is now driven by the SOURCE verdict, not the
            # article verdict: rejecting a weak article (quality low) keeps a
            # good source alive. Only an explicit "source not relevant" (or
            # spam) marks the domain a dead end.
            domain_relevant = source_relevant and quality != "spam"
            session.execute(
                DomainTrackingStmts.INSERT,
                (
                    domain,
                    row.last_crawled_at if row is not None else datetime.now(tz=UTC),
                    row.last_online_at if row is not None else datetime.now(tz=UTC),
                    float(row.relevance_score or 0) if row is not None else 0.0,
                    corrected_category,
                    domain_relevant,
                    metadata,
                    "approved" if domain_relevant else "dead_end",
                ),
            )

        if not article_id or corrected_category == predicted_category:
            return
        try:
            aid = UUID(article_id)
        except ValueError:
            return
        session = get_cassandra_session()
        row = session.execute(ArticlesStmts.GET_FULL_BY_ID, (aid,)).one()
        if row is None:
            return
        tags = list(row.tags or [])
        updated = False
        for index, tag in enumerate(tags):
            if tag == predicted_category:
                tags[index] = corrected_category
                updated = True
        if predicted_category in CONTENT_CATEGORIES and corrected_category not in tags:
            tags.append(corrected_category)
            updated = True
        if not updated:
            return
        self._dual_write_article_tags(session, aid, tags, old_row=row)

    @staticmethod
    def _dual_write_article_tags(
        session: CassandraSession,
        aid: UUID,
        tags: list[str],
        *,
        old_row: Any,  # noqa: ANN401 -- duck-typed Cassandra driver row, no formal class
    ) -> None:
        """New `articles` table dual-write for a tags-only correction. Best-effort.

        ``old_row`` is the caller's already-fetched full row (carries the
        PRE-correction tags, needed to reconcile articles_by_tag) -- the
        partition-key columns used for the write itself are re-read fresh via
        GET_BY_ID rather than reused from ``old_row``, same as before this
        dual-write existed, in case they moved between the caller's read and
        this write.
        """
        with contextlib.suppress(Exception):
            from algorand_shared.article_statements import ArticlesStmts
            from algorand_shared.article_tag_index import sync_tag_index

            new_row = session.execute(ArticlesStmts.GET_BY_ID, (aid,)).one()
            if new_row is not None:
                session.execute(
                    ArticlesStmts.UPDATE_TAGS,
                    (tags, new_row.status, new_row.year, new_row.published_at, aid),
                )
                sync_tag_index(
                    aid,
                    old_status=new_row.status,
                    old_tags=list(old_row.tags or []),
                    old_published_at=new_row.published_at,
                    new_status=new_row.status,
                    new_tags=tags,
                    new_published_at=new_row.published_at,
                    service_id=old_row.service_id,
                    title=old_row.title,
                    summary=old_row.summary,
                    image_url=old_row.image_url,
                    source_url=old_row.source_url,
                    slug=old_row.slug,
                    translations=dict(old_row.translations) if old_row.translations else None,
                    first_published_at=old_row.first_published_at,
                    updated_at=old_row.updated_at,
                )

    def _complete_classifier_review(self, review_id: str, *, resolution: str) -> bool:
        from uuid import UUID

        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ClassifierReviewStmts

        try:
            rid = UUID(review_id)
        except ValueError:
            return False
        session = get_cassandra_session()
        now = datetime.now(tz=UTC)
        row = session.execute(ClassifierReviewStmts.GET_FULL, (rid,)).one()
        if row is None:
            return False
        created = row.created_at
        session.execute(
            ClassifierReviewStmts.INSERT_QUEUE,
            (
                rid,
                row.url,
                row.page_text,
                row.page_title,
                row.category,
                row.storage_score,
                resolution,
                created or now,
                dict(row.metadata or {}),
            ),
        )
        if created is not None:
            session.execute(
                ClassifierReviewStmts.DELETE_PENDING,
                ("pending", created, rid),
            )
        return True

    def _write_domain_relevance(
        self, domain: str, *, is_relevant: bool, single_page_only: bool = False
    ) -> tuple[dict, str]:
        """Write the domain_tracking row and return (metadata, pending_url) for the caller's own follow-up logic (e.g. admin_set_domain's frontier crawl enqueue on approval). Shared by admin_set_domain and reject_domain_source so there is one write, not two hand-copies of the same INSERT.

        single_page_only (only meaningful with is_relevant=True): this domain
        was approved for ONE specific page/article, not as a monitored
        ecosystem service — frontier_status becomes "reference" instead of
        "approved", so it's excluded from every domain-wide sweep (backfills,
        bulk re-crawls, the admin-bypass gates' implicit "crawl the whole
        site" treatment). Added after python.org/nytimes.com/climatetrade.com
        got approved for one citation each and were then crawled/indexed as
        if they were ecosystem services (2026-07-21).
        """
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import DomainTrackingStmts

        # Only normalize the APPROVE path. reject_domain_source intentionally
        # dead-ends whatever exact host string it's given (e.g. a bad
        # subdomain like spam.geographia.com.br) without collapsing to the
        # registrable domain — doing so would silently dead-end every OTHER
        # subdomain of that same registrable domain too, which is not what
        # "mark dead end" means. Only the approve side needs to match the
        # crawler's own domain_from_url(page_url) key so
        # is_admin_approved_domain finds it.
        if is_relevant:
            domain = self._normalize_domain_input(domain)
        session = get_cassandra_session()
        row = session.execute(DomainTrackingStmts.GET_FOR_CORRECTION, (domain,)).one()
        now = datetime.now(tz=UTC)
        meta = dict(row.metadata or {}) if row is not None else {}
        meta["frontier_set_by_admin"] = "true"
        if single_page_only and is_relevant:
            frontier_status = "reference"
            meta["admin_scope"] = "single_page"
        else:
            frontier_status = "approved" if is_relevant else "dead_end"
            meta.pop("admin_scope", None)
        meta["frontier_status"] = frontier_status
        pending_url = meta.pop("pending_url", "")
        session.execute(
            DomainTrackingStmts.INSERT,
            (
                domain,
                row.last_crawled_at if row is not None else now,
                row.last_online_at if row is not None else now,
                float(row.relevance_score or 0) if row is not None else 0.0,
                (row.category if row is not None else "") or "",
                is_relevant,
                meta,
                frontier_status,
            ),
        )
        return meta, pending_url

    def _record_domain_relevance_feedback(
        self, *, domain: str, meta: dict, pending_url: str, is_relevant: bool, wallet: str
    ) -> None:
        """Train the relevance classifier on this domain decision. Shared by admin_set_domain and reject_domain_source."""
        try:
            blob = " ".join(
                x
                for x in (
                    meta.get("preview_title", ""),
                    meta.get("preview_description", ""),
                    meta.get("preview_keywords", ""),
                    meta.get("link_text", ""),
                )
                if x
            ).strip()
            if blob:
                self.record_classifier_feedback(
                    url=pending_url or f"https://{domain}",
                    text_sample=blob[:2000],
                    category="news" if is_relevant else "generic",
                    predicted_category=None,
                    quality="high" if is_relevant else "spam",
                    predicted_publish=is_relevant,
                    approved=is_relevant,
                    admin_wallet=wallet,
                    source_relevant=is_relevant,
                    training_only=True,
                )
        except Exception:
            logger.warning(
                "failed to record classifier feedback for domain %s", domain, exc_info=True
            )

    def reject_domain_source(self, *, domain: str, wallet: str, source_url_hint: str = "") -> None:
        """Permanently mark a domain irrelevant so it's never re-scraped or re-composed — run_publish_pipeline's is_dead_end_domain check (via domain_tracker.is_dead_end_domain) reads this exact flag before any future scrape/compose spend. For use anywhere an admin judges a source irrecoverably bad (e.g. deleting a fabricated article), not just the Domains tab's own "Mark Dead End" button — a deleted article previously left this flag unset, so the same source could (and did, 2026-07-14: GEO World Energy / world.geographia.com.br) get re-crawled and re-composed from scratch after being deleted once."""
        meta, pending_url = self._write_domain_relevance(domain, is_relevant=False)
        self._record_domain_relevance_feedback(
            domain=domain,
            meta=meta,
            pending_url=pending_url or source_url_hint,
            is_relevant=False,
            wallet=wallet,
        )

    def dead_end_queue_row_domain(self, queue_id: str, *, wallet: str) -> dict | None:
        """Permanently reject the source domain behind one publish_queue row — the one-click "I never want to see this domain again" action for the Queue tab, reached straight from the row that surfaced it instead of hunting for the same domain through the paginated Domains tab (2026-08-04: Kryptonurd — the writer had already correctly aborted it as a dead project, but confirming that judgment as a permanent reject took several page-throughs to find). None when the row or a resolvable domain is missing."""
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import PublishQueueStmts

        try:
            qid = UUID(queue_id)
        except ValueError:
            return None
        session = get_cassandra_session()
        row = session.execute(PublishQueueStmts.GET_ROW, (qid,)).one()
        if row is None:
            return None
        domain = self._domain_from_url(row.scrape_url or "")
        if not domain:
            return None
        self.reject_domain_source(domain=domain, wallet=wallet, source_url_hint=row.scrape_url or "")
        return {"queue_id": queue_id, "domain": domain}

    def list_tool_suggestions(self, *, include_resolved: bool = False) -> list[dict]:
        """Capabilities the writer model wished it had (via suggest_tool), newest first. Resolved suggestions (tools that have since shipped) are hidden by default so the Tool gaps panel only shows genuine gaps instead of growing forever — see resolve_tool_suggestions."""
        from datetime import UTC, datetime

        from algorand_shared.feed_bucket import months_back

        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ToolInsightStmts

        session = get_cassandra_session()
        # "all" is the legacy pre-2026-08-24 partition (see tool_insights_store's
        # bucket-cutover comment) -- new rows land in real month buckets, but
        # everything written before the cutover still lives there, so it stays
        # in the scan permanently rather than silently dropping out of the list.
        buckets = ["all", *months_back(datetime.now(tz=UTC), 3)]
        rows = [
            r for bucket in buckets for r in session.execute(ToolInsightStmts.LIST_SUGGESTIONS, (bucket,))
        ]
        rows.sort(key=lambda r: r.created_at, reverse=True)
        return [
            {
                "suggestion_id": str(r.suggestion_id) if r.suggestion_id else "",
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "capability": r.capability or "",
                "reason": r.reason or "",
                "service_id": r.service_id or "",
                "source_url": r.source_url or "",
                "model": r.model or "",
                "resolved": bool(r.resolved),
            }
            for r in rows[:300]
            if include_resolved or not r.resolved
        ]

    def _publish_article_to_feed(self, article_id: str) -> bool:
        from uuid import UUID

        from algorand_shared.article_statements import ArticlesStmts

        from app.core.cassandra import get_cassandra_session

        try:
            aid = UUID(article_id)
        except ValueError:
            return False
        session = get_cassandra_session()
        row = session.execute(ArticlesStmts.GET_FULL_BY_ID, (aid,)).one()
        if row is None:
            return False
        # This is the article's FIRST time going public — a held/review
        # draft's published_at is stamped at compose time, not release time,
        # so it must be re-stamped now on the `articles` row itself.
        published_at = datetime.now(tz=UTC)
        # `articles` table dual-write: review-approval is a status
        # transition to 'published' with published_at re-stamped (same as
        # workers' backlog-release path). Best-effort.
        with contextlib.suppress(Exception):
            from algorand_shared.article_transitions import transition_article_status

            transition_article_status(
                aid, new_status="published", new_published_at=published_at
            )
            if row.slug:
                new_row = session.execute(ArticlesStmts.GET_BY_ID, (aid,)).one()
                if new_row is not None:
                    session.execute(
                        ArticlesStmts.SET_SLUG,
                        (row.slug, new_row.status, new_row.year, new_row.published_at, aid),
                    )
        # A service_id match key used to be registered here too, purely to
        # patch service_has_article()'s blindness to review-approved articles
        # (workers only registered match keys on their direct-publish path,
        # so the first-coverage reframing re-introduced already-covered
        # services — the BasketbAlgo duplicate). 2026-08-24: service_has_article
        # / find_latest_service_article now read articles.service_id directly
        # (SAI-indexed) instead of article_match_keys, and the
        # transition_article_status() call above already carries service_id
        # onto this row with status='published' — the gap this worked around
        # no longer exists, so the extra write was removed.
        # The article just went live on the public feed — index it so it's
        # findable in site search immediately, instead of waiting on the
        # once-daily reindex_articles safety net. Best-effort, never blocks
        # the publish.
        with contextlib.suppress(Exception):
            from app.core.typesense_client import upsert_article_document

            published = self.get_article(article_id)
            if published is not None:
                upsert_article_document(
                    article_id=article_id,
                    title=published.title,
                    summary=published.summary,
                    body=published.body,
                    service_id=published.service_id,
                    published_at_epoch=published.published_at_epoch,
                )
        # The article just became publicly visible — notify IndexNow, same as
        # the workers' direct-publish path does. Best-effort, never blocks.
        with contextlib.suppress(Exception):
            from app.modules.seo.indexnow import ping_article

            ping_article(article_id, slug=row.slug)
        return True

    @staticmethod
    def _enqueue_article_translations(article_id: str) -> None:
        """Fan out worker translate_article tasks now that the article is feed- visible. Translation happens at publish time only — held drafts are not translated (see workers publish_tasks.enqueue_article_translations, the other half of this seam). The task fetches current text by id and skips already-stored languages, so this is safe to fire more than once."""
        try:
            from celery import Celery

            from app.core.article_translation_langs import ARTICLE_TRANSLATION_LANGS
            from app.core.config import settings

            app = Celery(broker=settings.celery_broker_url)
            for lang in ARTICLE_TRANSLATION_LANGS:
                app.send_task(
                    "app.tasks.newspaper.translate_article",
                    args=[str(article_id), lang],
                    queue="pipeline",
                )
        except Exception:
            logger.warning("failed to enqueue translation tasks", exc_info=True)

    def _review_replaces_article_id(self, review_id: str) -> str:
        """The published article this review's draft would replace on approval (recompose_published flow), or "" for normal reviews. Fail-open to "" so a metadata read error degrades to the normal publish path."""
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ClassifierReviewStmts

        try:
            row = (
                get_cassandra_session()
                .execute(ClassifierReviewStmts.GET_METADATA, (UUID(review_id),))
                .one()
            )
            raw = dict(row.metadata or {}).get("raw") if row else None
            if raw:
                return str(serialization.loads(raw).get("replaces_article_id") or "")
        except Exception:
            logger.warning(
                "failed to read replaces_article_id for review %s", review_id, exc_info=True
            )
        return ""

    @staticmethod
    def _trigger_apply_recompose(draft_article_id: str, live_article_id: str) -> None:
        try:
            from celery import Celery

            from app.core.config import settings

            Celery(broker=settings.celery_broker_url).send_task(
                "app.tasks.newspaper.apply_recomposed_article",
                args=[draft_article_id, live_article_id],
                queue="pipeline",
            )
        except Exception:
            logger.warning("failed to trigger apply_recomposed_article", exc_info=True)

    @staticmethod
    def _trigger_distribution(article_id: str) -> None:
        """Auto-post to social channels (Bluesky, Telegram, ...) once an admin-approved fresh article actually lands in the feed. Recompose approvals deliberately do NOT trigger this (see apply_recomposed_article) — reposting every refresh of already- published content would look repetitive to followers."""
        try:
            from celery import Celery

            from app.core.config import settings

            Celery(broker=settings.celery_broker_url).send_task(
                "app.tasks.newspaper.distribute_article",
                args=[article_id],
                queue="pipeline",
            )
        except Exception:
            logger.warning("failed to trigger distribute_article", exc_info=True)

    @staticmethod
    def _trigger_compose_next() -> None:
        try:
            from celery import Celery

            from app.core.config import settings

            app = Celery(broker=settings.celery_broker_url)
            # Approving/rejecting frees the review slot — compose the next ONE
            # candidate. (Do NOT also fire check_and_publish_mistral_on_diff here:
            # it re-scrapes all monitored sources and is a heavy periodic task, not
            # something an admin click should trigger.)
            app.send_task("app.tasks.newspaper.drain_standard_publish_queue", queue="pipeline")
        except Exception:
            logger.warning("failed to trigger compose-next task", exc_info=True)

    def _feed_count_today(self, session: CassandraSession, _bucket: str = "") -> int:
        """2026-08-24: reads `articles` directly (was `articles_feed`'s COUNT_TODAY)."""
        from datetime import UTC, datetime, timedelta

        from algorand_shared.article_statements import ArticlesStmts

        day_start = datetime.now(tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        # Today is always within the current year partition.
        rows = session.execute(
            ArticlesStmts.COUNT_PUBLISHED_IN_RANGE,
            (day_start.year, day_start, day_end),
        )
        return sum(1 for _ in rows)

    # Shares the SAME Redis key and interval as the workers' primary
    # drain_standard_publish_queue pacing (publish_schedule.py /
    # NEWS_STANDARD_INTERVAL_HOURS) — an admin-approved article published
    # immediately here is still a standard-tier release and must respect the
    # same cadence as one released by the worker's queue drain. Previously
    # used its own news:last_feed_release_epoch key + a 1h default
    # (APPROVED_FEED_MIN_GAP_SECONDS), which let immediate/backlog releases
    # come out far more often than the intended NEWS_STANDARD_INTERVAL_HOURS
    # rhythm (root-caused 2026-07-14 via the AlgoVanity article).
    @staticmethod
    def _standard_publish_interval_seconds() -> int:
        import os

        try:
            return max(1, int(os.getenv("NEWS_STANDARD_INTERVAL_HOURS", "8"))) * 3600
        except ValueError:
            return 8 * 3600

    def _is_standard_publish_due(self) -> bool:
        import time

        from app.core.config import settings

        try:
            import redis

            client = redis.from_url(settings.redis_url, decode_responses=True)
            raw = client.get("news:last_standard_publish_epoch")
            if raw is None:
                return True
            return (int(time.time()) - int(raw)) >= self._standard_publish_interval_seconds()
        except Exception:
            # Fail CLOSED: a Redis error must never look like "clock elapsed,
            # go ahead and publish" — that would silently bypass the pacing
            # cadence entirely. Skipping this run and retrying later is the
            # safe direction; the workers side (publish_schedule.py) already
            # fails this way by letting the exception propagate and abort the
            # task, so this keeps both paths' behavior aligned.
            logger.warning("standard-publish due-check failed; treating as not due", exc_info=True)
            return False

    def _record_standard_publish(self) -> None:
        import time

        from app.core.config import settings

        try:
            import redis

            client = redis.from_url(settings.redis_url, decode_responses=True)
            client.set("news:last_standard_publish_epoch", str(int(time.time())))
        except Exception:
            logger.warning("failed to record standard publish timestamp", exc_info=True)

    @staticmethod
    def _record_url_rejected(url: str) -> None:
        """Mark a URL as recently-rejected so the worker enqueue path suppresses it (see domain_tracker.url_recently_rejected). Key format must match the worker's reject_cooldown_key. Best-effort."""
        if not url:
            return
        import hashlib

        from app.core.config import settings

        try:
            import redis

            client = redis.from_url(settings.redis_url, decode_responses=True)
            digest = hashlib.sha1(url.strip().lower().encode("utf-8")).hexdigest()
            client.set(
                f"algorand:reject:url:{digest}",
                "1",
                ex=int(settings.url_reject_cooldown_ttl),
            )
        except Exception:
            logger.warning("failed to record rejected-url cooldown for %s", url, exc_info=True)

    def _publish_or_queue_article(self, article_id: str) -> str:
        """Publish to the feed if under the daily cap; otherwise hold as status='backlog' for a worker to release at the configured pace."""
        from uuid import UUID

        from app.core.cassandra import get_cassandra_session
        from app.core.config import settings

        session = get_cassandra_session()
        bucket = getattr(settings, "news_feed_bucket", "main") or "main"
        cap = int(getattr(settings, "news_max_articles_per_day", 3) or 3)
        # Publish now only if under the daily cap AND the standard-publish
        # interval has elapsed since the last standard release — otherwise
        # queue, so the feed gets a steady drip not a dump.
        if self._feed_count_today(session, bucket) < cap and self._is_standard_publish_due():
            if self._publish_article_to_feed(article_id):
                self._enqueue_article_translations(article_id)
                self._trigger_distribution(article_id)
            self._record_standard_publish()
            return "published"
        try:
            aid = UUID(article_id)
        except ValueError:
            return "error"
        from datetime import UTC, datetime

        score = 0.0  # interest unknown here; FIFO within the day is fine
        approved_at = datetime.now(tz=UTC)
        from algorand_shared.article_transitions import transition_article_status

        # This is now the ONLY write recording the backlog decision (the old
        # pending_feed_queue dual-write is gone -- article-table
        # consolidation Phase 5) -- unlike a redundant secondary write, a
        # silent failure here would mean the article never gets released,
        # with nothing to show for it. Log loudly on failure rather than
        # swallowing it, but still don't crash the caller (this runs as a
        # post-write side effect after the review itself already resolved).
        try:
            transition_article_status(
                aid, new_status="backlog", interest_score=score, approved_at=approved_at
            )
        except Exception:
            logger.error(
                "failed to transition article %s to backlog -- it will never be released",
                aid,
                exc_info=True,
            )
            return "error"
        return "queued_daily_cap"

    def list_classifier_reviews(self, *, limit: int = 50, scan_limit: int = 500) -> list[dict]:
        """List recent classifier reviews (human-corrected verdicts), newest first."""
        from algorand_shared.article_statements import ArticlesStmts

        details = self._pending_review_details(scan_limit)
        if not details:
            return []

        # Parse metadata (pure Python) and collect the article ids to batch-fetch.
        parsed_rows = [self._parse_review_detail(detail) for detail in details]

        # Phase 2: batch-fetch the referenced articles concurrently (was a second
        # sequential SELECT per row). 2026-08-24: reads `articles` directly
        # (was `articles_by_id`'s GET_SUMMARY_CARD).
        uuid_args = []
        for _d, article_id, *_rest in parsed_rows:
            if article_id:
                with contextlib.suppress(ValueError):
                    uuid_args.append((UUID(article_id),))
        article_by_id = self._articles_by_id(ArticlesStmts.GET_FULL_BY_ID, uuid_args)

        items = [self._review_item_dict(row, article_by_id.get(row[1])) for row in parsed_rows]
        return _rank_reviews(items, limit=limit)

    def _pending_review_details(self, scan_limit: int) -> list:
        """Every pending classifier-review's queue detail row, fetched in one concurrent batch (was the dominant cost of this tab: a sequential SELECT per pending row)."""
        from app.core.cassandra import execute_parallel_with_args, get_cassandra_session
        from app.core.statements import ClassifierReviewStmts

        session = get_cassandra_session()
        try:
            # Scan the full pending set (bounded by the queue cap) so ranking is
            # global; the Cassandra clustering order (created_at ASC) is just the
            # scan order, not the display order — see _rank_reviews below.
            rows = session.execute(ClassifierReviewStmts.LIST_PENDING, ("pending", scan_limit))
        except Exception:
            return []
        review_ids = [row.review_id for row in rows]
        if not review_ids:
            return []
        details = []
        for ok, res in execute_parallel_with_args(
            ClassifierReviewStmts.GET_DETAIL,
            [(rid,) for rid in review_ids],
            concurrency=64,
            raise_on_error=False,
        ):
            if not ok:
                continue
            d = res.one()
            if d is not None:
                details.append(d)
        return details

    @staticmethod
    def _parse_review_raw_json(meta: dict) -> tuple:
        """Parse the review metadata's "raw" JSON blob into (parsed_dict, article_id, confidence, grade, grade_detail); article_id falls back to meta's own field on any parse failure or absence."""
        article_id = ""
        confidence: float | None = None
        grade: float | None = None
        grade_detail: dict | None = None
        parsed: dict = {}
        raw = meta.get("raw") if meta else None
        if not raw:
            return (
                parsed,
                str(meta.get("article_id", "") if meta else ""),
                confidence,
                grade,
                grade_detail,
            )
        try:
            parsed = serialization.loads(raw)
        except Exception:
            return parsed, str(meta.get("article_id", "")), confidence, grade, grade_detail
        article_id = str(parsed.get("article_id", ""))
        try:
            confidence = float(parsed["confidence"])
        except (KeyError, TypeError, ValueError):
            confidence = None
        try:
            grade = float(parsed["grade"])
        except (KeyError, TypeError, ValueError):
            grade = None
        gd = parsed.get("grade_detail")
        if gd:
            try:
                grade_detail = serialization.loads(gd) if isinstance(gd, str) else gd
            except Exception:
                grade_detail = None
        return parsed, article_id, confidence, grade, grade_detail

    @staticmethod
    def _review_categories(parsed: dict, meta: dict, detail: Any) -> list[str]:  # noqa: ANN401 -- duck-typed Cassandra driver row, no formal class
        """The review's normalized, valid content categories, falling back through raw JSON -> metadata -> the row's own category column."""
        cats_raw = parsed.get("categories") or meta.get("categories") or detail.category
        if isinstance(cats_raw, str) and cats_raw.strip():
            categories = [c.strip().lower() for c in cats_raw.split(",") if c.strip()]
        elif isinstance(cats_raw, list):
            categories = [str(c).strip().lower() for c in cats_raw if c]
        else:
            categories = []
        if not categories and detail.category:
            categories = [str(detail.category).strip().lower()]
        categories = [
            normalize_content_category(c, default="") for c in categories if is_content_category(c)
        ]
        if not categories and is_content_category(detail.category):
            categories = [normalize_content_category(detail.category)]
        return categories

    @classmethod
    def _parse_review_detail(cls, detail: Any) -> tuple:  # noqa: ANN401 -- duck-typed Cassandra driver row, no formal class
        """Parse one review's metadata blob into (detail, article_id, confidence, grade, grade_detail, categories, diverted_by, hold_reason)."""
        # Cassandra map columns come back as OrderedMapSerializedKey, which
        # is NOT a dict subclass — coerce so .get works.
        meta = dict(detail.metadata or {})
        parsed, article_id, confidence, grade, grade_detail = cls._parse_review_raw_json(meta)
        categories = cls._review_categories(parsed, meta, detail)
        # Why this draft was diverted, for the review card: which gate held it
        # and the specific reason (dead domain / unsourced specifics).
        diverted_by = str(parsed.get("diverted_by", "") or meta.get("diverted_by", "") or "")
        hold_reason = str(parsed.get("hold_reason", "") or meta.get("hold_reason", "") or "")
        return (
            detail,
            article_id,
            confidence,
            grade,
            grade_detail,
            categories,
            diverted_by,
            hold_reason,
        )

    @staticmethod
    def _articles_by_id(stmt: Any, uuid_args: list) -> dict[str, object]:  # noqa: ANN401 -- prepared-statement handle, no formal class
        """Batch-fetch articles by id concurrently (was a second sequential SELECT per row), keyed by string article_id."""
        from app.core.cassandra import execute_parallel_with_args

        article_by_id: dict[str, object] = {}
        if not uuid_args:
            return article_by_id
        for ok, res in execute_parallel_with_args(
            stmt, uuid_args, concurrency=64, raise_on_error=False
        ):
            if not ok:
                continue
            a = res.one()
            if a is not None:
                article_by_id[str(a.article_id)] = a
        return article_by_id

    @staticmethod
    def _review_item_dict(row: tuple, article: Any) -> dict:  # noqa: ANN401 -- duck-typed Cassandra driver row, no formal class
        """One parsed review row + its (possibly absent) article, as the admin review-card dict."""
        (
            detail,
            article_id,
            confidence,
            grade,
            grade_detail,
            categories,
            diverted_by,
            hold_reason,
        ) = row
        a = article
        return {
            "review_id": str(detail.review_id),
            "url": detail.url,
            "page_title": detail.page_title or "",
            "page_text_preview": (detail.page_text or "")[:500],
            "category": categories[0] if categories else "generic",
            "predicted_category": (detail.category or "").strip().lower() or None,
            "categories": categories,
            "storage_score": float(detail.storage_score or 0),
            "article_id": article_id,
            "confidence": confidence,
            "grade": grade,
            "grade_detail": grade_detail,
            "diverted_by": diverted_by,
            "hold_reason": hold_reason,
            "article_title": (a.title or "") if a else "",
            "article_summary": (a.summary or "") if a else "",
            "article_body": (a.body or "") if a else "",
            "service_id": (a.service_id or "") if a else "",
        }

    @staticmethod
    def _queue_row_dict(row: Any) -> dict:  # noqa: ANN401 -- duck-typed Cassandra driver row, no formal class
        return {
            "queue_id": str(row.queue_id),
            "status": row.status or "",
            "last_reason": getattr(row, "last_reason", None) or "",
            "priority": int(row.priority or 0),
            "topic": row.topic or "",
            "publish_kind": row.publish_kind or "",
            "service_id": row.service_id or "",
            "display_name": row.display_name or "",
            "scrape_url": row.scrape_url or "",
            "created_at": row.created_at.isoformat() if row.created_at else "",
            "updated_at": row.updated_at.isoformat() if row.updated_at else "",
            "human_pick_day": getattr(row, "human_pick_day", None) or "",
        }

    def list_publish_queue(self, *, limit: int = 200) -> list[dict]:
        """Publish-queue rows with their status and last drain/compose decision (last_reason). EVERY truly-pending row is always included (read from the same publish_queue_pending index the drain uses — a plain LIMIT scan of the main table returns token-order samples and silently under-reports pending); resolved history fills the remainder up to ``limit``, newest first. Payload deliberately excluded — it carries the full page text; use publish_queue_breakdown for one row's score."""
        from app.core.cassandra import execute_parallel_with_args, get_cassandra_session
        from app.core.statements import PublishQueueStmts

        session = get_cassandra_session()

        pending: list[dict] = []
        pending_ids: set[str] = set()
        try:
            id_rows = list(session.execute(PublishQueueStmts.LIST_PENDING_IDS, ("pending", 2000)))
            for ok, res in execute_parallel_with_args(
                PublishQueueStmts.GET_ROW,
                [(row.queue_id,) for row in id_rows],
                concurrency=64,
                raise_on_error=False,
            ):
                detail = res.one() if ok else None
                if detail is None:
                    continue
                item = self._queue_row_dict(detail)
                pending.append(item)
                pending_ids.add(item["queue_id"])
        except Exception:
            pending = []

        resolved: list[dict] = []
        try:
            scan_limit = max(limit * 5, 1000)
            for row in session.execute(PublishQueueStmts.LIST_RECENT, (scan_limit,)):
                item = self._queue_row_dict(row)
                if item["queue_id"] not in pending_ids and item["status"] != "pending":
                    resolved.append(item)
        except Exception:
            resolved = []

        pending.sort(key=lambda it: it["priority"], reverse=True)
        resolved.sort(key=lambda it: it["updated_at"], reverse=True)
        room = max(0, limit - len(pending))
        return pending + resolved[:room]

    def list_pending_feed_backlog(self) -> list[dict]:
        """Articles already approved and composed, waiting (articles.status='backlog') for the paced-release worker to publish them (PENDING_FEED_MAX_DEPTH caps this at 3) — distinct from publish_queue above, which is in-flight COMPOSING work. Not surfaced anywhere in admin before 2026-07-17; checking it required a direct DB read.

        2026-08-25: reads `articles` directly (was pending_feed_queue, article-
        table consolidation Phase 4) -- title/service_id come straight off the
        row instead of a per-item get_article() lookup, since LIST_BACKLOG
        already selects them from the same table.
        """
        from algorand_shared.article_transitions import list_backlog_articles

        items: list[dict] = [
            {
                "article_id": str(row.article_id),
                "title": row.title or "",
                "service_id": row.service_id or "",
                "interest_score": float(row.interest_score or 0),
                "approved_at": row.approved_at.isoformat() if row.approved_at else "",
            }
            for row in list_backlog_articles()
        ]
        items.sort(key=lambda it: it["approved_at"])
        return items

    def publish_queue_breakdown(self, queue_id: str) -> dict | None:
        """One row's priority_breakdown (computed at enqueue, stored on the payload) plus content signals — the "why this score" companion to list_publish_queue. None when the row is missing/unreadable."""
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import PublishQueueStmts

        try:
            qid = UUID(queue_id)
        except ValueError:
            return None
        session = get_cassandra_session()
        row = session.execute(PublishQueueStmts.GET_PAYLOAD, (qid,)).one()
        if row is None:
            return None
        try:
            payload = serialization.loads(row.payload or "{}")
        except Exception:
            return None
        return {
            "queue_id": queue_id,
            "priority_breakdown": payload.get("priority_breakdown") or "",
            "signals": payload.get("signals") or {},
            "tier": payload.get("tier") or "",
            "page_title": str(payload.get("page_title", "")),
            "diff_preview": str(payload.get("diff") or "")[:2000],
        }

    def bump_queue_priority(self, queue_id: str) -> dict | None:
        """Pin a pending queue row to the front: gives it a priority higher than every other pending row, so the drain's next legitimate run (still fully gated by the daily cap and pacing interval — this never touches either) composes it next instead of whatever would otherwise win on priority. None when the row is missing or not pending."""
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import PublishQueueStmts

        try:
            qid = UUID(queue_id)
        except ValueError:
            return None
        session = get_cassandra_session()
        row = session.execute(PublishQueueStmts.GET_ROW, (qid,)).one()
        if row is None or (row.status or "") != "pending":
            return None

        max_row = session.execute(PublishQueueStmts.MAX_PENDING_PRIORITY, ("pending",)).one()
        current_max = int(max_row.priority) if max_row is not None else 0
        new_priority = max(current_max, int(row.priority or 0)) + 1
        now = datetime.now(tz=UTC)

        session.execute(PublishQueueStmts.UPDATE_PRIORITY, (new_priority, now, qid))
        session.execute(
            PublishQueueStmts.DELETE_PENDING,
            ("pending", row.priority, row.created_at, qid),
        )
        session.execute(
            PublishQueueStmts.INSERT_PENDING,
            (
                "pending",
                new_priority,
                row.created_at,
                qid,
                row.service_id,
                row.topic,
                row.publish_kind,
            ),
        )
        return {"queue_id": queue_id, "priority": new_priority}

    def set_human_pick_for_today(self, queue_id: str) -> dict | None:
        """Pin a pending queue row as Lane 1 (human pick) of the day's 3 publish slots — distinct from bump_queue_priority, which only reorders within the automated ranking. drain_standard_publish_queue checks human_pick_day == today's UTC date before falling through to the automated lane selection, so this reserves a specific slot rather than just jumping the line. None when the row is missing or not pending."""
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import PublishQueueStmts

        try:
            qid = UUID(queue_id)
        except ValueError:
            return None
        session = get_cassandra_session()
        row = session.execute(PublishQueueStmts.GET_ROW, (qid,)).one()
        if row is None or (row.status or "") != "pending":
            return None

        today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        now = datetime.now(tz=UTC)
        session.execute(PublishQueueStmts.SET_HUMAN_PICK, (today, now, qid))
        return {"queue_id": queue_id, "human_pick_day": today}
