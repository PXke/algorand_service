"""Per-route payment guard.

Robyn has no per-route middleware in this codebase (see require_admin_wallet
in app/modules/admin/auth.py) — auth/payment gating is a guard function called
first in the handler, not a framework middleware chain. require_payment
follows that same shape: call it, return its .error Response immediately if
set, otherwise proceed with the now-paid-for work.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from robyn import Request, Response
from x402.http.types import (
    HTTPRequestContext,
    HTTPResponseInstructions,
    PaymentOption,
    RouteConfig,
)
from x402.http.x402_http_server import x402HTTPResourceServerSync

from app.core.config import settings
from app.modules.x402.adapter import RobynAdapter
from app.modules.x402.client import get_resource_server

# The contest's required "x402-global-challenge" tag is injected via a custom
# money parser registered on the AVM scheme in client.py, NOT here —
# PaymentOption.extra is never read by the installed package when building
# the response (verified against x402/server_base.py). See client.py's
# CHALLENGE_TAG for the actual mechanism.


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


def _instructions_to_response(instr: HTTPResponseInstructions) -> Response:
    if instr.is_html:
        body = instr.body if isinstance(instr.body, str) else ""
    else:
        body = json.dumps(instr.body if instr.body is not None else {})
    return Response(status_code=instr.status, headers=instr.headers, description=body)


def require_payment(
    request: Request,
    *,
    price: str,
    resource: str,
    extensions: dict[str, Any] | None = None,
) -> PaymentResult:
    """Gate a Robyn handler behind an x402 payment.

    `price` is a Money string, e.g. "$0.01" — converted to USDC atomic units
    by the tagged money parser registered in client.py, which is also where
    the required challenge tag gets attached (see CHALLENGE_TAG there).
    `resource` is a short stable id for this endpoint, shown to the payer.
    `extensions` sets RouteConfig.extensions — pass
    `x402.extensions.bazaar.declare_discovery_extension(...)` here to make a
    route Bazaar-discoverable (required for the leaderboard, not automatic).
    """
    route_config = RouteConfig(
        accepts=PaymentOption(
            scheme="exact",
            pay_to=settings.x402_pay_to_address,
            price=price,
            network=settings.x402_network,
        ),
        resource=resource,
        extensions=extensions,
    )
    # A fresh wrapper per call (route compilation is cheap, local regex work)
    # around the process-wide, already-initialized resource server — this
    # avoids re-hitting the facilitator's /supported endpoint per request.
    http_server = x402HTTPResourceServerSync(get_resource_server(), route_config)
    context = HTTPRequestContext(
        adapter=RobynAdapter(request),
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
                description=json.dumps(
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
                description=json.dumps(
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
    )
