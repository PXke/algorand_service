"""Cassandra-backed KYC enrollment and lookup-event storage."""

from __future__ import annotations

from datetime import UTC, datetime

from app.modules.kyc.models.domain import StoredEnrollment


class CassandraEnrollmentStore:
    """Cassandra-backed KYC enrollment storage."""
    def upsert(self, item: StoredEnrollment) -> None:
        """Insert or update a KYC enrollment record."""
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
