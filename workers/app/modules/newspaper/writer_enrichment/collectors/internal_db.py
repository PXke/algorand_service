from __future__ import annotations

from typing import Any


def collect_internal_context(*, service_id: str) -> dict[str, Any]:
    """Articles and snapshots we already stored for this service."""
    from app.modules.newspaper.article_store import count_articles_for_service
    from app.modules.newspaper.snapshot_store import get_latest_snapshot, source_id_for_service

    article_count = count_articles_for_service(service_id)
    snap = get_latest_snapshot(source_id_for_service(service_id))
    snapshot_note = "none"
    if snap:
        snapshot_note = f"hash={snap[0][:12]}… chars≈{len(snap[2] or '')}"

    return {
        "prior_articles": article_count,
        "latest_snapshot": snapshot_note,
        "platform_has_history": article_count > 0,
    }
