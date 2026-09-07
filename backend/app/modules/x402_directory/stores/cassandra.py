"""Cassandra-backed x402 directory listing storage."""

from __future__ import annotations

from datetime import UTC, datetime

from algorand_shared.x402_statements import PROBE_QUEUE_NEVER_PROBED, X402ProbeStmts

from app.core.cassandra import get_cassandra_session
from app.core.statements import X402DirectoryStmts
from app.modules.x402_directory.models.domain import (
    AUTO_DISCOVERED_PARTITION,
    DEFAULT_CATEGORY,
    DIRECTORY_PARTITION,
    SOURCE_AUTO_DISCOVERED,
    SOURCE_PAID,
    StoredListing,
    StoredProbe,
)


def _dt(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, tz=UTC)


def _epoch(value: datetime | None) -> int:
    """UTC epoch seconds from a stored timestamp.

    The Cassandra driver returns timezone-NAIVE datetimes that are already UTC wall-clock values;
    calling .timestamp() directly makes Python assume the server's LOCAL zone and silently shift
    the result (same bug class fixed in news/stores/cassandra.py and x402_social/stores/cassandra.py
    -- that fix never got propagated here).
    """
    if value is None:
        return 0
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp())


def _row_to_listing(row: object) -> StoredListing:
    return StoredListing(
        url_hash=row.url_hash,
        url=row.url or "",
        price=row.price or "",
        description=row.description or "",
        schema_json=row.schema_json or "",
        settlement_tx_id=row.settlement_tx_id or "",
        term_end_epoch=_epoch(row.term_end),
        created_at_epoch=_epoch(row.created_at),
        assets=sorted(row.assets or []),
        tags=sorted(row.tags or []),
        payer=getattr(row, "payer", None) or "",
        verified_wallet=getattr(row, "verified_wallet", None) or "",
        verified_at_epoch=_epoch(getattr(row, "verified_at", None)),
        # Pre-099 rows read back null; they are DEFAULT_CATEGORY by definition.
        category=getattr(row, "category", None) or DEFAULT_CATEGORY,
        # Pre-104 rows read back null; unset either way, never a fabricated claim.
        reimburses=bool(getattr(row, "reimburses", None)),
        contact=getattr(row, "contact", None) or "",
        # Pre-113 rows read back null; every row before that migration was,
        # without exception, a real paid listing (SOURCE_PAID).
        source=getattr(row, "source", None) or SOURCE_PAID,
        discovered_from=getattr(row, "discovered_from", None) or "",
        # Pre-114 rows read back null: "not boosted," exactly right since
        # boosting did not exist before that migration.
        boosted_until_epoch=_epoch(getattr(row, "boosted_until", None)),
    )


def _row_to_probe(row: object) -> StoredProbe:
    return StoredProbe(
        url_hash=row.url_hash,
        url=row.url or "",
        probed_at_epoch=_epoch(row.probed_at),
        reachable=bool(row.reachable),
        http_status=int(row.http_status or 0),
        latency_ms=int(row.latency_ms or 0),
        served_valid_402=bool(row.served_valid_402),
        payto_seen=row.payto_seen or "",
        error=row.error or "",
    )


def _badge_params(item: StoredListing) -> tuple:
    """The verified_wallet / verified_at bind params every listing INSERT ends with.

    Written on every write so the canonical row and both projections always
    agree on the badge: a same-owner relist carries it, an ownership change
    writes it empty (the service sets the fields; see ListingService.create).
    """
    return (item.verified_wallet, _dt(item.verified_at_epoch) if item.verified_at_epoch else None)


def _boosted_until_param(item: StoredListing | object) -> object:
    """The boosted_until bind value: a timestamp when boosted, else None (never a fabricated 0)."""
    return _dt(item.boosted_until_epoch) if item.boosted_until_epoch else None


def _canonical_params(item: StoredListing) -> tuple:
    """Bind params shared by UPSERT_LISTING and INSERT_LISTING_IF_ABSENT."""
    return (
        item.url_hash,
        item.url,
        item.price,
        set(item.assets),
        item.description,
        item.schema_json,
        set(item.tags),
        _dt(item.term_end_epoch),
        item.settlement_tx_id,
        _dt(item.created_at_epoch),
        item.payer,
        item.category,
        *_badge_params(item),
        item.reimburses,
        item.contact,
        item.source,
        item.discovered_from,
        _boosted_until_param(item),
    )


def _projection_params(partition: str, item: StoredListing) -> tuple:
    """Bind params shared by INSERT_RECENCY and INSERT_BY_TAG (same column order after the partition key).

    NOT INSERT_AUTO_DISCOVERED_RECENCY, which never carries boosted_until (migration 114) --
    see that table's own comment in the migration and _write_projections' auto-discovered branch,
    which builds its own tuple rather than calling this helper.
    """
    return (
        partition,
        _dt(item.created_at_epoch),
        item.url_hash,
        item.url,
        item.price,
        set(item.assets),
        item.description,
        item.schema_json,
        set(item.tags),
        _dt(item.term_end_epoch),
        item.settlement_tx_id,
        item.payer,
        item.category,
        *_badge_params(item),
        item.reimburses,
        item.contact,
        item.source,
        item.discovered_from,
        _boosted_until_param(item),
    )


def _probe_queue_seed_params(item: StoredListing) -> tuple:
    """Bind params for X402ProbeStmts.INSERT_PROBE_QUEUE's seed write (paid listings only).

    Matches that statement's column order: directory, last_probed_at,
    url_hash, url, created_at, tags, term_end, payer, verified_wallet,
    category. Always PROBE_QUEUE_NEVER_PROBED, never `now` -- see
    _write_projections' own docstring for why that constant is what keeps
    this insert idempotent.
    """
    return (
        DIRECTORY_PARTITION,
        PROBE_QUEUE_NEVER_PROBED,
        item.url_hash,
        item.url,
        _dt(item.created_at_epoch),
        set(item.tags),
        _dt(item.term_end_epoch),
        item.payer,
        item.verified_wallet,
        item.category,
    )


def _auto_discovered_projection_params(partition: str, item: StoredListing) -> tuple:
    """Bind params for INSERT_AUTO_DISCOVERED_RECENCY.

    Same shape as _projection_params minus boosted_until, which that table does not have
    (an auto-discovered stub is never boosted).
    """
    return (
        partition,
        _dt(item.created_at_epoch),
        item.url_hash,
        item.url,
        item.price,
        set(item.assets),
        item.description,
        item.schema_json,
        set(item.tags),
        _dt(item.term_end_epoch),
        item.settlement_tx_id,
        item.payer,
        item.category,
        *_badge_params(item),
        item.reimburses,
        item.contact,
        item.source,
        item.discovered_from,
    )


class CassandraListingStore:
    """Cassandra-backed x402 directory listing storage.

    Four tables: the canonical x402_listings row (holds BOTH paid and
    auto-discovered listings), the newest-first recency projection (090) and
    the per-tag projection (096) -- both PAID-ONLY -- which also carries one
    reserved `category:<name>` row per listing (099), and the auto-discovered
    ("unclaimed") recency projection (113), which is auto-discovered-ONLY.
    Every write path goes canonical row first, projections second (store
    before mark): if a projection write then fails, the listing exists and is
    missing only from a feed, which a re-list (or re-import) repairs. The
    reverse order could leave a feed entry pointing at a listing that was
    never durably stored.

    The paid/auto-discovered projection split (migration 113) exists so an
    import of many free stubs from a public facilitator feed can never crowd
    a real paid listing out of LIST_RECENT/LIST_BY_TAG's own feeds -- see
    ListingService.search()'s paid-first merge, which reads list_recent() and
    list_auto_discovered() as two separate, independently-bounded sources
    rather than one shared, size-competing feed. A "claim" (a real payer
    relisting a url that was previously only an auto-discovered stub) moves
    the row from the auto-discovered feed to the paid ones -- see
    _write_projections' claim-transition branch below.
    """

    def insert_if_absent(self, item: StoredListing) -> bool:
        """Store the listing only if no x402_listings row exists for its url_hash.

        A lightweight transaction (INSERT ... IF NOT EXISTS), so two
        concurrent first-time listers of the same url cannot both succeed:
        exactly one INSERT is applied and the other returns False having
        written nothing, including no projection rows. Projections are
        written only on the applied path, and with no previous listing to
        supersede there is nothing to delete first.
        """
        session = get_cassandra_session()
        result = session.execute(
            X402DirectoryStmts.INSERT_LISTING_IF_ABSENT, _canonical_params(item)
        )
        if not result.was_applied:
            return False
        self._write_projections(item, previous=None)
        return True

    def upsert(self, item: StoredListing) -> None:
        """Create or replace the listing for one endpoint URL, projections included.

        A superseded projection row is deleted BEFORE the new one is inserted.
        A crash in that window drops the endpoint from that feed until it is
        re-listed; the opposite order would leave a permanent duplicate feed
        row advertising the previous, already-expired term.
        """
        session = get_cassandra_session()
        previous = self.get(item.url_hash)
        session.execute(X402DirectoryStmts.UPSERT_LISTING, _canonical_params(item))
        self._write_projections(item, previous=previous)

    def _write_projections(self, item: StoredListing, *, previous: StoredListing | None) -> None:
        """Refresh the recency and by-tag projections for `item`, superseding `previous`'s rows.

        Auto-discovered items (item.source == SOURCE_AUTO_DISCOVERED) never
        touch the paid recency/by-tag projections at all -- they get their
        own, separate feed (INSERT_AUTO_DISCOVERED_RECENCY, migration 113;
        see the class docstring for why). discovery_import.py preserves the
        existing row's created_at_epoch on a refresh, so that write is always
        an in-place rewrite of the same clustering key -- nothing to delete
        first.

        A "claim" (a real payer relisting a url whose only existing listing
        was an auto-discovered stub) is the one case where `previous.source`
        and `item.source` disagree: the stub's row in the auto-discovered
        feed is superseded by this write under a DIFFERENT clustering key
        (create() always re-stamps created_at for a fresh paid term), so it
        must be deleted explicitly here -- the paid-projection logic below
        has no way to know about it. `previous` is then treated as absent for
        the rest of this call: the stub was never in the paid projections, so
        there is nothing there to diff against, only a fresh insert to make.

        For an ordinary paid relist, a previous row is deleted only when the
        new INSERT will not overwrite it in place: for the recency feed that
        is when created_at moved; for the tag feed it is when created_at
        moved OR the tag was dropped from the listing. Deleting a row the
        INSERT is about to rewrite at the same key would be a tombstone
        racing its own replacement. The tag feed's key set is
        StoredListing.projection_tags(), real tags plus the reserved category
        row, so a category change drops the old category row exactly like a
        dropped tag. A boost (renew(), same created_at, new boosted_until)
        rewrites every row in place, which is how the projections pick up
        the new boosted_until.

        Also seeds x402_probe_queue (migration 118, same-night adversarial
        review finding #3): a PAID listing (never an auto-discovered stub,
        which the probe sweep has never covered) is written into the probe
        fairness queue at PROBE_QUEUE_NEVER_PROBED so it gets its first
        health check on the sweep's next run, whether this is a first-time
        listing or a relist. That seed key is a CONSTANT, not `now`, which
        is what makes this insert idempotent -- relisting the same url twice
        before workers ever probes it just overwrites the same queue row.
        No delete-before-insert is needed for the case where the listing was
        already probed for real (a queue row sitting at some real past
        last_probed_at): this seed's job is only to guarantee re-verification
        happens soon, not to keep the queue at exactly one row per listing at
        every instant -- the stale real-timestamp row is self-pruning, since
        workers reprobes and repositions whatever it reads once that row's
        turn comes up in the round-robin, converging back to one row. See
        shared/algorand_shared/x402_statements.py's X402ProbeStmts and
        workers/app/modules/x402_probe/service.py's CassandraProbeRepository
        for the read/reposition side.
        """
        session = get_cassandra_session()
        if item.source == SOURCE_AUTO_DISCOVERED:
            session.execute(
                X402DirectoryStmts.INSERT_AUTO_DISCOVERED_RECENCY,
                _auto_discovered_projection_params(AUTO_DISCOVERED_PARTITION, item),
            )
            return
        if previous is not None and previous.source == SOURCE_AUTO_DISCOVERED:
            session.execute(
                X402DirectoryStmts.DELETE_AUTO_DISCOVERED_RECENCY,
                (AUTO_DISCOVERED_PARTITION, _dt(previous.created_at_epoch), item.url_hash),
            )
            previous = None
        moved = previous is not None and previous.created_at_epoch != item.created_at_epoch
        if previous is not None and moved:
            session.execute(
                X402DirectoryStmts.DELETE_RECENCY,
                (DIRECTORY_PARTITION, _dt(previous.created_at_epoch), item.url_hash),
            )
        session.execute(
            X402DirectoryStmts.INSERT_RECENCY, _projection_params(DIRECTORY_PARTITION, item)
        )
        if previous is not None:
            new_tags = set(item.projection_tags())
            for tag in previous.projection_tags():
                if moved or tag not in new_tags:
                    session.execute(
                        X402DirectoryStmts.DELETE_BY_TAG,
                        (tag, _dt(previous.created_at_epoch), item.url_hash),
                    )
        for tag in item.projection_tags():
            session.execute(X402DirectoryStmts.INSERT_BY_TAG, _projection_params(tag, item))
        session.execute(X402ProbeStmts.INSERT_PROBE_QUEUE, _probe_queue_seed_params(item))

    def get(self, url_hash: str) -> StoredListing | None:
        """Return the current listing for a URL hash, or None if not listed."""
        session = get_cassandra_session()
        row = session.execute(X402DirectoryStmts.GET_LISTING, (url_hash,)).one()
        return None if row is None else _row_to_listing(row)

    def list_recent(self, *, limit: int) -> list[StoredListing]:
        """Return listings newest-first, at most `limit` of them."""
        session = get_cassandra_session()
        rows = session.execute(X402DirectoryStmts.LIST_RECENT, (DIRECTORY_PARTITION, limit))
        return [_row_to_listing(row) for row in rows]

    def list_by_tag(self, tag: str, *, limit: int) -> list[StoredListing]:
        """Return listings carrying the (already normalized) tag, newest-first, at most `limit`."""
        session = get_cassandra_session()
        rows = session.execute(X402DirectoryStmts.LIST_BY_TAG, (tag, limit))
        return [_row_to_listing(row) for row in rows]

    def delete(self, url_hash: str) -> bool:
        """Remove the listing for a URL hash, projections included. False if it did not exist.

        Every projection is keyed on (partition, created_at, url_hash), so
        deleting it needs the exact created_at (and, for a paid listing, tag
        set) of the row being removed, read from x402_listings first (same
        reason upsert() reads `previous` before writing). An auto-discovered
        listing was only ever in the auto-discovered feed (see
        _write_projections), never the paid recency/by-tag ones, so only that
        one projection is touched for it. The canonical x402_listings row is
        deleted last -- if a crash lands between the deletes, the listing is
        gone from the free feeds but the canonical row (and its owner-still-
        set payer) still exists, which is the safer of the half-done states
        for an admin removal.
        """
        session = get_cassandra_session()
        existing = self.get(url_hash)
        if existing is None:
            return False
        created_at = _dt(existing.created_at_epoch)
        if existing.source == SOURCE_AUTO_DISCOVERED:
            session.execute(
                X402DirectoryStmts.DELETE_AUTO_DISCOVERED_RECENCY,
                (AUTO_DISCOVERED_PARTITION, created_at, url_hash),
            )
        else:
            session.execute(
                X402DirectoryStmts.DELETE_RECENCY, (DIRECTORY_PARTITION, created_at, url_hash)
            )
            for tag in existing.projection_tags():
                session.execute(X402DirectoryStmts.DELETE_BY_TAG, (tag, created_at, url_hash))
        session.execute(X402DirectoryStmts.DELETE_LISTING, (url_hash,))
        return True

    def list_auto_discovered(self, *, limit: int) -> list[StoredListing]:
        """Return auto-discovered listings newest-imported-first, at most `limit`.

        Reads the dedicated auto-discovered recency projection (migration
        113) -- never x402_listings_by_recency, which holds only paid
        listings (see class docstring) -- so a directory holding thousands of
        imported stubs can never crowd a paid listing out of list_recent().
        """
        session = get_cassandra_session()
        rows = session.execute(
            X402DirectoryStmts.LIST_AUTO_DISCOVERED, (AUTO_DISCOVERED_PARTITION, limit)
        )
        return [_row_to_listing(row) for row in rows]

    def latest_probe(self, url_hash: str) -> StoredProbe | None:
        """Point read of x402_probe_latest (written by the workers probe beat); None if never probed."""
        session = get_cassandra_session()
        row = session.execute(X402ProbeStmts.GET_LATEST, (url_hash,)).one()
        return None if row is None else _row_to_probe(row)

    def probe_history(self, url_hash: str, *, limit: int) -> list[StoredProbe]:
        """Bounded read of x402_probe_results (097), newest first (its own clustering order)."""
        session = get_cassandra_session()
        rows = session.execute(X402ProbeStmts.LIST_HISTORY, (url_hash, limit))
        return [_row_to_probe(row) for row in rows]
