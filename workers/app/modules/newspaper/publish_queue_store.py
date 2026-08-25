"""``QueuedPublishRow`` and the compose-outcome vocabulary shared across the compose pipeline.

publish_queue itself (the Cassandra table this module used to read/write) was
dropped 2026-08-25 -- the editorial-room `artifacts`/`to_compose` system
(artifact_store.py, queue_drain_tasks.py) replaced it as the live compose
selection mechanism the day before, and publish_queue's one-deploy-cycle
dual-write rollback safety net was removed once that path proved stable in
prod. What's left here is the row shape and outcome-classification helpers
that turned out to be genuinely generic (not publish_queue-specific): every
compose input, whether reconstructed from an artifact
(queue_drain_tasks._artifact_to_queued_row) or (historically) read straight
off a publish_queue row, is shaped as a ``QueuedPublishRow`` so
``publish_from_queued_row`` and every ``_PRE_COMPOSE_GATES`` check have one
input shape to work against; and ``is_terminal_outcome`` classifies a
compose outcome the same way regardless of which system produced the row.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Compose outcomes that resolve a queue row (mark it done). Any other status
# (rate_limited, mistral_failed, review_queue_full, domain_capped, error, ...)
# leaves the row pending for a later attempt. Centralised so a new resolving
# status is added in one place — a missing one here is the bug class behind
# "the same topic keeps reappearing".
TERMINAL_OUTCOMES = frozenset(
    {
        "published",
        "review",
        # Auto-approved article stored to pending_feed_queue for the paced
        # backlog release — the queue row is resolved, the article exists.
        "approved_backlog",
        "duplicate",
        "duplicate_review_pending",
        # The writer itself declined to compose (abort_article) — a deliberate
        # judgment, not a transient failure. Retrying next beat would just
        # re-spend tokens re-researching the same dead subject; resolved like
        # "duplicate" so the row doesn't loop. An admin can still trigger a
        # manual recompose to override.
        "aborted_by_writer",
        # run_article_edit's success outcome — MISSING here caused a real
        # runaway loop (2026-07-17): a completed edit never resolved the
        # queue row, so the row stayed "pending" and drain_breaking_publish_queue
        # (fires every ~2 min) redrained and re-edited the same article on
        # every beat — 165 edit calls / 330 versions in under 4 hours on one
        # live article before this was caught and stopped by hand.
        "edited",
        # run_article_edit's failure outcome ({"reason": "update_failed"}) —
        # only reachable when update_article() returns False, which is ONLY
        # a permanent condition (linked article deleted, malformed id, or
        # never actually published) — a real Cassandra write error raises
        # instead of returning False, so retrying here can never help.
        # Same missing-terminal-status shape as "edited" above; fixed
        # alongside it rather than waiting for a second live incident.
        "failed",
    }
)


def is_terminal_outcome(outcome: dict[str, Any]) -> bool:
    """Check whether a compose outcome resolves its queue row rather than leaving it pending."""
    return outcome.get("status") in TERMINAL_OUTCOMES


@dataclass(frozen=True)
class QueuedPublishRow:
    """One row of compose input, shaped identically regardless of source (see module docstring)."""

    queue_id: str
    priority: int
    topic: str
    publish_kind: str
    service_id: str
    display_name: str
    scrape_url: str
    payload: dict[str, Any]
    created_at_epoch: int
    human_pick_day: str | None = None
