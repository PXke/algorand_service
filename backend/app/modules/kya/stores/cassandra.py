"""Cassandra-backed KYA enrollment and lookup-event storage."""

from __future__ import annotations

from datetime import UTC, datetime

from app.modules.kya.models.domain import StoredEnrollment, StoredLookupEvent

# Bound on the whole-partition scan a payout retry does to find one lookup
# event by payment_txid (see GET_LOOKUP_EVENTS_FOR_WALLET) -- generous for a
# per-wallet audit trail of paid lookups, never unbounded.
_MAX_LOOKUP_EVENTS_SCANNED = 2000


class CassandraEnrollmentStore:
    """Cassandra-backed KYA enrollment storage."""

    def upsert(self, item: StoredEnrollment) -> None:
        """Insert or update a KYA enrollment record."""
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import KycStmts

        session = get_cassandra_session()
        session.execute(
            KycStmts.UPSERT_ENROLLMENT,
            (
                item.wallet_address,
                datetime.fromtimestamp(item.enrolled_at_epoch, tz=UTC),
                datetime.fromtimestamp(item.updated_at_epoch, tz=UTC),
                item.consent_signature_b64,
                item.wallet_age_round,
                item.recent_tx_count,
                item.kyc_level,
            ),
        )

    def get(self, wallet_address: str) -> StoredEnrollment | None:
        """Look up a wallet's stored enrollment, or None if not enrolled."""
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import KycStmts

        session = get_cassandra_session()
        row = session.execute(KycStmts.GET_ENROLLMENT, (wallet_address,)).one()
        if row is None:
            return None
        enrolled_at = row.enrolled_at
        updated_at = row.updated_at
        return StoredEnrollment(
            wallet_address=row.wallet_address,
            enrolled_at_epoch=int(enrolled_at.timestamp()) if enrolled_at else 0,
            updated_at_epoch=int(updated_at.timestamp()) if updated_at else 0,
            consent_signature_b64=row.consent_signature_b64,
            wallet_age_round=row.wallet_age_round,
            recent_tx_count=row.recent_tx_count or 0,
            kyc_level=row.kyc_level,
        )

    def record_lookup_event(
        self,
        *,
        wallet_address: str,
        payer_address: str,
        payment_txid: str,
        found: bool,
        payout_status: str,
        payout_txid: str | None = None,
        payout_error: str | None = None,
    ) -> None:
        """Record the outcome of one paid lookup, for audit and payout retry."""
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import KycStmts

        session = get_cassandra_session()
        session.execute(
            KycStmts.INSERT_LOOKUP_EVENT,
            (
                wallet_address,
                payer_address,
                payment_txid,
                found,
                payout_status,
                payout_txid,
                payout_error,
            ),
        )

    def find_lookup_event(
        self, *, wallet_address: str, payment_txid: str
    ) -> StoredLookupEvent | None:
        """The recorded lookup event for this wallet+payment_txid, or None if there isn't one."""
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import KycStmts

        session = get_cassandra_session()
        rows = session.execute(
            KycStmts.GET_LOOKUP_EVENTS_FOR_WALLET,
            (wallet_address, _MAX_LOOKUP_EVENTS_SCANNED),
        )
        for row in rows:
            if row.payment_txid == payment_txid:
                return StoredLookupEvent(
                    wallet_address=row.wallet_address,
                    created_at=row.created_at,
                    payer_address=row.payer_address,
                    payment_txid=row.payment_txid,
                    found=row.found,
                    payout_status=row.payout_status,
                    payout_txid=row.payout_txid,
                    payout_error=row.payout_error,
                )
        return None

    def update_lookup_event_payout(
        self,
        *,
        wallet_address: str,
        created_at: object,
        payout_status: str,
        payout_txid: str | None,
        payout_error: str | None,
    ) -> None:
        """Update one lookup event's payout outcome in place (after a payout retry)."""
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import KycStmts

        session = get_cassandra_session()
        session.execute(
            KycStmts.UPDATE_LOOKUP_EVENT_PAYOUT,
            (payout_status, payout_txid, payout_error, wallet_address, created_at),
        )
