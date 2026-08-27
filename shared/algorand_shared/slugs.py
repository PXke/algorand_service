"""Article URL slugs, derived from titles and de-duplicated with a numeric suffix.

Lives in shared/ because both sides need the same answer: the workers assign a
slug when an article is published, and the backend resolves and re-renders it.

A slug that has been served is a PERMANENT URL. Retitling an article must not
move it; if a slug ever has to change, the old one redirects to the new one and
is never dropped. Nothing here mutates an existing assignment — `unique_slug`
only ever proposes one for a slug that has no owner yet.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

logger = logging.getLogger(__name__)

# Long enough to stay descriptive, short enough that the URL survives being
# pasted into a chat client without wrapping. Cut on a word boundary.
MAX_SLUG_CHARS = 70

_SEPARATORS = re.compile(r"[\s_/\\]+")
_NON_SLUG = re.compile(r"[^a-z0-9-]+")
_DASH_RUN = re.compile(r"-{2,}")


def slugify(title: str) -> str:
    """Turn a headline into a URL slug, or "" when nothing usable survives.

    Unicode is folded to ASCII rather than percent-escaped: "Algorand's Über
    Rollup" becomes "algorands-uber-rollup", not a URL full of %C3%9C. Callers
    must handle the empty result — a title of only CJK or emoji legitimately
    reduces to nothing, and the article id is the fallback.
    """
    text = unicodedata.normalize("NFKD", title or "")
    # Drop combining marks, so "é" -> "e" rather than disappearing entirely.
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    # Curly and straight apostrophes vanish rather than becoming dashes, so
    # "Algorand's" reads "algorands" and not "algorand-s".
    text = text.replace("'", "").replace("’", "")
    text = _SEPARATORS.sub("-", text)
    text = _NON_SLUG.sub("-", text)
    text = _DASH_RUN.sub("-", text).strip("-")
    return _clamp(text)


def _clamp(slug: str) -> str:
    """Trim to MAX_SLUG_CHARS on a word boundary, never mid-word."""
    if len(slug) <= MAX_SLUG_CHARS:
        return slug
    cut = slug[:MAX_SLUG_CHARS]
    boundary = cut.rfind("-")
    # Only honour the boundary if it leaves a reasonable slug; otherwise a
    # title whose first word is 70 chars would collapse to almost nothing.
    if boundary > MAX_SLUG_CHARS // 2:
        cut = cut[:boundary]
    return cut.strip("-")


def unique_slug(title: str, *, fallback: str, is_taken: Callable[[str], bool]) -> str:
    """A slug for `title` that no other article owns.

    `is_taken` is the lookup against articles_by_slug. The base slug is tried
    first, then `-2`, `-3`, ... — one-based counting from the SECOND claimant,
    so the first article to use a title keeps the bare slug forever and later
    ones queue behind it. `fallback` (the article id) is used when the title
    yields no usable characters at all.

    Note the suffix is applied to the CLAMPED base, so a very long duplicated
    title cannot push the result past MAX_SLUG_CHARS plus the suffix.
    """
    base = slugify(title) or slugify(fallback) or "article"
    if not is_taken(base):
        return base
    for n in range(2, 1000):
        candidate = f"{base}-{n}"
        if not is_taken(candidate):
            return candidate
    # 998 articles sharing one title is not a collision, it is a bug upstream.
    raise ValueError(f"could not find a free slug for {base!r} after 998 attempts")


def ensure_article_slug(article_id: str | UUID, title: str) -> str | None:
    """Claim a permanent URL slug for an article, or return the one it already has.

    Shared 2026-08-27 (was workers-only, `article_store.py`): the backend's
    review-approval publish path (`_publish_article_to_feed`) transitions a
    held/review draft straight to `status='published'` without ever going
    through workers' own direct-publish path -- and a review draft never had
    a slug claimed for it in the first place (slugs are claimed at PUBLISH
    time, not draft-creation time). `_publish_article_to_feed` used to only
    ever COPY an existing slug forward across the publish-time re-stamp
    (`if row.slug: ...`), never CLAIM one when there wasn't one yet -- so
    every review-approved article silently published with `slug=NULL`,
    falling back to a bare-UUID URL that search engines never index cleanly.
    Root-caused live 2026-08-27 (Al Goanna recompose). Both services now call
    this exact function so the claim logic can never diverge between them
    again.

    Idempotent and safe under concurrency: the claim is a lightweight
    transaction (IF NOT EXISTS), so two callers racing on the same title
    cannot both take one slug — the loser tries the next suffix.
    """
    from algorand_shared.article_statements import ArticlesStmts

    from app.core.cassandra import get_cassandra_session

    aid = article_id if isinstance(article_id, UUID) else UUID(str(article_id))
    session = get_cassandra_session()

    existing = session.execute(ArticlesStmts.GET_FULL_BY_ID, (aid,)).one()
    if existing and existing.slug:
        return existing.slug

    base = slugify(title) or str(aid)
    for _attempt in range(50):
        candidate = unique_slug(
            title,
            fallback=str(aid),
            is_taken=lambda s: session.execute(ArticlesStmts.SLUG_TAKEN, (s,)).one() is not None,
        )
        applied = session.execute(
            ArticlesStmts.CLAIM_SLUG, (candidate, aid, datetime.now(tz=UTC))
        ).one()
        # LWT returns [applied] — False means another caller took it first.
        # The reverse-index claim above is the durable part; writing the slug
        # back onto the owning `articles` row is the caller's job.
        if applied is None or getattr(applied, "applied", True):
            return candidate
    logger.warning("could not claim a slug for %s (base=%s)", aid, base)
    return None
