from __future__ import annotations

from app.modules.registry.matching import match_services_for_transaction
from app.modules.registry.models import ChainTransaction, ServiceEntry


def test_match_address_on_receiver() -> None:
    treasury = "T" * 58
    wallet = "W" * 58
    tx = ChainTransaction(
        txid="X" * 52,
        round=10,
        sender=wallet,
        txn_type="pay",
        receiver=treasury,
    )
    registry = [
        ServiceEntry(
            service_id="svc-treasury",
            display_name="Treasury",
            match_kind="address",
            match_value=treasury,
            scrape_url="https://example.com",
        )
    ]
    matched = match_services_for_transaction(tx, registry)
    assert len(matched) == 1
    assert matched[0].service_id == "svc-treasury"


def test_no_match_when_disabled() -> None:
    tx = ChainTransaction(txid="X" * 52, round=1, sender="W" * 58, txn_type="pay")
    registry = [
        ServiceEntry(
            service_id="off",
            display_name="Off",
            match_kind="address",
            match_value="W" * 58,
            scrape_url=None,
            enabled=False,
        )
    ]
    assert match_services_for_transaction(tx, registry) == []
