"""Match an indexed transaction to a registered service by address/app id."""

from __future__ import annotations

import json

from app.modules.chain.payment import payment_details_from_txn_json
from app.modules.registry.models import ChainTransaction, ServiceEntry

MATCH_ADDRESS = "address"
MATCH_APP_ID = "app_id"
MATCH_ASSET_ID = "asset_id"


def _app_id_from_txn_json(txn_json: str | None) -> int | None:
    if not txn_json:
        return None
    try:
        data = json.loads(txn_json)
    except json.JSONDecodeError:
        return None
    txn = data.get("txn") if isinstance(data, dict) else None
    if not isinstance(txn, dict):
        return None
    txn_type = str(txn.get("type", "")).lower()
    if txn_type not in {"appl", "axfer"}:
        return None
    if txn_type == "appl":
        raw = txn.get("apid") if txn.get("apid") is not None else txn.get("app-index")
    else:
        raw = txn.get("xaid") if txn.get("xaid") is not None else txn.get("asset-index")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _addresses_for_txn(tx: ChainTransaction) -> set[str]:
    addresses = {tx.sender}
    if tx.receiver:
        addresses.add(tx.receiver)
    payment = payment_details_from_txn_json(tx.txn_json)
    if payment:
        addresses.add(payment[0])
    return addresses


def match_services_for_transaction(
    tx: ChainTransaction,
    registry: list[ServiceEntry],
) -> list[ServiceEntry]:
    """Return enabled registry entries that match this transaction."""
    if not registry:
        return []

    addresses = _addresses_for_txn(tx)
    app_id = _app_id_from_txn_json(tx.txn_json)
    matched: list[ServiceEntry] = []

    for entry in registry:
        if not entry.enabled:
            continue
        kind = entry.match_kind.strip().lower()
        value = entry.match_value.strip()
        if not value:
            continue
        if (
            (kind == MATCH_ADDRESS and value in addresses)
            or (
                kind == MATCH_APP_ID
                and app_id is not None
                and value == str(app_id)
                and tx.txn_type.lower() == "appl"
            )
            or (
                kind == MATCH_ASSET_ID
                and app_id is not None
                and value == str(app_id)
                and tx.txn_type.lower() == "axfer"
            )
        ):
            matched.append(entry)

    return matched
