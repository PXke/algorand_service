"""Article-level grader: scores a composed draft on the factors that actually
matter here — novelty, relevance, recency, length, factual specificity,
structure, and a popularity prior from real view counts. Distinct from the
SOURCE publish classifier (which only judges input relevance).

Surfaced to the human reviewer on the classifier page, and (next) to the writer
as a `review_draft` tool so it can self-assess and improve once before finishing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

_STOP = frozenset(
    [
        "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are",
        "was", "were", "be", "been", "with", "from", "at", "by", "as", "this",
        "that", "these", "those", "it", "its", "algorand", "algo",
    ]
)


def _tokens(text: str) -> set[str]:
    return {
        w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in _STOP and len(w) > 2
    }


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _age_decay_weight(published_at_epoch: float, *, now_ts: float | None = None) -> float:
    """How much a recent article's similarity should still count against novelty,
    by its age: full weight (1.0) within NOVELTY_DECAY_FULL_DAYS, easing linearly
    to 0 by NOVELTY_DECAY_ZERO_DAYS. Lets us re-cover a story freely once the
    closest prior article is old enough (~10 weeks by default)."""
    from app.core import config

    full_days = max(0, config.NOVELTY_DECAY_FULL_DAYS)
    zero_days = max(full_days + 1, config.NOVELTY_DECAY_ZERO_DAYS)
    if now_ts is None:
        now_ts = datetime.now(tz=UTC).timestamp()
    try:
        epoch = float(published_at_epoch)
    except (TypeError, ValueError):
        return 1.0  # unknown age → treat as recent (conservative: full penalty)
    if epoch <= 0:
        return 1.0  # missing publish time → don't let absent data fake novelty
    age_days = max(0.0, (now_ts - epoch) / 86400.0)
    if age_days <= full_days:
        return 1.0
    if age_days >= zero_days:
        return 0.0
    return (zero_days - age_days) / (zero_days - full_days)


def _length_score(words: int) -> float:
    """Lax band: 1.0 anywhere in [LENGTH_OK_MIN, LENGTH_OK_MAX]; ramps down only
    outside it. Length is intentionally NOT a target — research depth drives the
    grade, so we don't reward hitting a word count (which causes padding)."""
    from app.core.config import LENGTH_OK_MAX_WORDS, LENGTH_OK_MIN_WORDS

    if words < LENGTH_OK_MIN_WORDS:
        return round(max(0.0, words / max(1, LENGTH_OK_MIN_WORDS)), 3)
    if words > LENGTH_OK_MAX_WORDS:
        return round(max(0.0, 1.0 - (words - LENGTH_OK_MAX_WORDS) / max(1, LENGTH_OK_MAX_WORDS)), 3)
    return 1.0


def _structure_score(body: str) -> float:
    text = body or ""
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    has_heading = bool(re.search(r"(^|\n)\s*(#{1,4}\s+\S|\*\*[^\n*]+\*\*\s*$)", text, re.MULTILINE))
    has_table = bool(re.search(r"(^|\n)\s*\|.*\|.*\|", text))  # a 3+ cell markdown row
    # Structure means *scannable* structure, not length. A wall of raw text —
    # paragraphs but no heading or table — tops out low no matter how many blank
    # lines it has; real structure needs a heading and/or a data table.
    score = 0.0
    if len(paragraphs) >= 2:
        score += 0.25
    if len(paragraphs) >= 4:
        score += 0.10
    if has_heading:
        score += 0.35
    if has_table:
        score += 0.30
    return min(1.0, score)


@dataclass
class _Recent:
    title: str = ""
    title_tokens: set[str] = field(default_factory=set)
    views: int = 0
    tags: tuple[str, ...] = ()
    published_at_epoch: float = 0.0


_RECENT_CACHE: dict[str, object] = {"at": 0.0, "rows": []}
_RECENT_TTL_SECONDS = 60


def _recent_articles(limit: int = 60) -> list[_Recent]:
    import time

    # Cached ~60s: novelty is now checked on every ingest (priority) and at every
    # compose, so re-scanning the feed each call would be wasteful.
    cache_age = time.monotonic() - float(_RECENT_CACHE["at"])
    if cache_age < _RECENT_TTL_SECONDS and _RECENT_CACHE["rows"]:
        return _RECENT_CACHE["rows"]  # type: ignore[return-value]

    from app.modules.newspaper.view_counts import get_views_bulk

    now = datetime.now(tz=UTC)
    # Cover the full age-decay horizon (~10 weeks) so older near-duplicates are
    # SEEN and tapered by age, rather than hard-cut by a too-short month window.
    buckets = set()
    cursor = now.replace(day=1)
    for _ in range(4):  # current + 3 prior months spans 70+ days in all cases
        buckets.add(cursor.strftime("%Y-%m"))
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    from app.core.cassandra import execute_parallel_with_args
    from app.core.statements import FeedStmts

    rows: list = []
    # Fan the per-month bucket reads out concurrently rather than serially.
    for ok, page in execute_parallel_with_args(
        FeedStmts.BY_BUCKET_RECENT, [(bucket,) for bucket in buckets]
    ):
        if ok:
            rows.extend(page)
    rows = rows[:limit]
    views = get_views_bulk([str(r.article_id) for r in rows]) if rows else {}

    def _epoch(pa: datetime | None) -> float:
        if pa is None:
            return 0.0
        return (pa.replace(tzinfo=UTC) if pa.tzinfo is None else pa).timestamp()

    out = [
        _Recent(
            title=r.title or "",
            title_tokens=_tokens(r.title or ""),
            views=int(views.get(str(r.article_id), 0)),
            tags=tuple(r.tags or ()),
            published_at_epoch=_epoch(getattr(r, "published_at", None)),
        )
        for r in rows
    ]
    _RECENT_CACHE["rows"] = out
    _RECENT_CACHE["at"] = time.monotonic()
    return out


def _closest_similarity(cand_tokens: set[str], recent: list[_Recent]) -> tuple[float, str]:
    """Highest title-token Jaccard between the candidate and any recent article,
    plus that article's title. Compares HEADLINES only — category tags
    (governance/defi/…) are shared across many articles and would otherwise
    inflate similarity and crush novelty for genuinely distinct stories."""
    best_sim, best_title = 0.0, ""
    if not cand_tokens:
        return best_sim, best_title
    now_ts = datetime.now(tz=UTC).timestamp()
    for a in recent:
        sim = _jaccard(cand_tokens, a.title_tokens)
        if sim <= 0:
            continue
        # Age-decay so a near-identical headline from months ago no longer blocks
        # re-covering the story (see _age_decay_weight).
        sim *= _age_decay_weight(a.published_at_epoch, now_ts=now_ts)
        if sim > best_sim:
            best_sim, best_title = sim, a.title
    return best_sim, best_title


def recent_title_similarity(title: str) -> tuple[float, str]:
    """(closest_similarity, that_article_title) of a candidate headline vs
    recently published articles. Used pre-composition to skip near-duplicates;
    same tokenizer/metric as the grader's novelty so they agree."""
    try:
        recent = _recent_articles()
    except Exception:
        return 0.0, ""
    return _closest_similarity(_tokens(title), recent)


def recent_content_similarity(title: str, text: str = "") -> tuple[float, str]:
    """(closest_similarity, that_article_title) of a candidate vs recently
    published articles, retrieved by CONTENT (title+summary+body) from the
    Typesense articles index rather than by headline tokens. This catches the
    same-topic / different-headline dupes that recent_title_similarity misses
    (e.g. "Pera adds staking" vs "Explore Pera's new feature").

    The retrieval is semantic-ish (full-text over the body); the raw score is
    token Jaccard of the candidate's title+summary against the matched article's
    title+summary. That raw similarity is then AGE-WEIGHTED by how long ago the
    matched article was published: full weight within NOVELTY_DECAY_FULL_DAYS,
    easing linearly to 0 by NOVELTY_DECAY_ZERO_DAYS — so re-covering a story is
    penalized hard for a week and allowed again after ~10 weeks. The returned
    score is on the same 0-1 scale as the headline metric (combinable with max()).
    Fails open (0.0) when Typesense is unavailable or the window is disabled."""
    from app.core import config

    window_hours = config.NOVELTY_CONTENT_WINDOW_HOURS
    if window_hours <= 0:
        return 0.0, ""

    cand = _tokens(f"{title}\n{text}")
    if not cand:
        return 0.0, ""

    try:
        from app.modules.search.core.indexer import ARTICLES_COLLECTION
        from app.modules.search.core.typesense_config import build_typesense_client

        client = build_typesense_client()
        if client is None:
            return 0.0, ""

        cutoff = int((datetime.now(tz=UTC) - timedelta(hours=window_hours)).timestamp())
        result = client.collections[ARTICLES_COLLECTION].documents.search(
            {
                # The headline carries the topic; the body index does the matching.
                "q": title or text[:200],
                "query_by": "title,summary,body",
                "filter_by": f"published_at:>{cutoff}",
                "per_page": 5,
            }
        )
    except Exception:
        return 0.0, ""

    now_ts = datetime.now(tz=UTC).timestamp()

    best_sim, best_title = 0.0, ""
    for hit in result.get("hits", []):
        doc = hit.get("document", {})
        other = _tokens(f"{doc.get('title', '')}\n{doc.get('summary', '')}")
        sim = _jaccard(cand, other)
        if sim <= 0:
            continue
        decayed = sim * _age_decay_weight(doc.get("published_at", 0), now_ts=now_ts)
        if decayed > best_sim:
            best_sim, best_title = decayed, doc.get("title", "")
    return best_sim, best_title


# The colon-label headline shape ("<Name>: <description of name>") — the house
# style bans it (headlines must state a concrete development, 2026-07-12), and
# the prompt alone can't be trusted to hold a small/large model to it forever.
# Requires ": " (colon+space) within the first ~50 chars so times ("3:1") and
# late colons in long titles don't trip it.
_COLON_LABEL_RE = re.compile(r"^[^:]{2,48}:\s")

# Vague marketing verbs the style guide bans in headlines; word-boundary match,
# any inflection covered by listing the common forms.
_MARKETING_VERBS_RE = re.compile(
    r"\b(unveil(?:s|ed|ing)?|empower(?:s|ed|ing)?|elevat(?:es?|ed|ing)|"
    r"revolutioniz(?:es?|ed|ing)|superchargh?(?:es?|ed|ing)|unleash(?:es|ed|ing)?)\b",
    re.IGNORECASE,
)

_HEADLINE_MAX_CHARS = 90

# A dollar figure in the headline, e.g. "$2.4K", "$4,000", "$135". The
# NUMERIC HONESTY prompt rule (mistral_compose.py) already tells the model a
# sub-$10K TVL/figure is "negligible, not a headline metric" — but that's a
# soft prompt rule, and prompt rules drift under revision pressure exactly
# like the colon-label headline did before it got a deterministic check here
# (2026-07-12). Confirmed reproducing 2026-07-14: a CompX recompose kept
# "$2.4K TVL" in the headline across every revision pass despite the prompt
# rule, because nothing deterministic ever flagged it.
_DOLLAR_FIGURE_RE = re.compile(r"\$([\d,]+(?:\.\d+)?)\s*([kKmMbB])?\b")
_NEGLIGIBLE_HEADLINE_DOLLAR_THRESHOLD = 10_000
_DOLLAR_MULTIPLIERS = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}


def headline_violations(title: str) -> list[str]:
    """Deterministic house-style checks for a headline. Returns actionable
    issue strings (prefixed "headline — ") that the compose revision pass can
    feed straight back to the writer; empty list when the title conforms."""
    text = (title or "").strip()
    if not text:
        return []
    issues: list[str] = []
    if _COLON_LABEL_RE.match(text):
        issues.append(
            "headline — colon-label title (“<Name>: <description>”). Rewrite as an "
            "active-voice claim stating what actually happened in THIS story, "
            "ideally with a verified number or stake"
        )
    if len(text) > _HEADLINE_MAX_CHARS:
        issues.append(
            f"headline — {len(text)} chars; tighten to under {_HEADLINE_MAX_CHARS} "
            "by keeping only the single most newsworthy claim"
        )
    match = _MARKETING_VERBS_RE.search(text)
    if match:
        issues.append(
            f"headline — vague marketing verb “{match.group(0)}”; replace with the "
            "concrete verb for what happened (shipped, launched, hit, cut, raised…)"
        )
    for dm in _DOLLAR_FIGURE_RE.finditer(text):
        raw, suffix = dm.group(1), (dm.group(2) or "").lower()
        try:
            value = float(raw.replace(",", "")) * _DOLLAR_MULTIPLIERS.get(suffix, 1)
        except ValueError:
            continue
        if value < _NEGLIGIBLE_HEADLINE_DOLLAR_THRESHOLD:
            issues.append(
                f"headline — “${raw}{dm.group(2) or ''}” is a negligible figure, not a "
                "headline metric; drop it and lead with what actually happened instead "
                "(the number can still appear in the body)"
            )
            break
    return issues


def grade_article_schema(
    *,
    title: str,
    summary: str,
    body: str,
) -> dict:
    """Deterministic schema/structure checks only — not qualitative journalism."""
    from app.core.config import LENGTH_OK_MAX_WORDS, LENGTH_OK_MIN_WORDS
    from app.modules.gatekeeper.structure import evaluate_structure

    words = len((body or "").split())
    length_score = _length_score(words)
    struct = evaluate_structure(body)
    structure_score = (sum(h.passed for h in struct) / len(struct)) if struct else 1.0
    has_heading = bool(re.search(r"(^|\n)\s*#{1,4}\s+\S", body or "", re.MULTILINE))
    has_table = bool(re.search(r"(^|\n)\s*\|.*\|.*\|", body or ""))

    issues: list[str] = []
    issues.extend(headline_violations(title))
    if len(title or "") > 120:
        issues.append("schema — title exceeds 120 characters")
    if len(summary or "") > 280:
        issues.append("schema — summary exceeds 280 characters")
    if words < LENGTH_OK_MIN_WORDS:
        issues.append(f"short ({words} words) — fine only if the source is genuinely thin")
    if words > LENGTH_OK_MAX_WORDS:
        issues.append(f"too long ({words} words) — over {LENGTH_OK_MAX_WORDS}; cut padding/filler")
    if not has_heading:
        issues.append("schema — body needs at least one ## section header")
    issues.extend(f"structure — {h.name}: {h.observed}" for h in struct if not h.passed)

    # Multi-item comma dumps without a table (light heuristic).
    if not has_table and re.search(
        r"(?:,\s+\w+){4,}", " ".join((body or "").splitlines()[:40])
    ):
        issues.append(
            "schema — multi-item data buried in comma-separated prose; use a "
            "Markdown table with Concept and Real-World Implication columns"
        )

    grade = round(10.0 * (structure_score * 0.55 + length_score * 0.45), 1)
    return {
        "grade": grade,
        "model": "schema_heuristic",
        "subscores": {
            "structure": round(structure_score, 2),
            "length": round(length_score, 2),
        },
        "issues": issues,
        "word_count": words,
        "has_table": has_table,
        "has_heading": has_heading,
    }


def grade_article_draft(
    *,
    title: str,
    body: str,
    source_url: str = "",
    published_at: str = "",
    tags: tuple[str, ...] = (),
    summary: str = "",
) -> dict:
    """Grade a draft: schema heuristic (grade/issues) plus informational signals.

    The headline ``grade`` is schema/structure only. Qualitative journalism
    (narrative synthesis, Algorand technical depth) is scored separately via
    ``grade_article_quality_llm`` during compose revision."""
    schema = grade_article_schema(title=title, summary=summary, body=body)

    # Informational signals for telemetry / human review — NOT fused into grade.
    from app.core.config import PAGE_STALE_MAX_AGE_DAYS
    from app.modules.gatekeeper.fact_align import content_recency_score

    try:
        from app.modules.search.classifier.score import score_page

        relevance_score = max(0.0, min(1.0, score_page(url=source_url, text=body).score))
    except Exception:
        relevance_score = 0.5

    recency_score = content_recency_score(
        f"{title}\n{body}", stale_days=max(1, PAGE_STALE_MAX_AGE_DAYS)
    )
    if recency_score is None:
        recency_score = 0.75

    try:
        recent = _recent_articles()
    except Exception:
        recent = []
    closest_sim, closest_title = _closest_similarity(_tokens(title), recent)
    novelty_score = max(0.0, 1.0 - closest_sim)

    issues = list(schema["issues"])
    signals: list[str] = []
    if novelty_score < 0.5:
        signals.append("low novelty — very close to a recent article; add a fresh angle or skip")
    if recency_score < 0.4:
        signals.append(
            "stale — source/event is old with no current-month hook; frame as a "
            "status update (not breaking news)"
        )
    if relevance_score < 0.4:
        signals.append("weak Algorand relevance — tie it more directly to Algorand")

    return {
        "grade": schema["grade"],
        "model": schema["model"],
        "subscores": {
            **schema["subscores"],
            "novelty": round(novelty_score, 2),
            "relevance": round(relevance_score, 2),
            "recency": round(recency_score, 2),
        },
        "issues": issues,
        "signals": signals,
        "word_count": schema["word_count"],
        "closest_similarity": round(closest_sim, 2),
        "closest_title": closest_title,
    }
