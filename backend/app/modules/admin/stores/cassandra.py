from __future__ import annotations

import contextlib
import logging
import uuid
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import UUID

from app.core.feed_bucket import feed_month
from app.modules.admin.classifier_constants import CONTENT_CATEGORIES
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
    """Order pending reviews by classifier promise score (desc), capping how
    many items any one source domain may occupy so a flood of pages from a
    single site can't dominate the window. Over-cap items are intentionally
    held back (the window may be shorter than ``limit``) and resurface once the
    higher-ranked ones ahead of them are cleared."""
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
    def get_article(self, article_id: str) -> StoredArticle | None:
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
        current = self.get_article(article_id)
        if current is None:
            return None
        new_title = title if title is not None else current.title
        new_summary = summary if summary is not None else current.summary
        new_body = body if body is not None else current.body
        self._save_version_snapshot(current, editor=editor)
        self._write_article(current, new_title, new_summary, new_body, tag_extra="updated")
        updated = self.get_article(article_id)
        return updated

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

    def _write_article(
        self,
        current: StoredArticle,
        title: str,
        summary: str,
        body: str,
        *,
        tag_extra: str = "",
    ) -> None:
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ArticleStmts, FeedStmts

        aid = UUID(current.article_id)
        published_at = datetime.fromtimestamp(current.published_at_epoch, tz=UTC)
        tags = list(current.tags or [])
        if tag_extra and tag_extra not in tags:
            tags.append(tag_extra)
        session = get_cassandra_session()
        session.execute(
            ArticleStmts.UPDATE_CONTENT,
            (title, summary, body, tags, aid),
        )
        session.execute(
            FeedStmts.INSERT_FULL,
            (
                feed_month(published_at),
                published_at,
                aid,
                current.service_id,
                title,
                summary,
                tags,
                current.image_url,
                current.source_url,
            ),
        )

    def delete_article(self, article_id: str) -> bool:
        current = self.get_article(article_id)
        if current is None:
            return False

        from app.core.cassandra import get_cassandra_session
        from app.core.statements import (
            ArticleMatchStmts,
            ArticleStmts,
            ArticleVersionStmts,
            FeedStmts,
        )

        try:
            aid = UUID(article_id)
        except ValueError:
            return False

        session = get_cassandra_session()

        # Exact stored timestamp -> its month bucket -> precise feed-row delete.
        ts_row = session.execute(ArticleStmts.GET_PUBLISHED_AT, (aid,)).one()
        if ts_row is not None and ts_row.published_at is not None:
            session.execute(
                FeedStmts.DELETE,
                (feed_month(ts_row.published_at), ts_row.published_at, aid),
            )

        try:
            match_rows = session.execute(ArticleMatchStmts.LIST_BY_ARTICLE, (aid,))
            for row in match_rows:
                session.execute(
                    ArticleMatchStmts.DELETE_KEY,
                    (row.key_type, row.key_value, aid),
                )
                session.execute(
                    ArticleMatchStmts.DELETE_KEY_BY_ARTICLE,
                    (aid, row.key_type, row.key_value),
                )
        except Exception:
            logger.warning("failed to delete match rows for article %s", aid, exc_info=True)

        try:
            version_rows = session.execute(ArticleVersionStmts.LIST_VERSIONS, (aid,))
            for row in version_rows:
                session.execute(ArticleVersionStmts.DELETE, (aid, row.version))
        except Exception:
            logger.warning("failed to delete version rows for article %s", aid, exc_info=True)

        session.execute(ArticleStmts.DELETE, (aid,))
        return True

    def list_versions(self, article_id: str, *, limit: int = 20) -> list[dict]:
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
        out = []
        for row in rows:
            edited = row.edited_at
            out.append(
                {
                    "version": int(row.version),
                    "title": row.title,
                    "summary": row.summary,
                    "edit_reason": row.edit_reason,
                    "editor": row.editor,
                    "edited_at_epoch": int(edited.timestamp()) if edited else 0,
                }
            )
        return out

    def create_brief(
        self,
        *,
        title: str,
        body_markdown: str,
        keywords: str,
        status: str,
        wallet_address: str,
        refresh_every_days: int = 0,
    ) -> dict:
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
                }
            )
        return items

    def get_brief(self, brief_id: str) -> dict | None:
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
        }

    def list_official_channels(self, *, kind: str | None = None, limit: int = 200) -> list[dict]:
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import OfficialChannelStmts

        session = get_cassandra_session()
        try:
            if kind:
                rows = session.execute(
                    OfficialChannelStmts.LIST_BY_KIND, (kind, limit)
                )
            else:
                rows = session.execute(OfficialChannelStmts.LIST_ALL, (limit,))
        except Exception:
            return []
        items = []
        for row in rows:
            created = row.created_at
            items.append(
                {
                    "kind": row.kind,
                    "channel_id": row.channel_id,
                    "label": row.label or "",
                    "added_by": row.added_by or "",
                    "created_at_epoch": int(created.timestamp()) if created else 0,
                }
            )
        return items

    def upsert_official_channel(
        self,
        *,
        kind: str,
        channel_id: str,
        label: str,
        added_by: str,
    ) -> dict:
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import OfficialChannelStmts

        session = get_cassandra_session()
        now = datetime.now(tz=UTC)
        session.execute(
            OfficialChannelStmts.INSERT,
            (kind, channel_id, label, added_by, now),
        )
        return {
            "kind": kind,
            "channel_id": channel_id,
            "label": label,
            "added_by": added_by,
            "created_at_epoch": int(now.timestamp()),
        }

    def delete_official_channel(self, *, kind: str, channel_id: str) -> None:
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import OfficialChannelStmts

        session = get_cassandra_session()
        session.execute(OfficialChannelStmts.DELETE, (kind, channel_id))

    def _grade_meta_for_review(self, review_id: str) -> dict[str, str]:
        """Pull the article grade + subscores from a review item so they're
        stored alongside the accept/reject label (trainable features)."""
        import json

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
                parsed = json.loads(raw)
                if parsed.get("grade") is not None:
                    out["grade"] = str(parsed["grade"])
                gd = parsed.get("grade_detail")
                if gd is not None:
                    out["grade_detail"] = gd if isinstance(gd, str) else json.dumps(
                        gd, separators=(",", ":")
                    )
        except Exception:
            logger.debug("failed to parse review metadata for %s", review_id, exc_info=True)
        return out

    def training_stats(self) -> dict:
        """Labelled-data volume + balance + grader readiness for the Training tab.
        `graded_*` are rows that captured grade dimensions (only since the capture
        was added) — those are what the learned grader trains on."""
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ClassifierFeedbackStmts

        session = get_cassandra_session()
        rows = list(
            session.execute(ClassifierFeedbackStmts.LIST_BY_TIME, ("main",))
        )
        total = len(rows)
        approved = sum(1 for r in rows if r.approved)
        graded = graded_pos = graded_neg = 0
        # Fetch the grade-dimension detail rows in ONE concurrent batch instead of
        # up to 400 sequential round-trips (this was the Training tab's slow point).
        from app.core.cassandra import execute_parallel_with_args

        for ok, res in execute_parallel_with_args(
            ClassifierFeedbackStmts.GET_GRADE,
            [(r.feedback_id,) for r in rows[:400]],
            concurrency=64, raise_on_error=False,
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
        from app.core.cassandra import get_cassandra_session

        predicted = (predicted_category or category).strip().lower()
        corrected = category.strip().lower()
        cats = [c.strip().lower() for c in (categories or []) if c and c.strip()]
        if corrected and corrected not in cats:
            cats.insert(0, corrected)
        feedback_id = uuid.uuid4()
        now = datetime.now(tz=UTC)
        # Capture the article grade + its point-in-time dimensions so each
        # decision becomes a trainable row {dimensions -> approved}. Novelty /
        # recency can't be recomputed later, so we must snapshot them here.
        feedback_meta = self._grade_meta_for_review(review_id) if review_id else {}
        # Human-corrected dimensions become ground truth (the grader trains on
        # these in preference to the auto-scores).
        if corrected_scores:
            import json as _json

            feedback_meta["corrected_scores"] = _json.dumps(
                {k: float(v) for k, v in corrected_scores.items()}, separators=(",", ":")
            )
        # Snapshot the composed article text so the (text -> approved) pairs the
        # text-aware grader needs accumulate from here. Source `text_sample` is
        # the upstream page, not the article we actually grade.
        if article_id:
            try:
                art = self.get_article(article_id)
                if art is not None and getattr(art, "body", ""):
                    feedback_meta["article_text"] = (
                        f"{getattr(art, 'title', '')}\n{art.body}"
                    )[:8000]
            except Exception:
                logger.warning(
                    "failed to snapshot article text for feedback on %s",
                    article_id,
                    exc_info=True,
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
        from app.core.statements import ClassifierFeedbackStmts

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
            approved=approved,
            source_relevant=source_relevant,
        )
        if article_id and cats:
            self._apply_article_categories(article_id, cats)
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
            self._publish_or_queue_article(article_id)
        # A review slot just freed — generate the next-highest-interest
        # candidate now instead of waiting for the next scheduled drain.
        self._trigger_compose_next()
        return {
            "feedback_id": str(feedback_id),
            "approved": approved,
            "category": corrected,
            "predicted_category": predicted,
            "quality": quality,
        }

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
        later changes. Returns the anchor id."""
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
                now, anchor_id, article_id or "", url[:512],
                (source_text or "")[:8000], (article_text or "")[:8000],
                bool(factuality_fail), bool(tone_fail),
                [str(t) for t in (error_types or [])], admin_wallet,
            ),
        )
        return str(anchor_id)

    def list_gatekeeper_anchors(self, *, limit: int = 200) -> dict:
        """List anchors (newest-first, deduped to the latest tag per article).
        Returns {count, target, items}."""
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
        import json as _json

        from app.core.cassandra import get_cassandra_session
        from app.core.statements import GatekeeperStmts

        row = get_cassandra_session().execute(GatekeeperStmts.GET_REPORT).one()
        if row is None or not row.report_json:
            return None
        try:
            report = _json.loads(row.report_json)
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
    _MULTI_LABEL_SUFFIXES = frozenset({
        "co.uk", "org.uk", "gov.uk", "ac.uk", "me.uk", "ltd.uk", "plc.uk",
        "co.jp", "co.kr", "co.za", "co.nz", "co.in", "co.il", "co.id", "co.th",
        "com.au", "com.br", "com.mx", "com.tr", "com.cn", "com.sg", "com.hk",
        "com.tw", "com.ar", "com.co", "com.ua", "com.pl", "com.ng",
        "ac.in", "edu.in", "gov.in", "res.in", "nic.in", "org.in", "net.in",
    })

    # Platform / hosting suffixes where the SUBDOMAIN is the real identity
    # (foo.medium.com != bar.medium.com). Keep in sync with workers'
    # domain_tracker._PLATFORM_SUFFIXES; parity guarded by
    # test_domain_from_url_parity.py in both services.
    _PLATFORM_SUFFIXES = frozenset({
        "medium.com", "substack.com", "blogspot.com", "wordpress.com", "ghost.io",
        "github.io", "gitbook.io", "gitbook.com", "notion.site", "super.site",
        "netlify.app", "vercel.app", "pages.dev", "web.app", "firebaseapp.com",
        "herokuapp.com", "onrender.com", "readthedocs.io", "ipfs.io", "w3s.link",
        "fleek.co", "surge.sh", "webflow.io", "wixsite.com", "replit.app", "repl.co",
    })

    @staticmethod
    def _domain_from_url(url: str) -> str:
        """Registrable domain (eTLD+1) — collapses subdomains so accepting e.g.
        blog.perawallet.app keys the frontier on perawallet.app, matching the
        workers' domain_from_url. Platform/public suffixes keep the subdomain
        (foo.medium.com stays distinct) so unrelated sources don't merge."""
        parsed = urlparse(url.strip())
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

    def _apply_classifier_corrections(
        self,
        *,
        url: str,
        corrected_category: str,
        predicted_category: str,
        quality: str,
        article_id: str | None,
        approved: bool = True,
        source_relevant: bool = True,
    ) -> None:
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ArticleStmts, DomainTrackingStmts

        domain = self._domain_from_url(url)
        if domain:
            session = get_cassandra_session()
            row = session.execute(
                DomainTrackingStmts.GET_FOR_CORRECTION, (domain,)
            ).one()
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
        row = session.execute(ArticleStmts.GET_TAGS, (aid,)).one()
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
        session.execute(ArticleStmts.UPDATE_TAGS, (tags, aid))

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

    def _publish_article_to_feed(self, article_id: str) -> bool:
        from uuid import UUID

        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ArticleStmts, FeedStmts

        try:
            aid = UUID(article_id)
        except ValueError:
            return False
        session = get_cassandra_session()
        row = session.execute(ArticleStmts.GET_FEED_ROW, (aid,)).one()
        if row is None:
            return False
        published_at = row.published_at or datetime.now(tz=UTC)
        tags = list(row.tags or [])
        session.execute(
            FeedStmts.INSERT_FULL,
            (
                feed_month(published_at), published_at, aid, row.service_id,
                row.title, row.summary or "", tags, row.image_url, row.source_url,
            ),
        )
        return True

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

    def _feed_count_today(self, session, bucket: str = "") -> int:
        from datetime import UTC, datetime, timedelta

        from app.core.statements import FeedStmts

        day_start = datetime.now(tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        # Today is always within the current month partition.
        rows = session.execute(
            FeedStmts.COUNT_TODAY,
            (feed_month(day_start), day_start, day_end),
        )
        return sum(1 for _ in rows)

    @staticmethod
    def _feed_gap_seconds() -> int:
        import os

        try:
            return int(os.getenv("APPROVED_FEED_MIN_GAP_SECONDS", "3600"))
        except ValueError:
            return 3600

    def _feed_release_due(self) -> bool:
        import time

        from app.core.config import settings

        try:
            import redis

            client = redis.from_url(settings.redis_url, decode_responses=True)
            raw = client.get("news:last_feed_release_epoch")
            if raw is None:
                return True
            return (int(time.time()) - int(raw)) >= self._feed_gap_seconds()
        except Exception:
            return True

    def _record_feed_release(self) -> None:
        import time

        from app.core.config import settings

        try:
            import redis

            client = redis.from_url(settings.redis_url, decode_responses=True)
            client.set("news:last_feed_release_epoch", str(int(time.time())))
        except Exception:
            logger.warning("failed to record feed release timestamp", exc_info=True)

    @staticmethod
    def _record_url_rejected(url: str) -> None:
        """Mark a URL as recently-rejected so the worker enqueue path suppresses
        it (see domain_tracker.url_recently_rejected). Key format must match the
        worker's reject_cooldown_key. Best-effort."""
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
        """Publish to the feed if under the daily cap; otherwise hold in
        pending_feed_queue for a worker to release at 7/day."""
        from uuid import UUID

        from app.core.cassandra import get_cassandra_session
        from app.core.config import settings
        from app.core.statements import PendingFeedStmts

        session = get_cassandra_session()
        bucket = getattr(settings, "news_feed_bucket", "main") or "main"
        cap = int(getattr(settings, "news_max_articles_per_day", 7) or 7)
        # Publish now only if under the daily cap AND ≥1h since the last feed
        # release — otherwise queue, so the feed gets a steady drip not a dump.
        if self._feed_count_today(session, bucket) < cap and self._feed_release_due():
            self._publish_article_to_feed(article_id)
            self._record_feed_release()
            return "published"
        try:
            aid = UUID(article_id)
        except ValueError:
            return "error"
        from datetime import UTC, datetime

        score = 0.0  # interest unknown here; FIFO within the day is fine
        session.execute(
            PendingFeedStmts.INSERT,
            (bucket, score, datetime.now(tz=UTC), aid),
        )
        return "queued_daily_cap"

    def _apply_article_categories(self, article_id: str, categories: list[str]) -> None:
        """Store multiple categories/keywords on the article as tags."""
        from uuid import UUID

        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ArticleStmts

        try:
            aid = UUID(article_id)
        except ValueError:
            return
        session = get_cassandra_session()
        row = session.execute(ArticleStmts.GET_TAGS, (aid,)).one()
        if row is None:
            return
        tags = list(row.tags or [])
        for c in categories:
            if c and c not in tags:
                tags.append(c)
        session.execute(ArticleStmts.UPDATE_TAGS, (tags[:12], aid))

    def list_classifier_reviews(self, *, limit: int = 50, scan_limit: int = 500) -> list[dict]:
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ArticleStmts, ClassifierReviewStmts

        session = get_cassandra_session()
        try:
            # Scan the full pending set (bounded by the queue cap) so ranking is
            # global; the Cassandra clustering order (created_at ASC) is just the
            # scan order, not the display order — see _rank_reviews below.
            rows = session.execute(
                ClassifierReviewStmts.LIST_PENDING, ("pending", scan_limit)
            )
        except Exception:
            return []
        import json

        from app.core.cassandra import execute_parallel_with_args

        review_ids = [row.review_id for row in rows]
        if not review_ids:
            return []

        # Phase 1: fetch every queue detail in ONE concurrent batch instead of a
        # sequential SELECT per pending row (was the dominant cost of this tab).
        details = []
        for ok, res in execute_parallel_with_args(
            ClassifierReviewStmts.GET_DETAIL, [(rid,) for rid in review_ids],
            concurrency=64, raise_on_error=False,
        ):
            if not ok:
                continue
            d = res.one()
            if d is not None:
                details.append(d)

        # Parse metadata (pure Python) and collect the article ids to batch-fetch.
        parsed_rows: list[tuple] = []  # (detail, article_id, confidence, grade, grade_detail)
        for detail in details:
            article_id = ""
            confidence: float | None = None
            grade: float | None = None
            grade_detail: dict | None = None
            # Cassandra map columns come back as OrderedMapSerializedKey, which
            # is NOT a dict subclass — coerce so .get works.
            meta = dict(detail.metadata or {})
            if meta:
                raw = meta.get("raw")
                if raw:
                    try:
                        parsed = json.loads(raw)
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
                                grade_detail = json.loads(gd) if isinstance(gd, str) else gd
                            except (json.JSONDecodeError, TypeError):
                                grade_detail = None
                    except (json.JSONDecodeError, TypeError):
                        article_id = str(meta.get("article_id", ""))
                else:
                    article_id = str(meta.get("article_id", ""))
            parsed_rows.append((detail, article_id, confidence, grade, grade_detail))

        # Phase 2: batch-fetch the referenced articles concurrently (was a second
        # sequential SELECT per row).
        uuid_args = []
        for _d, article_id, *_rest in parsed_rows:
            if article_id:
                with contextlib.suppress(ValueError):
                    uuid_args.append((UUID(article_id),))
        article_by_id: dict[str, object] = {}
        if uuid_args:
            for ok, res in execute_parallel_with_args(
                ArticleStmts.GET_SUMMARY_CARD, uuid_args,
                concurrency=64, raise_on_error=False,
            ):
                if not ok:
                    continue
                a = res.one()
                if a is not None:
                    article_by_id[str(a.article_id)] = a

        items: list[dict] = []
        for detail, article_id, confidence, grade, grade_detail in parsed_rows:
            a = article_by_id.get(article_id)
            items.append(
                {
                    "review_id": str(detail.review_id),
                    "url": detail.url,
                    "page_title": detail.page_title or "",
                    "page_text_preview": (detail.page_text or "")[:500],
                    "category": detail.category or "",
                    "storage_score": float(detail.storage_score or 0),
                    "article_id": article_id,
                    "confidence": confidence,
                    "grade": grade,
                    "grade_detail": grade_detail,
                    "article_title": (a.title or "") if a else "",
                    "article_summary": (a.summary or "") if a else "",
                    "service_id": (a.service_id or "") if a else "",
                }
            )
        return _rank_reviews(items, limit=limit)
