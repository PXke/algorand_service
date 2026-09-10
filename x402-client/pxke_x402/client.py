"""`PxkeClient` -- a thin wrapper around PXke's x402 marketplace.

Talks to `https://algorand-api.pxke.me` (Algorand **mainnet**, real money).
Free methods need nothing. Paid methods need a 25-word Algorand mnemonic and
follow the mainnet-proven flow from `backend/scripts/x402_mainnet_probe.py`
in the PXke Algorand backend repo: unpaid request -> 402 (offer in the
`PAYMENT-REQUIRED` response header) -> build + sign a payment for one of the
offered assets (USDC preferred, matching the marketplace's own offer order)
-> retry with the `PAYMENT-SIGNATURE` header -> parsed JSON response.

Wrapped products (see `GET /api/v1/x402` for the live, authoritative
catalog): the catalog and settlements feed, the News Engine (free headlines
and article read, paid search), the sandboxed URL scan (paid), and agent
backup storage (paid create/renew). The storage module's free, wallet-
signature-authenticated read/list/delete/versions routes are not wrapped
here -- see the README's "Development" section.
"""

from __future__ import annotations

import json
from base64 import b64encode
from collections.abc import Iterable
from typing import Any
from urllib.parse import quote

import requests

from .exceptions import PxkeConfigError, PxkeHTTPError, PxkeOfferValidationError, PxkePaymentError
from .signer import WorkingAvmSigner

BASE_URL = "https://algorand-api.pxke.me"
MAINNET_CAIP2 = "algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8="
DEFAULT_ALGOD_URL = "https://mainnet-api.algonode.cloud"
DEFAULT_TIMEOUT = 30

# PXke's own receive-only x402 payTo address (verified live 2026-09-07
# against prod's configured X402_PAY_TO_ADDRESS). This is the default
# recipient allowlist every paid call validates a 402 offer against BEFORE
# building or signing anything (2026-09-07 security review, finding 3):
# without this, a malicious or compromised 402 response -- a MITM, a DNS
# hijack, or a compromise of PXke's own server -- could redirect a real
# mainnet payment to an attacker's address, and a headless agent holding a
# funded mnemonic would sign and broadcast it with no human in the loop to
# notice. Pass `expected_pay_to=None` to PxkeClient(...) to disable this
# check (not recommended), or a different address/set if PXke's payTo
# address is ever rotated.
DEFAULT_EXPECTED_PAY_TO = "KSAVOYTVNB7A6NKCM4W2WBOOGFHWH2SEGR5T6OGB7THCAT5E36LDFEBTII"

# A conservative ceiling on any single payment this client will sign,
# expressed in atomic units assuming a 6-decimal (USDC-class) asset -- every
# route on this marketplace prices well under $0.25 as of this writing (see
# docs/x402-marketplace-api.md in the PXke Algorand backend repo), so
# $1.00-equivalent comfortably covers normal calls while still bounding a
# single malicious/compromised offer's worst case, rather than leaving it
# unlimited. Pass a higher `max_payment_atomic` (or None to disable) to
# PxkeClient(...) for a route you know legitimately costs more, e.g. a
# large storage backup priced per KB.
DEFAULT_MAX_PAYMENT_ATOMIC = 1_000_000


def _params(**kwargs: Any) -> dict[str, Any]:
    """Drop None values so unset query params aren't sent as literal "None"."""
    return {k: v for k, v in kwargs.items() if v is not None}


def _bypass_params(
    *, preview: bool, promo_code: str | None, promo_wallet: str | None
) -> dict[str, Any]:
    """The shared `?preview=`/`?promo=`/`?promo_wallet=` query params, empty when unused.

    `preview=True` bypasses payment for a redacted response with the same
    JSON shape (no facilitator call, nothing settled), rate-limited per IP
    server-side. `promo_code`/`promo_wallet` attempt an admin-issued
    promo-code bypass; on success the REAL response comes back tagged
    `via: "promo"` with an empty `settlement_tx_id`. Both are silent no-ops
    server-side when a route doesn't support them or the attempt fails --
    the call just falls through to the normal 402 -> pay -> retry flow.
    Check a route's `supports_preview`/`supports_promo` in `catalog()`
    before relying on either; see `docs/x402-marketplace-api.md`'s "Preview
    and promo codes" section in the PXke Algorand backend repo for the
    server-side contract.
    """
    return _params(preview=preview or None, promo=promo_code, promo_wallet=promo_wallet)


class PxkeClient:
    """A client for the PXke x402 marketplace.

    Args:
        mnemonic: A 25-word Algorand mnemonic for the paying wallet. Optional
            -- a client built without one can still call every free method;
            calling a paid method without one raises `PxkeConfigError`
            immediately, before any network request.
        session: A `requests.Session` to use (default: a fresh one). Handy
            for connection reuse or, in tests, for a fully faked transport.
        algod_url: Algod node used to build the payment transaction (fetch
            suggested params, etc). Only touched by paid calls.
        timeout: Per-request timeout in seconds (paid retries use double,
            since a real settlement round-trip is slower than a plain read).
        http_client: Test/advanced seam. An object already satisfying the
            `x402HTTPClientSync` interface (`.handle_402_response(headers,
            body) -> (headers, payload)`); when given, it is used as-is and
            `mnemonic`/`algod_url` are never touched to build one. Most
            callers should not pass this.
        expected_pay_to: Recipient allowlist checked against every 402
            offer's `pay_to` BEFORE any payment is built or signed (see
            `DEFAULT_EXPECTED_PAY_TO`'s own docstring for why this exists).
            A single address, an iterable of addresses, or None to disable
            the check entirely (not recommended).
        max_payment_atomic: Ceiling (in atomic units) checked against every
            402 offer's amount before signing (see
            `DEFAULT_MAX_PAYMENT_ATOMIC`'s own docstring). None disables
            the check entirely (not recommended).
    """

    def __init__(
        self,
        mnemonic: str | None = None,
        *,
        session: requests.Session | None = None,
        algod_url: str = DEFAULT_ALGOD_URL,
        timeout: int = DEFAULT_TIMEOUT,
        http_client: Any | None = None,
        expected_pay_to: str | Iterable[str] | None = DEFAULT_EXPECTED_PAY_TO,
        max_payment_atomic: int | None = DEFAULT_MAX_PAYMENT_ATOMIC,
    ) -> None:
        self._mnemonic = mnemonic
        self._session = session or requests.Session()
        self._algod_url = algod_url
        self._timeout = timeout
        self._http_client = http_client
        self._signer: WorkingAvmSigner | None = None
        if expected_pay_to is None:
            self._expected_pay_to: frozenset[str] = frozenset()
        elif isinstance(expected_pay_to, str):
            self._expected_pay_to = frozenset({expected_pay_to})
        else:
            self._expected_pay_to = frozenset(expected_pay_to)
        self._max_payment_atomic = max_payment_atomic

    # ------------------------------------------------------------------ #
    # Wallet identity
    # ------------------------------------------------------------------ #

    @property
    def address(self) -> str | None:
        """The payer wallet's address, or None if this client has no mnemonic."""
        if self._mnemonic is None:
            return None
        if self._signer is None:
            self._signer = WorkingAvmSigner(self._mnemonic)
        return self._signer.address

    # ------------------------------------------------------------------ #
    # Internal plumbing
    # ------------------------------------------------------------------ #

    def _url(self, path: str) -> str:
        return f"{BASE_URL}{path}"

    def _json_or_none(self, response: requests.Response) -> Any:
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    def _require_payment_capable(self) -> None:
        if self._http_client is None and self._mnemonic is None:
            raise PxkeConfigError(
                "this call is a paid marketplace route; construct PxkeClient(mnemonic=...) "
                "with a funded, USDC-opted-in Algorand wallet's 25-word mnemonic to use it. "
                "Free methods (catalog(), settlements_recent(), news(), read_article()) "
                "need no mnemonic."
            )

    def _ensure_payment_client(self) -> Any:
        if self._http_client is not None:
            return self._http_client
        # Heavy/optional imports deferred until a paid call actually needs them.
        from x402 import x402ClientSync
        from x402.http.x402_http_client import x402HTTPClientSync
        from x402.mechanisms.avm.exact import ExactAvmScheme

        if self._signer is None:
            self._signer = WorkingAvmSigner(self._mnemonic)  # type: ignore[arg-type]
        x402_client = x402ClientSync()
        x402_client.register(
            MAINNET_CAIP2, ExactAvmScheme(signer=self._signer, algod_url=self._algod_url)
        )
        self._http_client = x402HTTPClientSync(x402_client)
        return self._http_client

    def _free_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        response = self._session.request(
            method, self._url(path), params=params, json=json_body, timeout=self._timeout
        )
        body = self._json_or_none(response)
        if response.status_code >= 400:
            error = (body or {}).get("error") if isinstance(body, dict) else None
            message = (
                f"{method} {path} -> {response.status_code}"
                f"{': ' + error['message'] if isinstance(error, dict) and error.get('message') else ''}"
            )
            raise PxkeHTTPError(message, status_code=response.status_code, body=body)
        return body if body is not None else {}

    def _paid_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        self._require_payment_capable()
        url = self._url(path)

        r1 = self._session.request(
            method, url, params=params, json=json_body, timeout=self._timeout
        )
        if r1.status_code == 200:
            # A `preview=true` or a successful `promo=` bypassed the gate
            # entirely -- there was never a 402 to sign against, so this is
            # already the final (redacted, for preview; real, for promo)
            # response. See docs/x402-marketplace-api.md's "Preview and
            # promo codes" section.
            body = self._json_or_none(r1)
            return body if body is not None else {}
        if r1.status_code != 402:
            # The marketplace validates before the payment gate: a malformed
            # request is a plain 4xx here, nothing charged.
            body = self._json_or_none(r1)
            error = (body or {}).get("error") if isinstance(body, dict) else None
            message = (
                f"{method} {path} -> expected 402, got {r1.status_code}"
                f"{': ' + error['message'] if isinstance(error, dict) and error.get('message') else ''}"
            )
            raise PxkeHTTPError(message, status_code=r1.status_code, body=body)

        self._validate_offer(method, path, r1)

        http_client = self._ensure_payment_client()
        try:
            payment_headers, _payload = http_client.handle_402_response(
                dict(r1.headers), r1.content
            )
        except Exception as exc:  # re-raised as a typed payment error, not swallowed
            raise PxkePaymentError(
                f"{method} {path}: failed to build a signed payment from the 402 offer: {exc}"
            ) from exc

        r2 = self._session.request(
            method,
            url,
            params=params,
            json=json_body,
            headers={"Content-Type": "application/json", **payment_headers},
            timeout=self._timeout * 2,
        )
        body = self._json_or_none(r2)
        settled = any(k.lower() == "payment-response" for k in r2.headers)
        if r2.status_code == 200:
            return body if body is not None else {}

        error = (body or {}).get("error") if isinstance(body, dict) else None
        settlement_tx_id = (body or {}).get("settlement_tx_id") if isinstance(body, dict) else None
        message = f"{method} {path} -> {r2.status_code} after payment"
        if isinstance(error, dict) and error.get("message"):
            message += f": {error['message']}"
        if settled:
            message += " (payment settled but the request was refused -- see docs for why)"
        raise PxkePaymentError(
            message,
            status_code=r2.status_code,
            body=body,
            settlement_tx_id=settlement_tx_id,
            settled=settled,
        )

    def _parse_payment_required(self, response: requests.Response) -> Any:
        """Decode a 402 response's payment offer independently of the payment client, so it can be validated BEFORE anything is built or signed.

        Reuses the x402 package's own public decode_payment_required_header
        function and PaymentRequired/PaymentRequiredV1 schema classes --
        the same building blocks x402HTTPClientBase.get_payment_required_
        response uses internally (no new parsing logic, CLAUDE.md-style
        "don't copy existing logic" even though this SDK isn't in that
        repo). Done as a standalone step rather than via that method so
        validation stays in effect even when a caller injects their own
        `http_client` (see __init__'s docstring), which is only promised to
        satisfy the narrower `handle_402_response` interface.
        """
        from x402.http.utils import decode_payment_required_header
        from x402.schemas.v1 import PaymentRequiredV1

        # Normalize case ourselves rather than relying on requests.Response.
        # headers' CaseInsensitiveDict -- matches x402HTTPClientBase.
        # _handle_402_common's own approach, and stays correct against any
        # plain dict of headers (this method's only real contract).
        normalized_headers = {k.upper(): v for k, v in response.headers.items()}
        header = normalized_headers.get("PAYMENT-REQUIRED")
        if header:
            return decode_payment_required_header(header)
        if response.content:
            try:
                data = json.loads(response.content)
            except ValueError:
                data = None
            if isinstance(data, dict) and data.get("x402Version") == 1:
                return PaymentRequiredV1.model_validate(data)
        raise PxkeOfferValidationError("402 response carried no decodable payment offer")

    def _validate_offer(self, method: str, path: str, response: requests.Response) -> None:
        """Refuse a 402 offer before any payment is built or signed if it fails the recipient allowlist or amount cap (2026-09-07 security review, finding 3 -- see DEFAULT_EXPECTED_PAY_TO/DEFAULT_MAX_PAYMENT_ATOMIC's own docstrings for why).

        Checks every entry in `accepts`, not just whichever one the
        underlying x402 library ends up choosing to pay with -- this SDK
        does not duplicate that selection logic, so refusing on any bad
        entry is the conservative choice. In practice this marketplace
        offers only one or two accepts entries per route as of this
        writing, so this is not overly strict.
        """
        if not self._expected_pay_to and self._max_payment_atomic is None:
            return  # both checks explicitly disabled
        payment_required = self._parse_payment_required(response)
        for req in getattr(payment_required, "accepts", None) or []:
            pay_to = getattr(req, "pay_to", "")
            if self._expected_pay_to and pay_to not in self._expected_pay_to:
                raise PxkeOfferValidationError(
                    f"{method} {path}: refusing to pay -- the 402 offer's pay_to={pay_to!r} "
                    f"is not in the expected recipient set {sorted(self._expected_pay_to)!r}. "
                    "This could be a malicious or compromised response attempting to redirect "
                    "your payment. If PXke's payTo address has legitimately changed, pass the "
                    "new one via PxkeClient(expected_pay_to=...)."
                )
            if self._max_payment_atomic is not None:
                raw_amount = req.get_amount()
                try:
                    amount = int(raw_amount)
                except (TypeError, ValueError) as exc:
                    raise PxkeOfferValidationError(
                        f"{method} {path}: refusing to pay -- the 402 offer's amount "
                        f"{raw_amount!r} is not a valid integer"
                    ) from exc
                if amount > self._max_payment_atomic:
                    raise PxkeOfferValidationError(
                        f"{method} {path}: refusing to pay -- the 402 offer's amount "
                        f"{amount} atomic units exceeds max_payment_atomic="
                        f"{self._max_payment_atomic}. Pass a higher max_payment_atomic to "
                        "PxkeClient(...) if you expect calls this expensive."
                    )

    # ------------------------------------------------------------------ #
    # Free methods
    # ------------------------------------------------------------------ #

    def catalog(self) -> dict[str, Any]:
        """`GET /api/v1/x402` -- the live route roster, prices, network and assets."""
        return self._free_request("GET", "/api/v1/x402")

    def settlements_recent(self, limit: int | None = None) -> dict[str, Any]:
        """`GET /api/v1/x402/settlements/recent` -- proof-of-volume feed, real payments only."""
        return self._free_request(
            "GET", "/api/v1/x402/settlements/recent", params=_params(limit=limit)
        )

    def news(self, tag: str | None = None, limit: int | None = None) -> dict[str, Any]:
        """`GET /api/v1/x402/news` -- latest published headlines (id, slug, summary)."""
        return self._free_request("GET", "/api/v1/x402/news", params=_params(tag=tag, limit=limit))

    def read_article(self, article_id: str) -> dict[str, Any]:
        """`GET /api/v1/x402/news/articles/:article_id` -- one full article, by uuid or slug.

        Free: the content is already free on the public website. No wallet
        needed, no preview/promo bypass to pass -- there is no gate here.
        """
        return self._free_request("GET", f"/api/v1/x402/news/articles/{quote(article_id, safe='')}")

    # ------------------------------------------------------------------ #
    # Paid methods -- every one accepts the shared `preview` /
    # `promo_code` / `promo_wallet` bypass kwargs (see `_bypass_params`).
    # ------------------------------------------------------------------ #

    def search_news(
        self,
        q: str,
        limit: int | None = None,
        *,
        preview: bool = False,
        promo_code: str | None = None,
        promo_wallet: str | None = None,
    ) -> dict[str, Any]:
        """`GET /api/v1/x402/news/search` -- paid: ranked full-text search over published articles.

        Supports `preview=True` (the response shape with every value
        redacted; the search engine is never run for a preview).
        """
        return self._paid_request(
            "GET",
            "/api/v1/x402/news/search",
            params=_params(
                q=q,
                limit=limit,
                **_bypass_params(preview=preview, promo_code=promo_code, promo_wallet=promo_wallet),
            ),
        )

    # ------------------------------------------------------------------ #
    # Sandboxed URL scan
    # ------------------------------------------------------------------ #

    def scan_url(
        self,
        url: str,
        *,
        preview: bool = False,
        promo_code: str | None = None,
        promo_wallet: str | None = None,
    ) -> dict[str, Any]:
        """`POST /api/v1/x402/scan/url` -- paid: fetch `url` server-side and run a static security scan.

        The target is downloaded bounded and SSRF-guarded, then scanned in a
        network-isolated sandbox that never executes it: ClamAV signature
        match, file-type check, entropy, embedded URL/IP extraction, and a
        zip/tar-bomb-safe archive member listing with the same checks per
        member. The response carries `risk.verdict` / `risk.malicious` /
        `risk.score` plus the per-tool sections and `settlement_tx_id`.

        `url` must be http:// or https:// (a malformed url is a plain 400
        before the payment gate -- nothing charged). A fetch failure of the
        target you supplied (refused connection, timeout, non-200) settles
        the payment and returns 422 -- raised here as `PxkePaymentError`
        with `.settled` True; a sandbox failure on our side is auto-refunded.

        Supports `preview=True`: a fixed, clearly-fake report in the real
        response shape -- no target is ever fetched or scanned for a
        preview, and `risk.malicious` is null, never a verdict.
        """
        return self._paid_request(
            "POST",
            "/api/v1/x402/scan/url",
            json_body={"url": url},
            params=_bypass_params(
                preview=preview, promo_code=promo_code, promo_wallet=promo_wallet
            ),
        )

    # --------------------------------------------------------------------- #
    # Agent backup storage
    # --------------------------------------------------------------------- #
    #
    # `GET`/`DELETE` on an existing backup, `GET .../backups` (listing) and
    # the per-version reads are deliberately NOT wrapped here: they're free
    # but wallet-signature-authenticated (POST /storage/auth/challenge then
    # sign the returned nonce), a flow this thin client does not implement.
    # Only the PAID actions, where the settled payment itself proves the
    # caller, are wrapped.

    def storage_create_backup(
        self,
        data: bytes,
        *,
        label: str = "",
        preview: bool = False,
        promo_code: str | None = None,
        promo_wallet: str | None = None,
    ) -> dict[str, Any]:
        """`POST /api/v1/x402/storage/backups` -- paid: store an opaque backup blob.

        Priced per KB against `len(data)` (sent as `declared_size_bytes`,
        required by the server BEFORE the 402 offer is built -- this method
        computes it for you from the bytes given). The paying wallet is the
        only one that can ever retrieve or delete this backup again (a
        fresh signed-challenge proof, not a session) -- see the server's own
        `POST /storage/auth/challenge` for that flow, not wrapped here.
        Content is opaque and unencrypted by us: encrypt sensitive data
        yourself before calling this if that matters to you.
        """
        return self._paid_request(
            "POST",
            "/api/v1/x402/storage/backups",
            json_body={"data": b64encode(data).decode("ascii"), "label": label},
            params={
                "declared_size_bytes": len(data),
                **_bypass_params(preview=preview, promo_code=promo_code, promo_wallet=promo_wallet),
            },
        )

    def storage_renew_backup(
        self,
        backup_id: str,
        wallet: str,
        *,
        preview: bool = False,
        promo_code: str | None = None,
        promo_wallet: str | None = None,
    ) -> dict[str, Any]:
        """`POST /api/v1/x402/storage/backups/{backup_id}/renew` -- paid: extend a backup's retrieval window.

        Priced from the backup's already-stored size. `wallet` is sent as
        the `?wallet=` query param the server looks the backup up by (it is
        a lookup hint, never proof of ownership) and must be the one that
        created this backup -- an unknown backup_id/wallet pair is a free
        404, while a payment from any other wallet settles but is refused
        (403) and changes nothing.
        """
        return self._paid_request(
            "POST",
            f"/api/v1/x402/storage/backups/{quote(backup_id, safe='')}/renew",
            params={
                "wallet": wallet,
                **_bypass_params(preview=preview, promo_code=promo_code, promo_wallet=promo_wallet),
            },
        )
