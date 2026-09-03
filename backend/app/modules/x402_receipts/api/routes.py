"""HTTP route for the free signed-fulfillment-receipt read side.

`GET /api/v1/x402/receipts/{receipt_id}` -- the stored output plus every
field a third party needs to independently recompute the signed payload and
call `algosdk.util.verify_bytes(payload, signature, signing_address)`. See
modules/x402/receipts.py's own module docstring for the exact wire contract
(the same shapes/fields the X-Fulfillment-Receipt response header and this
route both carry).

Free, rate-limited per IP (CLAUDE.md section 9), same fail-open convention
as every other free x402 read in this codebase.
"""

from __future__ import annotations

from app.core.http import Request, Response, Router
from app.core.http_errors import json_error_response
from app.core.query_params import query_param
from app.modules.x402.receipt_store import get_receipt_store
from app.modules.x402.receipts import receipt_json
from app.modules.x402_receipts.services.rate_limit import receipts_rate_limited


def x402_receipt_detail(request: Request) -> Response | dict:
    """Free: one signed fulfillment receipt in full, by id; 404 if unknown or past its 90-day retention window."""
    if receipts_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many receipt requests — please try again later"
        )
    receipt_id = query_param(request.path_params.get("receipt_id", ""))
    if not receipt_id:
        return json_error_response(400, "invalid_request", "receipt_id is required")

    record = get_receipt_store().get_receipt(receipt_id)
    if record is None:
        return json_error_response(404, "not_found", "No receipt for that id")
    return receipt_json(record)


def register_x402_receipts_routes(app: Router) -> None:
    """Register the free receipt-read route."""
    app.get("/api/v1/x402/receipts/:receipt_id")(x402_receipt_detail)
