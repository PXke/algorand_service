"""The assets this marketplace accepts payment in, and their per-network ASA ids.

Order matters. ACCEPTED_ASSETS is the order the 402 offer lists options in, and
the x402 client package's `default_payment_selector` is literally
`return requirements[0]` (x402/client_base.py:99-104) — so a well-behaved agent
pays in whichever asset we list first. USDC is first because that is the
owner's stated preference; the rest are genuinely accepted, not decorative.

Why ALGO is absent
------------------
The installed x402-avm==2.0.2 AVM `exact` mechanism can only express an ASA
transfer, so a native-ALGO payment cannot be offered through it:

* the client builds `AssetTransferTxn(index=int(requirements.asset))` and has no
  `pay`-transaction branch (x402/mechanisms/avm/exact/client.py:162-169);
* the facilitator rejects any payment leg that is not an axfer —
  `if payment_txn.type != TXN_TYPE_ASSET_TRANSFER` — and then requires
  `asset_index == int(requirements.asset)`
  (x402/mechanisms/avm/exact/facilitator.py:225-238).

There is no native-currency sentinel: `AssetAmount.asset` is a required `str`
(x402/schemas/base.py:42-44), and `parse_price` silently rewrites a falsy asset
to the USDC ASA id (x402/mechanisms/avm/exact/server.py:84-87). An ALGO option
would therefore not fail loudly — it would advertise an ALGO-denominated amount
labelled as USDC and overcharge by the ALGO price. Adding ALGO needs mechanism
support first; when that lands it is one more entry in ACCEPTED_ASSETS.

EURQ and USDQ have no TestNet ASA, so they are mainnet-only by omission rather
than by a special case: `asa_id_for` returns None for a network an asset does
not exist on, and the offer builder skips it.
"""

from __future__ import annotations

from dataclasses import dataclass

from x402.mechanisms.avm.constants import (
    ALGORAND_MAINNET_CAIP2,
    ALGORAND_TESTNET_CAIP2,
    USDC_MAINNET_ASA_ID,
    USDC_TESTNET_ASA_ID,
)
from x402.mechanisms.avm.utils import normalize_network


@dataclass(frozen=True)
class AcceptedAsset:
    """One asset the payment gate will accept, and where it exists on-chain."""

    symbol: str
    decimals: int
    # CoinGecko id used to price one unit of this asset in USD. None means the
    # asset IS the unit of account (USDC), so it needs no oracle and is always
    # offerable — see price_oracle.get_usd_rate.
    coingecko_id: str | None
    # CAIP-2 network -> ASA id. A network absent from this map means the asset
    # does not exist there and must not be offered on it.
    asa_ids: dict[str, int]

    def asa_id_for(self, network: str) -> int | None:
        """The ASA id for this asset on `network`, or None if it has none there.

        A network the mechanism does not recognise also yields None rather than
        raising: "this asset is not payable here" is the same answer either way,
        and the offer builder's own fallback decides what to do about it.
        """
        try:
            caip2 = normalize_network(network)
        except ValueError:
            return None
        return self.asa_ids.get(caip2)


# ASA ids for USDC come from the package rather than being retyped here, so the
# gate and the mechanism can never disagree about which ASA is USDC.
USDC = AcceptedAsset(
    symbol="USDC",
    decimals=6,
    coingecko_id=None,
    asa_ids={
        ALGORAND_MAINNET_CAIP2: USDC_MAINNET_ASA_ID,
        ALGORAND_TESTNET_CAIP2: USDC_TESTNET_ASA_ID,
    },
)

# Quantoz EURQ / USDQ, both 6 decimals, both verified against the live mainnet
# indexer. Neither has a TestNet deployment, hence the mainnet-only maps.
EURQ = AcceptedAsset(
    symbol="EURQ",
    decimals=6,
    coingecko_id="quantoz-eurq",
    asa_ids={ALGORAND_MAINNET_CAIP2: 2768422954},
)

USDQ = AcceptedAsset(
    symbol="USDQ",
    decimals=6,
    coingecko_id="quantoz-usdq",
    asa_ids={ALGORAND_MAINNET_CAIP2: 2768603795},
)

# USDC first: this tuple's order is the 402 offer's order, which is what makes
# USDC the preferred asset rather than merely a documented one.
ACCEPTED_ASSETS: tuple[AcceptedAsset, ...] = (USDC, EURQ, USDQ)
