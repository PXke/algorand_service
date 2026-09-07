"""Exception types raised by :mod:`pxke_x402`.

Kept small and typed so a caller can tell a configuration mistake, a plain
HTTP problem, and a payment-specific failure apart without parsing strings.
"""

from __future__ import annotations

from typing import Any


class PxkeError(Exception):
    """Base class for every error this package raises deliberately."""


class PxkeConfigError(PxkeError):
    """The client isn't set up for the call it was asked to make.

    Raised, for example, when a paid method is called on a client built
    without a mnemonic -- before any HTTP request is made.
    """


class PxkeHTTPError(PxkeError):
    """A request failed for a reason unrelated to payment (4xx/5xx, bad JSON).

    Covers validation errors (400/404), which the marketplace always returns
    *before* the payment gate runs -- nothing was charged.
    """

    def __init__(
        self, message: str, *, status_code: int | None = None, body: Any = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class PxkePaymentError(PxkeError):
    """A paid call's 402->sign->retry flow failed, or settled but was refused.

    `settlement_tx_id` is set whenever the response body carried one (always
    true on a clean 200). `settled` is True when the retry response carried a
    PAYMENT-RESPONSE header even though the call itself failed -- the
    marketplace's documented "settled-then-refused" cases (e.g. renewing a
    listing you don't own): money moved, but nothing else changed. Check it
    before assuming a failed paid call didn't cost anything.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: Any = None,
        settlement_tx_id: str | None = None,
        settled: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body
        self.settlement_tx_id = settlement_tx_id
        self.settled = settled


class PxkeOfferValidationError(PxkePaymentError):
    """A 402 offer was refused before any payment was built or signed.

    Raised when a 402 response's payment requirements fail the client's own
    pre-sign checks (recipient allowlist, amount cap) -- see `PxkeClient`'s
    `expected_pay_to`/`max_payment_atomic` constructor params. `settled` is
    always False and `settlement_tx_id` always None here: this fires before
    any transaction is even built, let alone signed or broadcast. Distinct
    from the base `PxkePaymentError` so a caller can specifically alert on
    "the marketplace tried to make me pay something suspicious" separately
    from an ordinary payment/settlement failure.
    """

