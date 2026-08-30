"""Shared x402 resource server + facilitator client, built from settings.

Kept process-wide and initialized lazily (first use, not import time): building
it calls the facilitator's `/supported` endpoint over the network, and this
module must stay import-safe even when X402_ENABLED=false (main.py always
imports consumer modules, it only conditionally registers their routes).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal

from x402.extensions.bazaar import bazaar_resource_server_extension
from x402.http.facilitator_client import HTTPFacilitatorClientSync
from x402.http.facilitator_client_base import FacilitatorConfig
from x402.http.types import PaymentOption
from x402.mechanisms.avm.constants import DEFAULT_DECIMALS
from x402.mechanisms.avm.exact.server import ExactAvmScheme
from x402.mechanisms.avm.utils import (
    get_usdc_asa_id,
    parse_money_to_decimal,
    to_atomic_amount,
)
from x402.schemas import AssetAmount
from x402.server import x402ResourceServerSync

from app.core.config import settings
from app.modules.x402.assets import ACCEPTED_ASSETS
from app.modules.x402.price_oracle import get_usd_rate

_lock = threading.Lock()
_server: x402ResourceServerSync | None = None

# Required by the Algorand Global x402 Challenge submission checklist: every
# PaymentRequirements the facilitator sees must carry extra.tag == this value,
# or the endpoint doesn't qualify for the leaderboard.
#
# The contest's own submission guide says to "add a field named 'tag' to your
# extra field in your resource server x402 config" (i.e. PaymentOption.extra).
# That does NOT work against the installed x402-avm==2.0.2: reading
# x402/server_base.py shows PaymentRequirements.extra is built ONLY from
# AssetAmount.extra (`extra=asset_amount.extra or {}`) — PaymentOption.extra
# is never read anywhere in that path. So the tag is set on the AssetAmount of
# every asset build_payment_offer emits, which is what puts it on all of them
# rather than only on the first.
CHALLENGE_TAG = "x402-global-challenge"


def _tagged_money_parser(amount: float, network: str) -> AssetAmount:
    """Same USDC conversion as the package's own default money parser (ExactAvmScheme._default_money_conversion), plus CHALLENGE_TAG in extra.

    Still registered, but no longer the path a normal request takes:
    build_payment_offer below hands the scheme an explicit AssetAmount per
    asset, which parse_price returns untouched. This remains the fallback for
    any price that reaches the scheme as a bare Money string, so the tag is
    attached on that path too rather than only on the one we build by hand.
    """
    return AssetAmount(
        amount=str(to_atomic_amount(amount, DEFAULT_DECIMALS)),
        asset=str(get_usdc_asa_id(network)),
        extra={"decimals": DEFAULT_DECIMALS, "tag": CHALLENGE_TAG},
    )


def _usd_amount(price: str) -> Decimal:
    """The Money string's dollar value as an exact Decimal.

    Routed through the package's own parser so the "$"/comma handling has a
    single owner, then re-widened via str() exactly the way to_atomic_amount
    does internally — which is what makes the USDC amount computed here
    bit-identical to what the money parser produced before multi-asset.
    """
    return Decimal(str(parse_money_to_decimal(price)))


def atomic_amount(usd: Decimal, usd_per_unit: Decimal, decimals: int) -> str:
    """Convert a USD price into atomic units of an asset worth `usd_per_unit` dollars.

        units      = usd / usd_per_unit
        atomic     = ceil(units * 10**decimals)

    written as `usd * 10**decimals / usd_per_unit` so the scaling happens
    before the division and only one rounding step is taken.

    Dividing (not multiplying) by the rate is the whole correctness question
    here: a cheaper asset must cost MORE units, so $0.10 at $1.12/EURQ is
    0.0893 EURQ, not 0.112. Rounding is ROUND_CEILING and the result is floored
    at 1 atomic unit, so a sub-unit price can never round down to a free call.
    """
    scaled = usd * (Decimal(10) ** decimals) / usd_per_unit
    return str(max(int(scaled.to_integral_value(rounding=ROUND_CEILING)), 1))


@dataclass(frozen=True)
class PaymentOffer:
    """The payment options for one route, plus the symbols they correspond to.

    The symbols ride along because the human-readable preference note has to
    name exactly the assets that made it into `options` — deriving them by
    mapping ASA ids back to symbols would be a second, drift-prone copy of the
    selection logic.
    """

    options: list[PaymentOption]
    symbols: list[str]

    def preference_note(self) -> str | None:
        """Say which asset is preferred, or None when only one asset is offered.

        Reads as "(USDC preferred; EURQ and USDQ also accepted.)". None rather
        than a one-asset note because on TestNet (where EURQ and USDQ do not
        exist) claiming they are accepted would simply be false.
        """
        if len(self.symbols) < 2:
            return None
        preferred, *rest = self.symbols
        if len(rest) == 1:
            others = rest[0]
        elif len(rest) == 2:
            others = f"{rest[0]} and {rest[1]}"
        else:
            others = f"{', '.join(rest[:-1])}, and {rest[-1]}"
        return f"({preferred} preferred; {others} also accepted.)"


def build_payment_offer(price: str) -> PaymentOffer:
    """Every asset this marketplace will accept `price` in, USDC first.

    An asset is offered only if it exists on the configured network AND its USD
    rate is known (USDC needs no rate — it is the unit of account). Anything
    else is silently omitted rather than quoted at a quantity we cannot stand
    behind; see price_oracle for the rate-failure ladder.

    Order is load-bearing, not cosmetic: the package's default client selector
    is `return requirements[0]` (x402/client_base.py:99-104), so listing USDC
    first is what actually makes an agent prefer it.
    """
    network = settings.x402_network
    usd = _usd_amount(price)

    options: list[PaymentOption] = []
    symbols: list[str] = []
    for asset in ACCEPTED_ASSETS:
        asa_id = asset.asa_id_for(network)
        if asa_id is None:
            continue
        if asset.coingecko_id is None:
            usd_per_unit = Decimal(1)
        else:
            rate = get_usd_rate(asset.coingecko_id)
            if rate is None:
                continue
            usd_per_unit = rate
        options.append(
            PaymentOption(
                scheme="exact",
                pay_to=settings.x402_pay_to_address,
                # An explicit AssetAmount, not a Money string: the money-parser
                # extension point is shaped "one dollar amount -> one asset",
                # and this needs N assets out of one dollar amount. parse_price
                # returns an AssetAmount untouched, and its .extra is the only
                # thing that reaches PaymentRequirements.extra (server_base.py:
                # 318-328) — which is why the challenge tag is set per asset.
                price=AssetAmount(
                    amount=atomic_amount(usd, usd_per_unit, asset.decimals),
                    asset=str(asa_id),
                    extra={"decimals": asset.decimals, "tag": CHALLENGE_TAG},
                ),
                network=network,
            )
        )
        symbols.append(asset.symbol)

    if not options:
        # Nothing resolved — an unrecognised network, most likely. Fall back to
        # the pre-multi-asset single option so a misconfiguration degrades to
        # exactly the old behaviour instead of an empty, unpayable offer.
        return PaymentOffer(
            options=[
                PaymentOption(
                    scheme="exact",
                    pay_to=settings.x402_pay_to_address,
                    price=price,
                    network=network,
                )
            ],
            symbols=[],
        )
    return PaymentOffer(options=options, symbols=symbols)


def register_tagged_exact_avm_scheme(
    server: x402ResourceServerSync, networks: str | list[str]
) -> None:
    """Same effect as x402.mechanisms.avm.exact.register.register_exact_avm_server, plus the challenge-tag money parser. Used by both the real resource server below and by tests, so the tag-injection mechanism lives in exactly one place."""
    scheme = ExactAvmScheme()
    scheme.register_money_parser(_tagged_money_parser)
    for network in [networks] if isinstance(networks, str) else networks:
        server.register(network, scheme)


def get_resource_server() -> x402ResourceServerSync:
    """The shared, initialized x402ResourceServerSync (built once per process)."""
    global _server
    if _server is not None:
        return _server
    with _lock:
        if _server is None:
            facilitator = HTTPFacilitatorClientSync(
                FacilitatorConfig(url=settings.x402_facilitator_url)
            )
            server = x402ResourceServerSync(facilitator)
            register_tagged_exact_avm_scheme(server, settings.x402_network)
            # Bazaar discovery: required for the endpoint to be auto-catalogued
            # once a real payment settles (see guard.py's `extensions` param,
            # which is what actually declares each route discoverable — this
            # registration just auto-enriches the declaration with the HTTP
            # method, matching what the upstream Flask/FastAPI middleware does).
            server.register_extension(bazaar_resource_server_extension)
            server.initialize()  # fetches facilitator /supported once
            _server = server
        return _server
