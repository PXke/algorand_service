"""Cassandra-backed Algorand Open Registry storage."""

from __future__ import annotations

from datetime import UTC, datetime

from algorand_shared.ecosystem_statements import EcosystemRequestStmts, EcosystemStmts

from app.core.cassandra import get_cassandra_session
from app.modules.ecosystem.models.domain import (
    DEFAULT_CATEGORY,
    DEFAULT_STAGE,
    REQUEST_STATUS_PENDING,
    STATUS_APPROVED,
    STATUS_PENDING,
    EntryRequest,
    StoredProject,
    SubmissionQueueItem,
)


def _dt(epoch: int) -> datetime | None:
    return datetime.fromtimestamp(epoch, tz=UTC) if epoch else None


def _epoch(value: datetime | None) -> int:
    """UTC epoch seconds from a stored timestamp (naive-but-UTC driver convention, see x402_directory's cassandra store for the incident this avoids)."""
    if value is None:
        return 0
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp())


def _row_to_project(row: object) -> StoredProject:
    return StoredProject(
        slug=row.slug,
        name=row.name or "",
        domain=row.domain or "",
        url=row.url or "",
        description=row.description or "",
        category=row.category or DEFAULT_CATEGORY,
        tags=sorted(row.tags or []),
        repo_url=getattr(row, "repo_url", None) or "",
        x402_url=getattr(row, "x402_url", None) or "",
        stage=getattr(row, "stage", None) or DEFAULT_STAGE,
        open_source=bool(getattr(row, "open_source", None)),
        source=getattr(row, "source", None) or "submitted",
        status=getattr(row, "status", None) or STATUS_PENDING,
        editor_pick=bool(getattr(row, "editor_pick", None)),
        contact=getattr(row, "contact", None) or "",
        service_id=getattr(row, "service_id", None) or "",
        draft_description=getattr(row, "draft_description", None) or "",
        category_suggestion=getattr(row, "category_suggestion", None) or "",
        submitted_at_epoch=_epoch(getattr(row, "submitted_at", None)),
        reviewed_at_epoch=_epoch(getattr(row, "reviewed_at", None)),
        reviewed_by=getattr(row, "reviewed_by", None) or "",
        reject_reason=getattr(row, "reject_reason", None) or "",
        last_probed_at_epoch=_epoch(getattr(row, "last_probed_at", None)),
        reachable=getattr(row, "reachable", None),
        last_http_status=int(getattr(row, "last_http_status", None) or 0),
    )


def _canonical_params(item: StoredProject) -> tuple:
    return (
        item.slug,
        item.name,
        item.domain,
        item.url,
        item.repo_url,
        item.x402_url,
        item.description,
        item.category,
        set(item.tags),
        item.stage,
        item.open_source,
        item.source,
        item.status,
        item.editor_pick,
        item.contact,
        item.service_id,
        item.draft_description,
        _dt(item.submitted_at_epoch),
        _dt(item.reviewed_at_epoch),
        item.reviewed_by,
        item.reject_reason,
        _dt(item.last_probed_at_epoch),
        item.reachable,
        item.last_http_status or None,
        item.category_suggestion,
    )


def _projection_params(key: str, approved_at: datetime | None, item: StoredProject) -> tuple:
    """Bind params shared by INSERT_BY_CATEGORY/INSERT_BY_TAG (same column order after the partition key + approved_at)."""
    return (
        key,
        approved_at,
        item.slug,
        item.name,
        item.domain,
        item.url,
        item.description,
        set(item.tags),
        item.category,
        item.stage,
        item.open_source,
        item.editor_pick,
        item.x402_url,
    )


class CassandraProjectStore:
    """Cassandra-backed Algorand Open Registry storage.

    Store-before-mark (CLAUDE.md section 2): the by-domain LWT claim lands
    before the canonical row, which lands before any category/tag/status
    projection. Every write path mirrors x402_directory's CassandraListingStore
    shape (read `previous` before a projection-affecting write, delete the
    superseded rows, insert the fresh ones).
    """

    def insert_if_domain_absent(self, item: StoredProject) -> bool:
        """Claim the domain via LWT, then write the canonical row and projections. False if the domain was already claimed."""
        session = get_cassandra_session()
        result = session.execute(
            EcosystemStmts.INSERT_BY_DOMAIN_IF_ABSENT, (item.domain, item.slug, item.status)
        )
        if not result.was_applied:
            return False
        session.execute(EcosystemStmts.UPSERT, _canonical_params(item))
        session.execute(
            EcosystemStmts.INSERT_BY_STATUS,
            (
                item.status,
                _dt(item.submitted_at_epoch),
                item.slug,
                item.name,
                item.domain,
                item.url,
                item.category,
                item.source,
            ),
        )
        if item.status == STATUS_APPROVED:
            self._write_approved_projections(item)
        return True

    def get(self, slug: str) -> StoredProject | None:
        """Return the current entry for a slug, or None if it does not exist."""
        session = get_cassandra_session()
        row = session.execute(EcosystemStmts.GET, (slug,)).one()
        return None if row is None else _row_to_project(row)

    def domain_status(self, domain: str) -> tuple[str, str] | None:
        """Return (slug, status) for an already-claimed domain, or None if unclaimed."""
        session = get_cassandra_session()
        row = session.execute(EcosystemStmts.GET_BY_DOMAIN, (domain,)).one()
        return None if row is None else (row.slug, row.status or STATUS_PENDING)

    def upsert(self, item: StoredProject) -> None:
        """Create or replace an entry: canonical row, by-domain status, submission-queue partition move, category/tag projection diff."""
        session = get_cassandra_session()
        previous = self.get(item.slug)
        session.execute(EcosystemStmts.UPSERT, _canonical_params(item))
        session.execute(EcosystemStmts.UPDATE_BY_DOMAIN_STATUS, (item.status, item.domain))

        if (
            previous is None
            or previous.status != item.status
            or previous.submitted_at_epoch != item.submitted_at_epoch
        ):
            if previous is not None:
                session.execute(
                    EcosystemStmts.DELETE_BY_STATUS,
                    (previous.status, _dt(previous.submitted_at_epoch), item.slug),
                )
            session.execute(
                EcosystemStmts.INSERT_BY_STATUS,
                (
                    item.status,
                    _dt(item.submitted_at_epoch),
                    item.slug,
                    item.name,
                    item.domain,
                    item.url,
                    item.category,
                    item.source,
                ),
            )

        if previous is not None and previous.status == STATUS_APPROVED:
            approved_at = _dt(previous.reviewed_at_epoch)
            for category in previous.projection_categories():
                session.execute(
                    EcosystemStmts.DELETE_BY_CATEGORY, (category, approved_at, item.slug)
                )
            for tag in previous.projection_tags():
                session.execute(EcosystemStmts.DELETE_BY_TAG, (tag, approved_at, item.slug))
        if item.status == STATUS_APPROVED:
            self._write_approved_projections(item)

    def _write_approved_projections(self, item: StoredProject) -> None:
        session = get_cassandra_session()
        approved_at = _dt(item.reviewed_at_epoch)
        for category in item.projection_categories():
            session.execute(
                EcosystemStmts.INSERT_BY_CATEGORY, _projection_params(category, approved_at, item)
            )
        for tag in item.projection_tags():
            session.execute(
                EcosystemStmts.INSERT_BY_TAG, _projection_params(tag, approved_at, item)
            )

    def delete(self, slug: str) -> bool:
        """Remove an entry, projections included. False if it did not exist."""
        session = get_cassandra_session()
        existing = self.get(slug)
        if existing is None:
            return False
        session.execute(EcosystemStmts.DELETE_BY_DOMAIN, (existing.domain,))
        session.execute(
            EcosystemStmts.DELETE_BY_STATUS,
            (existing.status, _dt(existing.submitted_at_epoch), slug),
        )
        if existing.status == STATUS_APPROVED:
            approved_at = _dt(existing.reviewed_at_epoch)
            for category in existing.projection_categories():
                session.execute(EcosystemStmts.DELETE_BY_CATEGORY, (category, approved_at, slug))
            for tag in existing.projection_tags():
                session.execute(EcosystemStmts.DELETE_BY_TAG, (tag, approved_at, slug))
        session.execute(EcosystemStmts.DELETE, (slug,))
        return True

    def list_by_category(self, category: str, *, limit: int) -> list[StoredProject]:
        """Point reads back through GET for every candidate slug in the projection, so the served row is always the fresh canonical one."""
        session = get_cassandra_session()
        rows = session.execute(EcosystemStmts.LIST_BY_CATEGORY, (category, limit))
        return [item for item in (self.get(row.slug) for row in rows) if item is not None]

    def list_by_tag(self, tag: str, *, limit: int) -> list[StoredProject]:
        """Point reads back through GET for every candidate slug in the projection, so the served row is always the fresh canonical one."""
        session = get_cassandra_session()
        rows = session.execute(EcosystemStmts.LIST_BY_TAG, (tag, limit))
        return [item for item in (self.get(row.slug) for row in rows) if item is not None]

    def list_by_submission_status(self, status: str, *, limit: int) -> list[SubmissionQueueItem]:
        """Bounded read of the admin queue projection."""
        session = get_cassandra_session()
        rows = session.execute(EcosystemStmts.LIST_BY_STATUS, (status, limit))
        return [
            SubmissionQueueItem(
                slug=row.slug,
                name=row.name or "",
                domain=row.domain or "",
                url=row.url or "",
                category=row.category or DEFAULT_CATEGORY,
                source=row.source or "submitted",
                submitted_at_epoch=_epoch(row.submitted_at),
                status=status,
            )
            for row in rows
        ]

    def list_all(self, *, limit: int) -> list[StoredProject]:
        """Bounded full-table scan (small, fully-enumerable table -- seed/liveness-beat use only, see EcosystemStmts.LIST_ALL)."""
        session = get_cassandra_session()
        rows = session.execute(EcosystemStmts.LIST_ALL)
        items = [_row_to_project(row) for row in rows]
        return items[: max(0, limit)]

    def set_liveness(
        self, slug: str, *, probed_at_epoch: int, reachable: bool, http_status: int
    ) -> None:
        """Update the liveness fields on the canonical row only (never denormalized into projections)."""
        session = get_cassandra_session()
        session.execute(
            EcosystemStmts.SET_LIVENESS,
            (_dt(probed_at_epoch), reachable, http_status or None, slug),
        )

    def set_draft_description(self, slug: str, draft: str) -> None:
        """Update the admin-only draft blurb suggestion."""
        session = get_cassandra_session()
        session.execute(EcosystemStmts.SET_DRAFT_DESCRIPTION, (draft, slug))


def _row_to_request(row: object) -> EntryRequest:
    return EntryRequest(
        request_id=row.request_id,
        slug=row.slug or "",
        kind=row.kind or "",
        message=row.message or "",
        contact=getattr(row, "contact", None) or "",
        status=getattr(row, "status", None) or REQUEST_STATUS_PENDING,
        created_at_epoch=_epoch(getattr(row, "created_at", None)),
        resolved_at_epoch=_epoch(getattr(row, "resolved_at", None)),
        resolved_by=getattr(row, "resolved_by", None) or "",
    )


def _request_params(item: EntryRequest) -> tuple:
    return (
        item.request_id,
        item.slug,
        item.kind,
        item.message,
        item.contact,
        item.status,
        _dt(item.created_at_epoch),
        _dt(item.resolved_at_epoch),
        item.resolved_by,
    )


class CassandraRequestStore:
    """Cassandra-backed "suggest a change" request storage. Same store-before-mark, diff-then-move shape as CassandraProjectStore."""

    def insert(self, item: EntryRequest) -> None:
        """Store a new request: canonical row, then its by-status queue row."""
        session = get_cassandra_session()
        session.execute(EcosystemRequestStmts.UPSERT, _request_params(item))
        session.execute(
            EcosystemRequestStmts.INSERT_BY_STATUS,
            (item.status, _dt(item.created_at_epoch), item.request_id, item.slug, item.kind),
        )

    def get(self, request_id: str) -> EntryRequest | None:
        """Return one request by id, or None if it does not exist."""
        session = get_cassandra_session()
        row = session.execute(EcosystemRequestStmts.GET, (request_id,)).one()
        return None if row is None else _row_to_request(row)

    def list_by_status(self, status: str, *, limit: int) -> list[EntryRequest]:
        """Point reads back through GET for every candidate id in the projection, so the served row is always fresh."""
        session = get_cassandra_session()
        rows = session.execute(EcosystemRequestStmts.LIST_BY_STATUS, (status, limit))
        return [item for item in (self.get(row.request_id) for row in rows) if item is not None]

    def upsert(self, item: EntryRequest) -> None:
        """Replace an existing request, moving its by-status queue row if the status changed."""
        session = get_cassandra_session()
        previous = self.get(item.request_id)
        session.execute(EcosystemRequestStmts.UPSERT, _request_params(item))
        if previous is not None and previous.status != item.status:
            session.execute(
                EcosystemRequestStmts.DELETE_BY_STATUS,
                (previous.status, _dt(previous.created_at_epoch), item.request_id),
            )
            session.execute(
                EcosystemRequestStmts.INSERT_BY_STATUS,
                (item.status, _dt(item.created_at_epoch), item.request_id, item.slug, item.kind),
            )
