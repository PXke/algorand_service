from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RoundTransaction:
    txid: str
    round: int
    sender: str
    txn_type: str
    receiver: str | None = None
    amount_microalgos: int | None = None
    txn_json: str | None = None


def get_conduit_head_round() -> int | None:
    from app.core.cassandra import get_cassandra_session

    session = get_cassandra_session()
    row = session.execute(
        "SELECT value FROM conduit_meta WHERE id = %s",
        ("last_ingested_round",),
    ).one()
    if row is None:
        return None
    return int(row.value)


def get_algod_head_round() -> int:
    """Live network head from algod (`/v2/status` → `last-round`).

    Unlike the Conduit-ingested head, this is always available as long as the
    node is reachable, so it is the fallback when Cassandra has no meta row yet.
    """
    import httpx

    from app.core.config import ALGOD_TOKEN, ALGOD_URL

    headers = {"X-Algo-API-Token": ALGOD_TOKEN} if ALGOD_TOKEN else {}
    with httpx.Client(timeout=20.0) as http:
        response = http.get(f"{ALGOD_URL}/v2/status", headers=headers)
        response.raise_for_status()
        return int(response.json()["last-round"])


def list_transactions_for_round(round_num: int) -> list[RoundTransaction]:
    from app.core.cassandra import get_cassandra_session

    session = get_cassandra_session()
    rows = session.execute(
        """
        SELECT txid, round, sender, txn_type, txn_json, receiver, amount_microalgos
        FROM transactions_by_round
        WHERE round = %s
        """,
        (round_num,),
    )
    items: list[RoundTransaction] = []
    for row in rows:
        items.append(
            RoundTransaction(
                txid=row.txid,
                round=int(row.round),
                sender=row.sender,
                txn_type=row.txn_type,
                receiver=getattr(row, "receiver", None) or None,
                amount_microalgos=(
                    int(row.amount_microalgos)
                    if getattr(row, "amount_microalgos", None) is not None
                    else None
                ),
                txn_json=getattr(row, "txn_json", None),
            )
        )
    return items
