"""Parse payment details out of a signed transaction's JSON."""

from __future__ import annotations

import json


def payment_details_from_txn_json(txn_json: str | None) -> tuple[str, int] | None:
    """Parse receiver and amount (microAlgos) from Conduit `txn_json` for payment txns."""
    if not txn_json:
        return None
    try:
        data = json.loads(txn_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    txn = data.get("txn")
    if not isinstance(txn, dict) and "type" in data:
        txn = data
    if not isinstance(txn, dict):
        return None

    txn_type = str(txn.get("type", "")).lower()
    if txn_type != "pay":
        return None

    receiver = txn.get("rcv") or txn.get("receiver")
    amount = txn.get("amt") if txn.get("amt") is not None else txn.get("amount")
    if not isinstance(receiver, str) or not receiver:
        return None
    try:
        return receiver, int(amount or 0)
    except (TypeError, ValueError):
        return None
