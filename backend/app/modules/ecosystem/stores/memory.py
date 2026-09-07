"""In-memory Algorand Open Registry store for dev and tests."""

from __future__ import annotations

from dataclasses import replace

from app.modules.ecosystem.models.domain import STATUS_APPROVED, StoredProject, SubmissionQueueItem


class InMemoryProjectStore:
    """In-memory registry storage, mirroring the Cassandra store's table shape.

    `_by_category`/`_by_tag` hold only APPROVED entries (mirrors the real
    projections, which are written only on approve), so tests can observe
    that a reject/delete really removes them. `_by_status` mirrors the admin
    queue projection and is kept in sync with every status change.
    """

    def __init__(self) -> None:
        """Start with every table empty."""
        self._items: dict[str, StoredProject] = {}
        self._by_domain: dict[str, tuple[str, str]] = {}
        self._by_category: dict[str, dict[str, StoredProject]] = {}
        self._by_tag: dict[str, dict[str, StoredProject]] = {}

    def insert_if_domain_absent(self, item: StoredProject) -> bool:
        """Store the entry only if its domain is not yet claimed. True if it was stored."""
        if item.domain in self._by_domain:
            return False
        self._by_domain[item.domain] = (item.slug, item.status)
        self._items[item.slug] = item
        self._sync_projections(item, previous=None)
        return True

    def get(self, slug: str) -> StoredProject | None:
        """Return the current entry for a slug, or None if it does not exist."""
        return self._items.get(slug)

    def domain_status(self, domain: str) -> tuple[str, str] | None:
        """Return (slug, status) for an already-claimed domain, or None if unclaimed."""
        return self._by_domain.get(domain)

    def upsert(self, item: StoredProject) -> None:
        """Create or replace an entry, category/tag projections synced to its current status."""
        previous = self._items.get(item.slug)
        self._items[item.slug] = item
        self._by_domain[item.domain] = (item.slug, item.status)
        self._sync_projections(item, previous=previous)

    def _sync_projections(self, item: StoredProject, *, previous: StoredProject | None) -> None:
        if previous is not None and previous.status == STATUS_APPROVED:
            for category in previous.projection_categories():
                self._by_category.get(category, {}).pop(item.slug, None)
            for tag in previous.projection_tags():
                self._by_tag.get(tag, {}).pop(item.slug, None)
        if item.status == STATUS_APPROVED:
            for category in item.projection_categories():
                self._by_category.setdefault(category, {})[item.slug] = item
            for tag in item.projection_tags():
                self._by_tag.setdefault(tag, {})[item.slug] = item

    def delete(self, slug: str) -> bool:
        """Remove an entry, projections included. False if it did not exist."""
        existing = self._items.pop(slug, None)
        if existing is None:
            return False
        self._by_domain.pop(existing.domain, None)
        if existing.status == STATUS_APPROVED:
            for category in existing.projection_categories():
                self._by_category.get(category, {}).pop(slug, None)
            for tag in existing.projection_tags():
                self._by_tag.get(tag, {}).pop(slug, None)
        return True

    @staticmethod
    def _newest_approved_first(items: list[StoredProject], limit: int) -> list[StoredProject]:
        ordered = sorted(items, key=lambda i: (-i.reviewed_at_epoch, i.slug))
        return ordered[: max(0, limit)]

    def list_by_category(self, category: str, *, limit: int) -> list[StoredProject]:
        """Return approved entries in `category`, newest-approved-first, at most `limit`."""
        return self._newest_approved_first(
            list(self._by_category.get(category, {}).values()), limit
        )

    def list_by_tag(self, tag: str, *, limit: int) -> list[StoredProject]:
        """Return approved entries carrying `tag`, newest-approved-first, at most `limit`."""
        return self._newest_approved_first(list(self._by_tag.get(tag, {}).values()), limit)

    def list_by_submission_status(self, status: str, *, limit: int) -> list[SubmissionQueueItem]:
        """Return submissions in `status`, newest-submitted-first, at most `limit`."""
        matching = [i for i in self._items.values() if i.status == status]
        ordered = sorted(matching, key=lambda i: (-i.submitted_at_epoch, i.slug))[: max(0, limit)]
        return [
            SubmissionQueueItem(
                slug=i.slug,
                name=i.name,
                domain=i.domain,
                url=i.url,
                category=i.category,
                source=i.source,
                submitted_at_epoch=i.submitted_at_epoch,
                status=i.status,
            )
            for i in ordered
        ]

    def list_all(self, *, limit: int) -> list[StoredProject]:
        """Return every entry regardless of status, at most `limit`."""
        return list(self._items.values())[: max(0, limit)]

    def set_liveness(
        self, slug: str, *, probed_at_epoch: int, reachable: bool, http_status: int
    ) -> None:
        """Update the liveness fields on the canonical row only."""
        existing = self._items.get(slug)
        if existing is None:
            return
        self._items[slug] = replace(
            existing,
            last_probed_at_epoch=probed_at_epoch,
            reachable=reachable,
            last_http_status=http_status,
        )

    def set_draft_description(self, slug: str, draft: str) -> None:
        """Update the admin-only draft blurb suggestion."""
        existing = self._items.get(slug)
        if existing is None:
            return
        self._items[slug] = replace(existing, draft_description=draft)
