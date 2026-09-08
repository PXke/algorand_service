"""Storage interface for the Algorand Open Registry."""

from __future__ import annotations

from typing import Protocol

from app.modules.ecosystem.models.domain import EntryRequest, StoredProject, SubmissionQueueItem


class ProjectStore(Protocol):
    """Storage interface for registry entries."""

    def insert_if_domain_absent(self, item: StoredProject) -> bool:
        """Store the entry only if no entry exists for its domain, projections included.

        Returns True if this call created the entry, False if an entry for
        that domain already existed (nothing is written in that case). The
        check-and-write is atomic per store, so two concurrent submitters of
        the same domain cannot both get True.
        """
        ...

    def get(self, slug: str) -> StoredProject | None:
        """Return the current entry for a slug, or None if it does not exist."""
        ...

    def domain_status(self, domain: str) -> tuple[str, str] | None:
        """Return (slug, status) for an already-claimed domain, or None if unclaimed."""
        ...

    def upsert(self, item: StoredProject) -> None:
        """Create or replace an entry, projections included (used by admin edit/approve/reject)."""
        ...

    def delete(self, slug: str) -> bool:
        """Remove an entry, projections included. False if it did not exist."""
        ...

    def list_by_category(self, category: str, *, limit: int) -> list[StoredProject]:
        """Return approved entries in `category` (or the reserved 'all' pseudo-category), newest-approved-first, at most `limit`."""
        ...

    def list_by_tag(self, tag: str, *, limit: int) -> list[StoredProject]:
        """Return approved entries carrying `tag`, newest-approved-first, at most `limit`."""
        ...

    def list_by_submission_status(self, status: str, *, limit: int) -> list[SubmissionQueueItem]:
        """Return submissions in `status`, newest-submitted-first, at most `limit` (the admin queue read)."""
        ...

    def list_all(self, *, limit: int) -> list[StoredProject]:
        """Return every entry regardless of status (small, fully-enumerable table -- seed/liveness-beat use only)."""
        ...

    def set_liveness(
        self, slug: str, *, probed_at_epoch: int, reachable: bool, http_status: int
    ) -> None:
        """Update the liveness fields on the canonical row only (never denormalized into projections)."""
        ...

    def set_draft_description(self, slug: str, draft: str) -> None:
        """Update the admin-only draft blurb suggestion (never auto-published)."""
        ...


class RequestStore(Protocol):
    """Storage interface for "suggest a change" entry requests (design doc-adjacent, owner ask 2026-09-08)."""

    def insert(self, item: EntryRequest) -> None:
        """Store a new request (canonical row + its by-status queue row)."""
        ...

    def get(self, request_id: str) -> EntryRequest | None:
        """Return one request by id, or None if it does not exist."""
        ...

    def list_by_status(self, status: str, *, limit: int) -> list[EntryRequest]:
        """Return requests in `status`, newest-first, at most `limit` (the admin queue read)."""
        ...

    def upsert(self, item: EntryRequest) -> None:
        """Replace an existing request, moving its by-status queue row if the status changed (mirrors ProjectStore.upsert's diff-then-move shape)."""
        ...
