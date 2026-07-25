"""Shared x402 resource server + facilitator client, built from settings.

Kept process-wide and initialized lazily (first use, not import time): building
it calls the facilitator's `/supported` endpoint over the network, and this
module must stay import-safe even when X402_ENABLED=false (main.py always
imports consumer modules, it only conditionally registers their routes).
"""

from __future__ import annotations

import threading

from x402.extensions.bazaar import bazaar_resource_server_extension
from x402.http.facilitator_client import HTTPFacilitatorClientSync
from x402.http.facilitator_client_base import FacilitatorConfig
from x402.mechanisms.avm.constants import DEFAULT_DECIMALS
from x402.mechanisms.avm.exact.server import ExactAvmScheme
from x402.mechanisms.avm.utils import get_usdc_asa_id, to_atomic_amount
from x402.schemas import AssetAmount
from x402.server import x402ResourceServerSync

from app.core.config import settings

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
# is never read anywhere in that path. The only place the tag actually reaches
# the response is through the price parser, so it's injected via
# ExactAvmScheme.register_money_parser — the package's own sanctioned
# extension point for exactly this kind of "customize the built
# AssetAmount/PaymentRequirements" need.
CHALLENGE_TAG = "x402-global-challenge"


def _tagged_money_parser(amount: float, network: str) -> AssetAmount:
    """Same USDC conversion as the package's own default money parser (ExactAvmScheme._default_money_conversion), plus CHALLENGE_TAG in extra."""
    return AssetAmount(
        amount=str(to_atomic_amount(amount, DEFAULT_DECIMALS)),
        asset=str(get_usdc_asa_id(network)),
        extra={"decimals": DEFAULT_DECIMALS, "tag": CHALLENGE_TAG},
    )


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
