from __future__ import annotations

from app.celery_app import celery_app


@celery_app.task(name="app.tasks.chain_tail.poll_xgov_proposals")
def poll_xgov_proposals_task() -> dict[str, object]:
    """Hourly beat: enumerate xGov proposals from the registry escrow's created
    apps and signal each new (proposal, phase). See chain_tail.xgov_watch."""
    from app.core.config import XGOV_POLL_ENABLED
    from app.modules.chain_tail.xgov_watch import poll_xgov_proposals

    if not XGOV_POLL_ENABLED:
        return {"status": "skipped", "reason": "xgov_poll_disabled"}
    return poll_xgov_proposals()
