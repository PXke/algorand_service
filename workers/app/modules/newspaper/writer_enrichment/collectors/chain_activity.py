from __future__ import annotations

from typing import Any


def collect_chain_context(
    *,
    service_id: str,
    match_kind: str,
    match_value: str,
) -> dict[str, Any]:
    """
    On-chain usage signals from registry match + conduit index (phase 2+).

    Today: expose match metadata so the writer knows what we watch for.
    Future: tx count, unique senders, ASA volume, fame percentile vs network.
    """
    return {
        "match_kind": match_kind,
        "match_value": match_value,
        "tx_stats": "not_implemented",
        "note": "Chain tail enqueues publishes when txs match registry; stats TBD.",
    }
