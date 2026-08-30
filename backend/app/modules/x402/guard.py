"""Per-route payment guard.

This backend has no per-route middleware chain (see require_admin_wallet in
app/modules/admin/auth.py) — auth/payment gating is a guard function called
first in the Falcon handler. require_payment follows that same shape: call it,
return its .error Response immediately if set, otherwise proceed with the
now-paid-for work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from x402.http.types import (
    HTTPRequestContext,
    HTTPResponseInstructions,
    RouteConfig,
)
from x402.http.x402_http_server import x402HTTPResourceServerSync

from app.core import serialization
from app.core.http import Request, Response
from app.modules.x402.adapter import PlatformHTTPAdapter
from app.modules.x402.client import build_payment_offer, get_resource_server

# The contest's required "x402-global-challenge" tag is set per asset on each
# AssetAmount.extra in client.py, NOT here — PaymentOption.extra is never read
# by the installed package when building the response, which takes extra from
# AssetAmount alone (verified against x402/server_base.py:318-328). See
# client.py's CHALLENGE_TAG and build_payment_offer for the actual mechanism.


@dataclass
class PaymentResult:
    """Outcome of require_payment.

    `error` is None only once the payment has been both verified AND settled
    — settlement is a second, separate facilitator call (x402 does not settle
    automatically after a successful verify).
    """

    error: Response | None
    payer: str | None = None
    settlement_headers: dict[str, str] = field(default_factory=dict)
    # The settled amount, atomic units as a string (e.g. USDC has 6 decimals)
    # — None unless error is None. Lets a caller like the KYC lookup endpoint
    # compute a payout split without re-deriving the price itself.
    amount_atomic: str | None = None
    # The incoming payment's own txid, for audit trails that need to link a
    # side effect (e.g. a payout) back to the payment that funded it.
    payment_txid: str | None = None
    # What was actually paid, taken from the matched PaymentRequirements rather
    # than re-derived from settings: the settlement ledger records the asset the
    # payer really used, which stops being "USDC on the configured network" the
    # moment a second asset is accepted. Both None unless error is None.
    asset_id: str | None = None
    network: str | None = None


def _describe(description: str | None, note: str | None) -> str | None:
    """Join a route's own description with the accepted-assets note.

    Either half may be absent: a single-asset offer has no note, and some
    routes pass no description. Only the parts that exist are joined, so no
    route ends up with a stray separator or a dangling note.
    """
    parts = [part for part in (description, note) if part]
    return " ".join(parts) if parts else None


def _instructions_to_response(instr: HTTPResponseInstructions) -> Response:
    if instr.is_html:
        body = instr.body if isinstance(instr.body, str) else ""
    else:
        body = serialization.dumps(instr.body if instr.body is not None else {})
    return Response(status_code=instr.status, headers=instr.headers, description=body)


def require_payment(
    request: Request,
    *,
    price: str,
    resource: str,
    description: str | None = None,
    extensions: dict[str, Any] | None = None,
) -> PaymentResult:
    """Gate a Falcon handler behind an x402 payment.

    `price` is a Money string, e.g. "$0.01" — the base price, always in US
    dollars. It is offered in every asset this marketplace accepts (USDC first,
    then any other asset available on the configured network with a known USD
    rate), each carrying the required challenge tag. See
    client.build_payment_offer; callers need no say in this and get multi-asset
    automatically.
    `resource` is a short stable id for this endpoint, shown to the payer.
    `description` is free text reaching the payer as the 402's
    resource.description, before they commit — the place to state anything the
    price alone doesn't say (a term length, what the fee buys). The
    which-assets-we-take note is appended here rather than at each call site,
    so no route can forget it or word it differently.
    `extensions` sets RouteConfig.extensions — pass
    `x402.extensions.bazaar.declare_discovery_extension(...)` here to make a
    route Bazaar-discoverable (required for the leaderboard, not automatic).
    """
    offer = build_payment_offer(price)
    route_config = RouteConfig(
        accepts=offer.options,
        resource=resource,
        description=_describe(description, offer.preference_note()),
        extensions=extensions,
    )
    # A fresh wrapper per call (route compilation is cheap, local regex work)
    # around the process-wide, already-initialized resource server — this
    # avoids re-hitting the facilitator's /supported endpoint per request.
    http_server = x402HTTPResourceServerSync(get_resource_server(), route_config)
    context = HTTPRequestContext(
        adapter=PlatformHTTPAdapter(request),
        path=request.url.path,
        method=request.method,
    )
    result = http_server.process_http_request(context)

    if result.type == "payment-error":
        return PaymentResult(error=_instructions_to_response(result.response))

    if result.type == "no-payment-required":
        # Every call here configures exactly one payment-required route, so
        # this shouldn't happen — fail closed rather than ever serve for free.
        return PaymentResult(
            error=Response(
                status_code=500,
                headers={"Content-Type": "application/json"},
                description=serialization.dumps(
                    {"error": {"code": "x402_misconfigured", "message": "No route matched"}}
                ),
            )
        )

    settle = http_server.process_settlement(result.payment_payload, result.payment_requirements)
    if not settle.success:
        return PaymentResult(
            error=Response(
                status_code=402,
                headers={"Content-Type": "application/json"},
                description=serialization.dumps(
                    {
                        "error": {
                            "code": "settlement_failed",
                            "message": settle.error_reason or "Settlement failed",
                        }
                    }
                ),
            )
        )

    return PaymentResult(
        error=None,
        payer=settle.payer,
        settlement_headers=settle.headers,
        amount_atomic=result.payment_requirements.amount,
        payment_txid=settle.transaction,
        asset_id=result.payment_requirements.asset,
        network=result.payment_requirements.network,
    )
