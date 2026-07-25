"""Gatekeeper Celery tasks (run in a worker, triggered from the admin API)."""

from __future__ import annotations

import logging

from app.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.gatekeeper.run_annotator_validation")
def run_annotator_validation() -> dict:
    """Validate the LLM annotator against the human-tagged anchors and persist the report for the admin UI. Builds (human, machine) pairs from gatekeeper_anchors, runs the Tier-2 Mistral classifier, scores per-error-type precision/recall, and decides which types are trustworthy to auto-label."""
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
        from app.core.statements import GatekeeperStmts

        get_cassandra_session().execute(
            GatekeeperStmts.INSERT_REPORT,
            (
                datetime.now(tz=UTC),
                json.dumps(summary, separators=(",", ":")),
                report.n_anchors,
                len(report.trusted_types),
            ),
        )
    except Exception:
        logger.warning("failed to persist annotator validation report", exc_info=True)

    return {
        "n_anchors": report.n_anchors,
        "gated": report.gated,
        "trusted_types": sorted(report.trusted_types),
        "tier2": classify is not None,
    }


@celery_app.task(name="app.tasks.gatekeeper.train_quality_head")
def train_quality_head_task() -> dict:
    """Train the gatekeeper's quality head from admin classifier_feedback labels (factuality/tone are untouched — that needs the gold-run/corruptor corpus, which doesn't exist yet). Writes GATEKEEPER_MODEL_PATH on success; whether that checkpoint actually serves live grading is a separate, explicit flag (GATEKEEPER_QUALITY_LIVE) — training alone never flips production behavior."""
    from app.core.config import GATEKEEPER_MODEL_PATH, GATEKEEPER_QUALITY_MIN_SAMPLES

    try:
        from app.modules.gatekeeper.feedback_loader import (
            FeedbackBatchConfig,
            iter_quality_batches,
            quality_sample_stats,
        )
    except ImportError as exc:
        return {"status": "error", "detail": f"ml extra not installed: {exc}"}

    stats = quality_sample_stats()
    if (
        stats["total"] < GATEKEEPER_QUALITY_MIN_SAMPLES
        or stats["approved"] == 0
        or stats["rejected"] == 0
    ):
        return {
            "status": "skipped",
            "reason": "insufficient_or_unbalanced_data",
            "min_samples": GATEKEEPER_QUALITY_MIN_SAMPLES,
            **stats,
        }

    try:
        from app.modules.gatekeeper.training import TrainConfig, train_gatekeeper

        loader = iter_quality_batches(FeedbackBatchConfig())
        summary = train_gatekeeper(loader, TrainConfig(out_path=GATEKEEPER_MODEL_PATH))
        return {**summary, **stats}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)[:300]}
