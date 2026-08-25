"""Prepared CQL shared between backend and workers for Conduit-indexed chain data.

Both deployables read the SAME physical Cassandra tables (`conduit_meta`,
`transactions_by_round`) that Conduit itself writes, via independently
hand-maintained statement classes in each service's own `app/core/statements.py`.
A diff of both files found `TXNS_BY_ROUND` had already drifted: backend's copy
selected `intra`, workers' did not, with no comment marking that as
intentional. Backend's `row_to_indexed_transaction` reads `row.intra`; workers'
`list_transactions_for_round` never reads `intra` at all (it builds its own
`RoundTransaction` dataclass field-by-field via `getattr`, and `intra` isn't
one of its fields). So the safe shared shape is backend's fuller one -- an
extra selected column costs nothing, and workers' caller already ignores
columns it doesn't ask for by name.

Every statement here is CQL text either byte-identical between both services
(`CHAIN_CONDUIT_HEAD`) or unified onto the wider of two accidentally-diverged
copies (`CHAIN_TXNS_BY_ROUND`, see above). Each local `statements.py` now
assigns its class attribute from one of these constants instead of defining
its own copy.

Names are flat module-level constants, NOT nested in a class, for the same
reason as `article_statements.py` (see that module's docstring for the full
explanation): `_Stmt` is a data descriptor, and only module-level attribute
access skips the descriptor protocol needed to keep preparation lazy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cassandra.query import PreparedStatement


class _Stmt:
    """Descriptor holding CQL; resolves to the (cached) PreparedStatement on access.

    Preparation is delegated to `app.core.cassandra.prepare_cached` -- resolved
    per-process, so this works identically whether accessed from backend or
    workers, each of which has its own `app.core.cassandra` module.
    """

    def __init__(self, cql: str) -> None:
        self.cql = cql

    def __get__(self, obj: object | None, owner: type | None) -> PreparedStatement:
        from app.core.cassandra import prepare_cached

        return prepare_cached(self.cql)


# --------------------------------------------------------------------------- #
# conduit_meta / transactions_by_round
# --------------------------------------------------------------------------- #
CHAIN_CONDUIT_HEAD = _Stmt("SELECT value FROM algorand_platform.conduit_meta WHERE id = ?")
CHAIN_TXNS_BY_ROUND = _Stmt(
    "SELECT txid, round, intra, sender, txn_type, txn_json, receiver, amount_microalgos "
    "FROM algorand_platform.transactions_by_round WHERE round = ?"
)
