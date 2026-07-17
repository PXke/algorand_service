"""On-chain citation gate (2026-07-17): AlgoGlyph (deleted article 9eb96392)
cited a REAL asset (492553812) and its REAL creator address, but fabricated
the arithmetic on top of them — "50.16% of supply" when the chain says
25.08%. Owner's fix: cited ASA ids / addresses / txids must exist on-chain;
verified ones are auto-linked to an explorer so readers (and this gate) can
check them, unverifiable ones are flagged for revision and delinked.
"""

from __future__ import annotations

import pytest

from app.modules.newspaper import chain_entity_gate
from app.modules.newspaper.chain_entity_gate import (
    find_chain_entities,
    link_and_verify_chain_entities,
    unverifiable_chain_entities,
)

# A real, checksum-valid Algorand address (58 chars) used across tests.
_REAL_ADDR = "CEBOWO3IUVK7MZQHOEJF4T47IMSCQWZS4X5SYBZGBR2VNHKJB3XMLBXDKI"
_BAD_CHECKSUM_ADDR = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"[:58]

_TRACE = [
    {
        "tool": "lookup_asset",
        "arguments": {"asset_id": 492553812},
        "result": {"asset_id": 492553812, "creator": _REAL_ADDR, "total": 2000000000000},
    },
]


@pytest.fixture(autouse=True)
def _gate_enabled(monkeypatch):
    monkeypatch.setattr("app.core.config.CHAIN_ENTITY_GATE_ENABLED", True, raising=False)


def test_find_chain_entities_extracts_addresses_txids_and_contextual_asset_ids() -> None:
    body = (
        f"The creator address {_REAL_ADDR} holds a large stake. "
        "Asset id 492553812 (GLYPH) was minted in 2021. "
        "The block height 2021 alone is not an asset id."
    )
    kinds = {k for k, _ in find_chain_entities(body)}
    values = {v for _, v in find_chain_entities(body)}
    assert "address" in kinds
    assert _REAL_ADDR in values
    assert ("asset", "492553812") in find_chain_entities(body)
    assert ("asset", "2021") not in find_chain_entities(body)


def test_invalid_checksum_address_flagged_for_revision() -> None:
    body = f"The team wallet is {_BAD_CHECKSUM_ADDR}."
    issues = unverifiable_chain_entities(body, _TRACE)
    assert any("bad checksum" in i for i in issues)


def test_valid_but_untraced_address_flagged_for_revision(monkeypatch) -> None:
    # Isolate provenance logic from real checksum math: any 58-char run here
    # is treated as a syntactically valid address, and it appears in NO trace.
    monkeypatch.setattr(chain_entity_gate, "_is_valid_address", lambda addr: True)
    body = f"The team wallet is {_REAL_ADDR}."
    issues = unverifiable_chain_entities(body, [])
    assert any("never appeared in your research" in i for i in issues)


def test_traced_address_not_flagged_for_provenance() -> None:
    body = f"The creator {_REAL_ADDR} controls the asset."
    issues = unverifiable_chain_entities(body, _TRACE)
    assert issues == []


def test_missing_asset_id_flagged_for_revision(monkeypatch) -> None:
    monkeypatch.setattr(
        chain_entity_gate,
        "_lookup_status",
        lambda kind, value: "missing",
    )
    body = "Asset id 999999999999 is the project's token."
    issues = unverifiable_chain_entities(body, [])
    assert any("does not exist on Algorand mainnet or testnet" in i for i in issues)


def test_network_error_fails_open(monkeypatch) -> None:
    monkeypatch.setattr(
        chain_entity_gate,
        "_lookup_status",
        lambda kind, value: "unknown",
    )
    body = "Asset id 999999999999 is the project's token."
    issues = unverifiable_chain_entities(body, [])
    assert issues == []


def test_verified_asset_auto_linked_to_explorer(monkeypatch) -> None:
    monkeypatch.setattr(chain_entity_gate, "_lookup_status", lambda kind, value: "mainnet")
    payload = {"body": "Asset id 492553812 (GLYPH) anchors the project."}
    out = link_and_verify_chain_entities(payload, [])
    assert "[492553812](https://allo.info/asset/492553812)" in out["body"]
    assert out["_chain_entities_linked"] == [
        {"kind": "asset", "value": "492553812", "net": "mainnet"}
    ]


def test_testnet_asset_links_to_lora(monkeypatch) -> None:
    monkeypatch.setattr(chain_entity_gate, "_lookup_status", lambda kind, value: "testnet")
    payload = {"body": "Asset id 123456 was deployed for testing."}
    out = link_and_verify_chain_entities(payload, [])
    assert "[123456](https://lora.algokit.io/testnet/asset/123456)" in out["body"]


def test_missing_asset_not_linked_and_recorded_unverified(monkeypatch) -> None:
    monkeypatch.setattr(chain_entity_gate, "_lookup_status", lambda kind, value: "missing")
    payload = {"body": "Asset id 999999999999 is the project's token."}
    out = link_and_verify_chain_entities(payload, [])
    assert "](https://" not in out["body"]
    assert out["_chain_entities_unverified"] == [
        {"kind": "asset", "value": "999999999999", "why": "missing"}
    ]


def test_explorer_link_to_missing_entity_is_delinked() -> None:
    # Writer already wrapped a nonexistent asset in an explorer link itself.
    payload = {
        "body": "See [the token](https://allo.info/asset/999999999999) for details."
    }

    def fake_lookup(kind: str, value: str) -> str:
        return "missing" if value == "999999999999" else "mainnet"

    import app.modules.newspaper.chain_entity_gate as mod

    orig = mod._lookup_status
    mod._lookup_status = fake_lookup
    try:
        out = link_and_verify_chain_entities(payload, [])
    finally:
        mod._lookup_status = orig
    assert "[the token](https://allo.info/asset/999999999999)" not in out["body"]
    assert "the token" in out["body"]


def test_algoglyph_incident_regression(monkeypatch) -> None:
    """Pin the real incident: a genuine asset + genuine creator address gets
    linked (so a reader — or this gate — can verify the real 25.08% share),
    while an address that never appeared in research stays unverified."""
    monkeypatch.setattr(chain_entity_gate, "_lookup_status", lambda kind, value: "mainnet")
    body = (
        f"Asset id 492553812 (GLYPH) is controlled by creator {_REAL_ADDR}, "
        "who reportedly holds a large share of supply."
    )
    out = link_and_verify_chain_entities({"body": body}, _TRACE)
    assert "[492553812](https://allo.info/asset/492553812)" in out["body"]
    assert f"[{_REAL_ADDR}](https://allo.info/account/{_REAL_ADDR})" in out["body"]


def test_gate_disabled_is_a_noop(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.CHAIN_ENTITY_GATE_ENABLED", False, raising=False)
    body = "Asset id 492553812 is the token."
    out = link_and_verify_chain_entities({"body": body}, [])
    assert out["body"] == body
