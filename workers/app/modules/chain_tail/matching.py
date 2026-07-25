"""Match an on-chain transaction to a registered service."""

from __future__ import annotations

import json

from app.modules.chain_tail.chain_reader import RoundTransaction
from app.modules.chain_tail.registry_cache import ServiceEntry


def _payment_receiver(txn_json: str | None) -> str | None:
    if not txn_json:
        return None
    try:
        data = json.loads(txn_json)
    except json.JSONDecodeError:
        return None
    txn = data.get("txn") if isinstance(data, dict) else None
    if not isinstance(txn, dict) or str(txn.get("type", "")).lower() != "pay":
        return None
    receiver = txn.get("rcv") or txn.get("receiver")
    return receiver if isinstance(receiver, str) and receiver else None


def _asset_or_app_id(txn_json: str | None) -> int | None:
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
    if txn_type == "appl":
        raw = txn.get("apid") if txn.get("apid") is not None else txn.get("app-index")
    elif txn_type == "axfer":
        raw = txn.get("xaid") if txn.get("xaid") is not None else txn.get("asset-index")
    else:
        return None
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def match_services(
    tx: RoundTransaction,
    registry: tuple[ServiceEntry, ...],
    *,
    enqueue_discovery: bool = True,
) -> list[ServiceEntry]:
    """Return registry entries whose address/app-id/asset-id matches this transaction."""
    addresses = {tx.sender}
    if tx.receiver:
        addresses.add(tx.receiver)
    recv = _payment_receiver(tx.txn_json)
    if recv:
        addresses.add(recv)

    app_or_asset = _asset_or_app_id(tx.txn_json)
    txn_type = tx.txn_type.lower()
    matched: list[ServiceEntry] = []

    if enqueue_discovery:
        from app.modules.chain_tail.discovery import enqueue_discovered_urls

        enqueue_discovered_urls(tx)

    for entry in registry:
        kind = entry.match_kind.strip().lower()
        value = entry.match_value.strip()
        if not value:
            continue
        if (
            (kind == "address" and value in addresses)
            or (
                kind == "app_id"
                and app_or_asset is not None
                and value == str(app_or_asset)
                and txn_type == "appl"
            )
            or (
                kind == "asset_id"
                and app_or_asset is not None
                and value == str(app_or_asset)
                and txn_type == "axfer"
            )
        ):
            matched.append(entry)
    return matched
