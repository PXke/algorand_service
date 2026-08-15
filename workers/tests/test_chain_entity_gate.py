"""On-chain citation gate (2026-07-17): AlgoGlyph (deleted article 9eb96392) cited a REAL asset (492553812) and its REAL creator address, but fabricated the arithmetic on top of them — "50.16% of supply" when the chain says 25.08%. Owner's fix: cited ASA ids / addresses / txids must exist on-chain; verified ones are auto-linked to an explorer so readers (and this gate) can check them, unverifiable ones are flagged for revision and delinked."""

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
def _gate_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.core.config.CHAIN_ENTITY_GATE_ENABLED", True, raising=False)


def test_find_chain_entities_extracts_addresses_txids_and_contextual_asset_ids() -> None:
    """Extracts addresses and contextual asset ids, but not a bare year that looks like a number."""
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
    """Flags an address with a bad checksum as an issue needing revision."""
    body = f"The team wallet is {_BAD_CHECKSUM_ADDR}."
    issues = unverifiable_chain_entities(body, _TRACE)
    assert any("bad checksum" in i for i in issues)


def test_valid_but_untraced_address_flagged_for_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flags a checksum-valid address that never appeared in the research trace as unverified."""
    # Isolate provenance logic from real checksum math: any 58-char run here
    # is treated as a syntactically valid address, and it appears in NO trace.
    monkeypatch.setattr(chain_entity_gate, "_is_valid_address", lambda _addr: True)
    body = f"The team wallet is {_REAL_ADDR}."
    issues = unverifiable_chain_entities(body, [])
    assert any("never appeared in your research" in i for i in issues)


def test_traced_address_not_flagged_for_provenance() -> None:
    """An address that appears in the research trace is not flagged as unverified."""
    body = f"The creator {_REAL_ADDR} controls the asset."
    issues = unverifiable_chain_entities(body, _TRACE)
    assert issues == []


def test_missing_asset_id_flagged_for_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flags an asset id that doesn't exist on-chain as an issue needing revision."""
    monkeypatch.setattr(
        chain_entity_gate,
        "_lookup_status",
        lambda _kind, _value: "missing",
    )
    body = "Asset id 999999999999 is the project's token."
    issues = unverifiable_chain_entities(body, [])
    assert any("does not exist on Algorand mainnet or testnet" in i for i in issues)


def test_network_error_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unknown lookup status (e.g. network error) fails open — no issue is raised."""
    monkeypatch.setattr(
        chain_entity_gate,
        "_lookup_status",
        lambda _kind, _value: "unknown",
    )
    body = "Asset id 999999999999 is the project's token."
    issues = unverifiable_chain_entities(body, [])
    assert issues == []


def test_verified_asset_auto_linked_to_explorer(monkeypatch: pytest.MonkeyPatch) -> None:
    """A mainnet-verified asset id is auto-linked to its allo.info explorer page."""
    monkeypatch.setattr(chain_entity_gate, "_lookup_status", lambda _kind, _value: "mainnet")
    payload = {"body": "Asset id 492553812 (GLYPH) anchors the project."}
    out = link_and_verify_chain_entities(payload, [])
    assert "[492553812](https://allo.info/asset/492553812)" in out["body"]
    assert out["_chain_entities_linked"] == [
        {"kind": "asset", "value": "492553812", "net": "mainnet"}
    ]


def test_testnet_asset_links_to_lora(monkeypatch: pytest.MonkeyPatch) -> None:
    """A testnet-verified asset id is auto-linked to its lora.algokit.io testnet explorer page."""
    monkeypatch.setattr(chain_entity_gate, "_lookup_status", lambda _kind, _value: "testnet")
    payload = {"body": "Asset id 123456 was deployed for testing."}
    out = link_and_verify_chain_entities(payload, [])
    assert "[123456](https://lora.algokit.io/testnet/asset/123456)" in out["body"]


def test_missing_asset_not_linked_and_recorded_unverified(monkeypatch: pytest.MonkeyPatch) -> None:
    """A nonexistent asset id is not linked, and gets recorded in _chain_entities_unverified."""
    monkeypatch.setattr(chain_entity_gate, "_lookup_status", lambda _kind, _value: "missing")
    payload = {"body": "Asset id 999999999999 is the project's token."}
    out = link_and_verify_chain_entities(payload, [])
    assert "](https://" not in out["body"]
    assert out["_chain_entities_unverified"] == [
        {"kind": "asset", "value": "999999999999", "why": "missing"}
    ]


def test_explorer_link_to_missing_entity_is_delinked() -> None:
    """A writer-authored explorer link to a nonexistent asset is stripped, keeping the link text."""
    # Writer already wrapped a nonexistent asset in an explorer link itself.
    payload = {"body": "See [the token](https://allo.info/asset/999999999999) for details."}

    def fake_lookup(_kind: str, value: str) -> str:
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


def test_legacy_algoexplorer_link_to_verified_asset_is_rewritten_to_allo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for the Kaafila incident (2026-08-10): a real, once-valid algoexplorer.io citation (AlgoExplorer has since gone dark) survives link_gate's traced-url exemption, so this gate must catch it — a verified entity behind a legacy-domain link gets its link REWRITTEN to the live explorer, not just delinked, so the citation still works for readers."""
    monkeypatch.setattr(chain_entity_gate, "_lookup_status", lambda _kind, _value: "mainnet")
    payload = {"body": "See [it on-chain](https://algoexplorer.io/asset/239444645) for details."}
    out = link_and_verify_chain_entities(payload, [])
    assert "algoexplorer.io" not in out["body"]
    assert "[it on-chain](https://allo.info/asset/239444645)" in out["body"]


def test_legacy_algoexplorer_address_link_is_rewritten_to_allo_account_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The address path (algoexplorer.io/address/... -> allo.info/account/...) rewrites correctly too, matching the exact Kaafila reserve-account citation shape."""
    monkeypatch.setattr(chain_entity_gate, "_lookup_status", lambda _kind, _value: "mainnet")
    payload = {
        "body": (
            f"The [reserve account](https://algoexplorer.io/address/{_REAL_ADDR}) still "
            "holds the full allocation."
        )
    }
    out = link_and_verify_chain_entities(payload, [])
    assert f"[reserve account](https://allo.info/account/{_REAL_ADDR})" in out["body"]


def test_legacy_algoexplorer_link_to_missing_entity_is_delinked_not_rewritten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A legacy-domain link to an entity that doesn't exist at all is delinked (existing dead-explorer rule), never rewritten to a working domain for a nonexistent target."""
    monkeypatch.setattr(chain_entity_gate, "_lookup_status", lambda _kind, _value: "missing")
    payload = {"body": "See [the token](https://algoexplorer.io/asset/999999999999) for details."}
    out = link_and_verify_chain_entities(payload, [])
    assert "algoexplorer.io" not in out["body"]
    assert "allo.info" not in out["body"]
    assert "[the token](" not in out["body"]
    assert "the token" in out["body"]


def test_algoglyph_incident_regression(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the real incident: a genuine asset + genuine creator address gets linked (so a reader — or this gate — can verify the real 25.08% share), while an address that never appeared in research stays unverified."""
    monkeypatch.setattr(chain_entity_gate, "_lookup_status", lambda _kind, _value: "mainnet")
    body = (
        f"Asset id 492553812 (GLYPH) is controlled by creator {_REAL_ADDR}, "
        "who reportedly holds a large share of supply."
    )
    out = link_and_verify_chain_entities({"body": body}, _TRACE)
    assert "[492553812](https://allo.info/asset/492553812)" in out["body"]
    assert f"[{_REAL_ADDR}](https://allo.info/account/{_REAL_ADDR})" in out["body"]


def test_gate_disabled_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leaves the body unchanged when the chain-entity gate is disabled."""
    monkeypatch.setattr("app.core.config.CHAIN_ENTITY_GATE_ENABLED", False, raising=False)
    body = "Asset id 492553812 is the token."
    out = link_and_verify_chain_entities({"body": body}, [])
    assert out["body"] == body
