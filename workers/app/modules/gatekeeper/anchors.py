"""Load human-tagged validation anchors and pair them with machine annotations.

The admin grade screen stores anchor tags in ``classifier_feedback`` metadata
under the ``anchor_tags`` key (set when the reviewer flips "Add to validation
anchor set"). This reads those rows, rebuilds the human ``AnnotatedSample`` from
the tags, runs the machine annotator on the same draft, and returns
``(human, machine)`` pairs ready for ``validation.validate_annotator``.

Parsing is factored into pure helpers (unit-tested); the Cassandra scan is a thin
failure-tolerant wrapper.
"""

from __future__ import annotations

from app.modules.gatekeeper.annotator import ClassifyFn, annotate
from app.modules.gatekeeper.profile import AnnotatedSample
from app.modules.gatekeeper.validation import Pair


def human_sample_from_tags(tags: dict) -> AnnotatedSample:
    """Rebuild the human ground-truth ``AnnotatedSample`` from stored anchor tags.
    Severities are unknown from a human y/n tag, so they're left empty (the
    profile defaults them); the harness only compares error-type membership."""
    return AnnotatedSample(
        factuality_fail=bool(tags.get("factuality_fail", False)),
        tone_fail=bool(tags.get("tone_fail", False)),
        error_types=tuple(str(t) for t in (tags.get("error_types") or [])),
        severities={},
        source="anchor",
    )


def build_pair(
    *,
    source_text: str,
    trace_text: str,
    article_text: str,
    tags: dict,
    classify: ClassifyFn | None = None,
    fact_min: float = 0.80,
) -> Pair:
    """One ``(human, machine)`` pair for a single anchor."""
    human = human_sample_from_tags(tags)
    machine = annotate(
        source_text, trace_text, article_text,
        classify=classify, fact_min=fact_min, sample_source="anchor",
    )
    return (human, machine)


def load_anchor_pairs(*, classify: ClassifyFn | None = None, limit: int = 200) -> list[Pair]:
    """Read the ``gatekeeper_anchors`` table and build (human, machine) pairs.

    Each anchor stores its own immutable source_text + article_text snapshot; the
    tool trace is pulled from ``investigation_findings`` by url (falling back to
    article_id). Deduped to the latest tag per article. Best-effort: any row that
    can't be rebuilt is skipped, total failure returns []. Pass ``classify`` (e.g.
    ``annotator.mistral_classifier()``) to exercise Tier 2; omit for Tier-1-only."""
    try:
        from app.core.cassandra import get_cassandra_session
        from app.modules.newspaper.investigation_store import load_investigation_trace

        rows = get_cassandra_session().execute(
            "SELECT anchor_id, article_id, url, source_text, article_text, "
            "factuality_fail, tone_fail, error_types FROM gatekeeper_anchors "
            "WHERE bucket = 'main' LIMIT %s",
            (limit,),
        )
        pairs: list[Pair] = []
        seen: set[str] = set()
        for row in rows:
            key = row.article_id or str(row.anchor_id)
            if key in seen:
                continue
            seen.add(key)
            article_text = row.article_text or ""
            if not article_text:
                continue
            try:
                trace = load_investigation_trace(row.url or row.article_id or "")
                tags = {
                    "factuality_fail": bool(row.factuality_fail),
                    "tone_fail": bool(row.tone_fail),
                    "error_types": list(row.error_types or []),
                }
                pairs.append(
                    build_pair(
                        source_text=row.source_text or "",
                        trace_text=trace,
                        article_text=article_text,
                        tags=tags,
                        classify=classify,
                    )
                )
            except Exception:
                continue
        return pairs
    except Exception:
        return []
