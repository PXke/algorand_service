from __future__ import annotations

import json
from unittest.mock import patch

from app.modules.chain_tail.chain_reader import RoundTransaction
from app.modules.chain_tail.discovery import extract_urls_from_tx, enqueue_discovered_urls


def test_extract_urls_from_note() -> None:
    note = b"Visit https://allo.info for details"
    txn_json = json.dumps({"note": note.decode()})
    tx = RoundTransaction(
        txid="TX1",
        round=1,
        sender="ADDR",
        txn_type="pay",
        txn_json=txn_json,
    )
    urls = extract_urls_from_tx(tx)
    assert "https://allo.info" in urls


def test_extract_urls_from_rekey_hint() -> None:
    txn_json = json.dumps({"txn": {"type": "pay", "rekey-to": "service-proxy-mainnet"}})
    tx = RoundTransaction(
        txid="TX2",
        round=2,
        sender="ADDR",
        txn_type="pay",
        txn_json=txn_json,
    )
    urls = extract_urls_from_tx(tx)
    assert "https://service-proxy.com" in urls


@patch("app.modules.crawler.url_queue.enqueue_url")
@patch("app.core.config.DISCOVERY_MODE_ENABLED", True)
def test_enqueue_discovered_urls(mock_enqueue) -> None:
    mock_enqueue.return_value = ("id", True)
    txn_json = json.dumps({"note": "https://perawallet.app"})
    tx = RoundTransaction(
        txid="TX3",
        round=3,
        sender="ADDR",
        txn_type="pay",
        txn_json=txn_json,
    )
    count = enqueue_discovered_urls(tx)
    assert count == 1
    mock_enqueue.assert_called()
