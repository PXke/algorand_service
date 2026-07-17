from __future__ import annotations

import json

from robyn import Request, Response

from app.modules.x402.guard import require_payment


def register_kyc_routes(app) -> None:
    # Throwaway: proves the x402 402 -> pay -> verify -> settle round-trip
    # through Robyn on TestNet. No attestation data, no Cassandra, no payout
    # leg yet — those land once this is confirmed working end to end.
    @app.get("/api/v1/kyc/_test/ping")
    async def kyc_test_ping(request: Request) -> Response:
        result = require_payment(request, price="$0.01", resource="kyc-ping")
        if result.error:
            return result.error
        return Response(
            status_code=200,
            headers={"Content-Type": "application/json", **result.settlement_headers},
            description=json.dumps({"ok": True, "paid_by": result.payer}),
        )
