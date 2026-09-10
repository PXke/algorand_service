"""The "suggest a change" flow: a free, anonymous change/removal request against an already-approved registry entry.

Distinct from `submission_service` (a fresh listing): no domain dedupe, no
liveness check, no category -- just a human-reviewed note. Gates run
cheapest-first same as submission (CLAUDE.md's own convention here):
honeypot and rate limit are checked by the route before this module is
even called (reuses `submission_service`'s own hourly submit budget --
same abuse surface, no reason for a second counter), then field
validation, then the target-slug lookup.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from app.modules.ecosystem.models.domain import (
    MAX_REQUEST_MESSAGE_LENGTH,
    MIN_REQUEST_MESSAGE_LENGTH,
    REQUEST_KINDS,
    REQUEST_STATUS_DISMISSED,
    REQUEST_STATUS_PENDING,
    REQUEST_STATUS_RESOLVED,
    STATUS_APPROVED,
    EcosystemError,
    EntryRequest,
)
from app.modules.ecosystem.services.markdown_guard import reject_embedded_html
from app.modules.ecosystem.stores.factory import get_project_store, get_request_store

_MAX_CONTACT_LENGTH = 254


def normalize_kind(raw: str) -> str:
    """Canonical form of a request's kind; raises on anything outside the closed enum."""
    kind = (raw or "").strip().lower()
    if kind not in REQUEST_KINDS:
        raise EcosystemError("invalid_request", f"kind must be one of: {', '.join(REQUEST_KINDS)}")
    return kind


def normalize_message(raw: str) -> str:
    """Trim, length-bound, HTML-reject a request's message."""
    message = (raw or "").strip()
    if not (MIN_REQUEST_MESSAGE_LENGTH <= len(message) <= MAX_REQUEST_MESSAGE_LENGTH):
        raise EcosystemError(
            "invalid_request",
            f"message must be {MIN_REQUEST_MESSAGE_LENGTH}-{MAX_REQUEST_MESSAGE_LENGTH} characters",
        )
    reject_embedded_html(message, field_name="message")
    return message


def submit_request(*, slug: str, kind: str, message: str, contact: str = "") -> EntryRequest:
    """Validate and store one change/removal request against an approved entry.

    Raises EcosystemError("not_found", ...) when the target slug isn't an
    approved entry -- a request against a pending/rejected/nonexistent slug
    has nothing for a reviewer to act on (the same "approved only" bar the
    public detail read uses, `ecosystem_detail`).
    """
    target = get_project_store().get(slug)
    if target is None or target.status != STATUS_APPROVED:
        raise EcosystemError("not_found", "No registry entry for that slug", http_status=404)

    clean_kind = normalize_kind(kind)
    clean_message = normalize_message(message)

    now_epoch = int(datetime.now(tz=UTC).timestamp())
    item = EntryRequest(
        request_id=uuid4().hex,
        slug=slug,
        kind=clean_kind,
        message=clean_message,
        contact=(contact or "").strip()[:_MAX_CONTACT_LENGTH],
        status=REQUEST_STATUS_PENDING,
        created_at_epoch=now_epoch,
    )
    get_request_store().insert(item)
    return item


def list_requests(status: str, *, limit: int) -> list[EntryRequest]:
    """The admin queue read for one status partition, bounded."""
    return get_request_store().list_by_status(status, limit=limit)


def get_request(request_id: str) -> EntryRequest | None:
    """One request by id, any status (admin detail view)."""
    return get_request_store().get(request_id)


def resolve_request(request_id: str, *, wallet: str, status: str) -> EntryRequest:
    """Mark a pending request resolved or dismissed -- never mutates the target entry itself; a human acts through the ordinary admin edit/delete path first, then closes the request."""
    if status not in (REQUEST_STATUS_RESOLVED, REQUEST_STATUS_DISMISSED):
        raise EcosystemError(
            "invalid_request",
            f"status must be one of: {REQUEST_STATUS_RESOLVED}, {REQUEST_STATUS_DISMISSED}",
        )
    store = get_request_store()
    existing = store.get(request_id)
    if existing is None:
        raise EcosystemError("not_found", "No request for that id", http_status=404)

    updated = replace(
        existing,
        status=status,
        resolved_by=wallet,
        resolved_at_epoch=int(datetime.now(tz=UTC).timestamp()),
    )
    store.upsert(updated)
    return updated


__all__ = [
    "get_request",
    "list_requests",
    "normalize_kind",
    "normalize_message",
    "resolve_request",
    "submit_request",
]
