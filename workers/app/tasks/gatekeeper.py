"""Gatekeeper Celery tasks (run in a worker, triggered from the admin API)."""

from __future__ import annotations

from app.celery_app import celery_app


@celery_app.task(name="app.tasks.gatekeeper.run_annotator_validation")
def run_annotator_validation() -> dict:
    """Validate the LLM annotator against the human-tagged anchors and persist the
    report for the admin UI. Builds (human, machine) pairs from gatekeeper_anchors,
    runs the Tier-2 Mistral classifier, scores per-error-type precision/recall, and
    decides which types are trustworthy to auto-label."""
    import json
    from datetime import UTC, datetime

    from app.modules.gatekeeper.anchors import load_anchor_pairs
    from app.modules.gatekeeper.validation import validate_annotator

    try:
        from app.modules.gatekeeper.annotator import mistral_classifier

        classify = mistral_classifier()
    except Exception:
        classify = None  # fall back to Tier-1-only machine labels

    pairs = load_anchor_pairs(classify=classify)
    report = validate_annotator(pairs)
    summary = report.summary()

    try:
        from app.core.cassandra import get_cassandra_session

        get_cassandra_session().execute(
            "INSERT INTO gatekeeper_validation_report "
            "(bucket, computed_at, report_json, n_anchors, trusted_count) "
            "VALUES ('main', %s, %s, %s, %s)",
            (
                datetime.now(tz=UTC),
                json.dumps(summary, separators=(",", ":")),
                report.n_anchors,
                len(report.trusted_types),
            ),
        )
    except Exception:
        pass

    return {
        "n_anchors": report.n_anchors,
        "gated": report.gated,
        "trusted_types": sorted(report.trusted_types),
        "tier2": classify is not None,
    }
