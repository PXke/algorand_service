from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.modules.suggestions.models.domain import StoredSuggestion, SuggestionError


class CassandraSuggestionStore:
    def insert(self, item: StoredSuggestion) -> None:
        from app.core.cassandra import get_cassandra_session

        if self.has_submission_txid(item.submission_txid):
            raise SuggestionError("duplicate_txid", "submission_txid already used")

        session = get_cassandra_session()
        created_at = datetime.fromtimestamp(item.created_at_epoch, tz=UTC)
        session.execute(
            """
            INSERT INTO suggestions_by_status (
              status, created_at, suggestion_id, wallet_address, title, body, submission_txid
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                item.status,
                created_at,
                UUID(item.suggestion_id),
                item.wallet_address,
                item.title,
                item.body,
                item.submission_txid,
            ),
        )

    def list_open(self) -> list[StoredSuggestion]:
        from app.core.cassandra import get_cassandra_session

        session = get_cassandra_session()
        rows = session.execute(
            """
            SELECT suggestion_id, wallet_address, title, body, submission_txid, created_at
            FROM suggestions_by_status
            WHERE status = %s
            LIMIT 200
            """,
            ("open",),
        )
        items: list[StoredSuggestion] = []
        for row in rows:
            created_at = row.created_at
            epoch = int(created_at.timestamp()) if created_at else 0
            items.append(
                StoredSuggestion(
                    suggestion_id=str(row.suggestion_id),
                    wallet_address=row.wallet_address,
                    title=row.title,
                    body=row.body,
                    submission_txid=row.submission_txid,
                    status="open",
                    created_at_epoch=epoch,
                )
            )
        return items

    def get(self, suggestion_id: str) -> StoredSuggestion | None:
        from app.core.cassandra import get_cassandra_session

        session = get_cassandra_session()
        try:
            sid = UUID(suggestion_id)
        except ValueError:
            return None
        rows = session.execute(
            """
            SELECT suggestion_id, wallet_address, title, body, submission_txid, created_at, status
            FROM suggestions_by_status
            WHERE status = %s AND suggestion_id = %s ALLOW FILTERING
            """,
            ("open", sid),
        )
        row = rows.one()
        if row is None:
            return None
        created_at = row.created_at
        epoch = int(created_at.timestamp()) if created_at else 0
        return StoredSuggestion(
            suggestion_id=str(row.suggestion_id),
            wallet_address=row.wallet_address,
            title=row.title,
            body=row.body,
            submission_txid=row.submission_txid,
            status=row.status,
            created_at_epoch=epoch,
        )

    def has_submission_txid(self, submission_txid: str) -> bool:
        from app.core.cassandra import get_cassandra_session

        session = get_cassandra_session()
        row = session.execute(
            """
            SELECT suggestion_id FROM suggestions_by_status
            WHERE submission_txid = %s ALLOW FILTERING
            LIMIT 1
            """,
            (submission_txid,),
        ).one()
        return row is not None
