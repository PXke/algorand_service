"""Direct tests for the shared accepted-asset helpers in modules/x402/assets.py."""

from __future__ import annotations

import pytest

pytest.importorskip("x402")

from x402.mechanisms.avm.constants import (
    ALGORAND_MAINNET_CAIP2,
    ALGORAND_TESTNET_CAIP2,
    USDC_MAINNET_ASA_ID,
    USDC_TESTNET_ASA_ID,
)

from app.modules.x402.assets import EURQ, USDC, USDQ, asset_for_asa_id


def test_asset_for_asa_id_resolves_usdc_on_both_networks() -> None:
    """USDC's package-provided ASA ids resolve to USDC on their own network."""
    assert asset_for_asa_id(str(USDC_TESTNET_ASA_ID), ALGORAND_TESTNET_CAIP2) is USDC
    assert asset_for_asa_id(str(USDC_MAINNET_ASA_ID), ALGORAND_MAINNET_CAIP2) is USDC


def test_asset_for_asa_id_resolves_quantoz_assets_on_mainnet_only() -> None:
    """EURQ/USDQ resolve on MainNet and are None on TestNet, where they do not exist."""
    assert asset_for_asa_id("2768422954", ALGORAND_MAINNET_CAIP2) is EURQ
    assert asset_for_asa_id("2768603795", ALGORAND_MAINNET_CAIP2) is USDQ
    # No TestNet deployment: the mainnet id names nothing there.
    assert asset_for_asa_id("2768422954", ALGORAND_TESTNET_CAIP2) is None
    assert asset_for_asa_id("2768603795", ALGORAND_TESTNET_CAIP2) is None


def test_asset_for_asa_id_is_network_scoped() -> None:
    """An id is only an accepted asset on the network it was issued on."""
    # The TestNet USDC id is not USDC on MainNet, and vice versa.
    assert asset_for_asa_id(str(USDC_TESTNET_ASA_ID), ALGORAND_MAINNET_CAIP2) is None
    assert asset_for_asa_id(str(USDC_MAINNET_ASA_ID), ALGORAND_TESTNET_CAIP2) is None


def test_asset_for_asa_id_tolerates_surrounding_whitespace() -> None:
    """Whitespace around the id is stripped before parsing."""
    assert asset_for_asa_id(f"  {USDC_TESTNET_ASA_ID} \n", ALGORAND_TESTNET_CAIP2) is USDC


@pytest.mark.parametrize("bad", [None, "", "   ", "not-an-asa-id", "12.5", "999999999"])
def test_asset_for_asa_id_unparseable_or_unknown_is_none(bad: str | None) -> None:
    """Unparseable and unknown ids both yield None rather than raising."""
    assert asset_for_asa_id(bad, ALGORAND_TESTNET_CAIP2) is None
    assert asset_for_asa_id(bad, ALGORAND_MAINNET_CAIP2) is None


def test_asset_for_asa_id_unknown_network_is_none() -> None:
    """A network the mechanism does not recognise yields None, not an error."""
    assert asset_for_asa_id(str(USDC_MAINNET_ASA_ID), "algorand:not-a-real-network") is None
