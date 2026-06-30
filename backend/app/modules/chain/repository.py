from __future__ import annotations

from typing import Protocol

from app.modules.chain.models import IndexedTransaction


class ChainRepository(Protocol):
    def get_transaction(self, txid: str) -> IndexedTransaction | None: ...

    def get_chain_head_round(self) -> int | None: ...

    def list_transactions_for_round(self, round: int) -> list[IndexedTransaction]: ...


def row_to_indexed_transaction(row: object | None) -> IndexedTransaction | None:
    if row is None:
        return None
    receiver = getattr(row, "receiver", None)
    amount = getattr(row, "amount_microalgos", None)
    return IndexedTransaction(
        txid=row.txid,
        round=int(row.round),
        intra=int(row.intra),
        sender=row.sender,
        txn_type=row.txn_type,
        txn_json=getattr(row, "txn_json", None),
        receiver=receiver if receiver else None,
        amount_microalgos=int(amount) if amount is not None else None,
    )


class CassandraChainRepository:
    """Reads on-chain rows written by the Conduit cassandra exporter."""

    def get_transaction(self, txid: str) -> IndexedTransaction | None:
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ChainStmts

        session = get_cassandra_session()
        row = session.execute(ChainStmts.GET_TXN, (txid,)).one()
        return row_to_indexed_transaction(row)

    def get_chain_head_round(self) -> int | None:
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ChainStmts

        session = get_cassandra_session()
        row = session.execute(ChainStmts.CONDUIT_HEAD, ("last_ingested_round",)).one()
        if row is None:
            return None
        return int(row.value)

    def list_transactions_for_round(self, round: int) -> list[IndexedTransaction]:
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ChainStmts

        session = get_cassandra_session()
        rows = session.execute(ChainStmts.TXNS_BY_ROUND, (round,))
        items: list[IndexedTransaction] = []
        for row in rows:
            tx = row_to_indexed_transaction(row)
            if tx is not None:
                items.append(tx)
        return items


class FakeChainRepository:
    """Test double with configurable transactions and head round."""

    def __init__(self) -> None:
        self.transactions: dict[str, IndexedTransaction] = {}
        self.by_round: dict[int, list[IndexedTransaction]] = {}
        self.chain_head_round: int | None = 1000

    def get_transaction(self, txid: str) -> IndexedTransaction | None:
        return self.transactions.get(txid)

    def get_chain_head_round(self) -> int | None:
        return self.chain_head_round

    def list_transactions_for_round(self, round: int) -> list[IndexedTransaction]:
        return list(self.by_round.get(round, []))


_chain_repository: ChainRepository | None = None


def get_chain_repository() -> ChainRepository:
    global _chain_repository
    if _chain_repository is None:
        _chain_repository = CassandraChainRepository()
    return _chain_repository


def set_chain_repository(repository: ChainRepository | None) -> None:
    global _chain_repository
    _chain_repository = repository
