"""`PxkeClient` -- a thin wrapper around PXke's x402 marketplace.

Talks to `https://algorand-api.pxke.me` (Algorand **mainnet**, real money).
Free methods need nothing. Paid methods need a 25-word Algorand mnemonic and
follow the mainnet-proven flow from `backend/scripts/x402_mainnet_probe.py`
in the PXke Algorand backend repo: unpaid request -> 402 (offer in the
`PAYMENT-REQUIRED` response header) -> build + sign a payment for one of the
offered assets (USDC preferred, matching the marketplace's own offer order)
-> retry with the `PAYMENT-SIGNATURE` header -> parsed JSON response.

See `docs/x402-marketplace-api.md` in that repo for the full route roster;
this client only wraps the routes named in its own docstrings below, not the
full catalog (renew/vote/claim/demand/top and the not-yet-enabled KYA routes
are deliberately out of scope for this thin wrapper).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import requests

from .exceptions import PxkeConfigError, PxkeHTTPError, PxkePaymentError
from .signer import WorkingAvmSigner

BASE_URL = "https://algorand-api.pxke.me"
MAINNET_CAIP2 = "algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8="
DEFAULT_ALGOD_URL = "https://mainnet-api.algonode.cloud"
DEFAULT_TIMEOUT = 30


def _params(**kwargs: Any) -> dict[str, Any]:
    """Drop None values so unset query params aren't sent as literal "None"."""
    return {k: v for k, v in kwargs.items() if v is not None}


def _bypass_params(
    *, preview: bool, promo_code: str | None, promo_wallet: str | None
) -> dict[str, Any]:
    """The shared `?preview=`/`?promo=`/`?promo_wallet=` query params, empty when unused.

    See `docs/x402-marketplace-api.md`'s "Preview and promo codes" section in
    the PXke Algorand backend repo for what these do server-side. As of this
    writing the server only wires either mechanism up for `GET
    /api/v1/x402/ping` (check a route's `supports_preview`/`supports_promo`
    in `catalog()` before relying on either elsewhere) -- every paid method
    below accepts these kwargs regardless, so the SDK doesn't need another
    release once more routes are wired.
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
    """

    def __init__(
        self,
        mnemonic: str | None = None,
        *,
        session: requests.Session | None = None,
        algod_url: str = DEFAULT_ALGOD_URL,
        timeout: int = DEFAULT_TIMEOUT,
        http_client: Any | None = None,
    ) -> None:
        self._mnemonic = mnemonic
        self._session = session or requests.Session()
        self._algod_url = algod_url
        self._timeout = timeout
        self._http_client = http_client
        self._signer: WorkingAvmSigner | None = None

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
                "Free methods (catalog(), search(), board(), features(), grades(), news(), "
                "settlements_recent(), probe(), file_feature_request()) need no mnemonic."
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

        http_client = self._ensure_payment_client()
        try:
            payment_headers, _payload = http_client.handle_402_response(
                dict(r1.headers), r1.content
            )
        except Exception as exc:  # noqa: BLE001 - re-raised as a typed payment error, not swallowed
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

    # ------------------------------------------------------------------ #
    # Free methods
    # ------------------------------------------------------------------ #

    def catalog(self) -> dict[str, Any]:
        """`GET /api/v1/x402` -- the live route roster, prices, network and assets."""
        return self._free_request("GET", "/api/v1/x402")

    def search(
        self,
        tag: str | None = None,
        category: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """`GET /api/v1/x402/search` -- unexpired directory listings, newest first.

        `tag` and `category` together is rejected by the server (400).
        """
        return self._free_request(
            "GET", "/api/v1/x402/search", params=_params(tag=tag, category=category, limit=limit)
        )

    def board(self, limit: int | None = None) -> dict[str, Any]:
        """`GET /api/v1/x402/board` -- live visibility-board tiles with click counts."""
        return self._free_request("GET", "/api/v1/x402/board", params=_params(limit=limit))

    def features(self, limit: int | None = None) -> dict[str, Any]:
        """`GET /api/v1/x402/features` -- filed feature requests, no vote totals."""
        return self._free_request("GET", "/api/v1/x402/features", params=_params(limit=limit))

    def grades(self, limit: int | None = None) -> dict[str, Any]:
        """`GET /api/v1/x402/grades` -- every graded endpoint, no scores."""
        return self._free_request("GET", "/api/v1/x402/grades", params=_params(limit=limit))

    def news(self, tag: str | None = None, limit: int | None = None) -> dict[str, Any]:
        """`GET /api/v1/x402/news` -- latest published headlines (id, slug, summary)."""
        return self._free_request(
            "GET", "/api/v1/x402/news", params=_params(tag=tag, limit=limit)
        )

    def read_article(self, article_id: str) -> dict[str, Any]:
        """`GET /api/v1/x402/news/articles/:article_id` -- one full article, by uuid or slug.

        Free: the content is already free on the public website. No wallet
        needed, no preview/promo bypass to pass -- there is no gate here.
        """
        return self._free_request("GET", f"/api/v1/x402/news/articles/{quote(article_id, safe='')}")

    def settlements_recent(self, limit: int | None = None) -> dict[str, Any]:
        """`GET /api/v1/x402/settlements/recent` -- proof-of-volume feed, real payments only."""
        return self._free_request(
            "GET", "/api/v1/x402/settlements/recent", params=_params(limit=limit)
        )

    def probe(self, url: str) -> dict[str, Any]:
        """`GET /api/v1/x402/directory/probe` -- newest unpaid reachability probe for a listed url."""
        return self._free_request("GET", "/api/v1/x402/directory/probe", params={"url": url})

    def probe_history(self, url: str, limit: int | None = None) -> dict[str, Any]:
        """`GET /api/v1/x402/directory/probe/history` -- past probe results for a listed url, newest first."""
        return self._free_request(
            "GET", "/api/v1/x402/directory/probe/history", params=_params(url=url, limit=limit)
        )

    def file_feature_request(self, title: str, description: str) -> dict[str, Any]:
        """`POST /api/v1/x402/features` -- free, anonymous. File one feature request."""
        return self._free_request(
            "POST", "/api/v1/x402/features", json_body={"title": title, "description": description}
        )

    # ------------------------------------------------------------------ #
    # Paid methods
    # ------------------------------------------------------------------ #

    def list_endpoint(
        self,
        url: str,
        price: str,
        description: str,
        *,
        assets: list[str] | None = None,
        tags: list[str] | None = None,
        category: str | None = None,
        schema: dict[str, Any] | None = None,
        preview: bool = False,
        promo_code: str | None = None,
        promo_wallet: str | None = None,
    ) -> dict[str, Any]:
        """`POST /api/v1/x402/list` -- list one x402 endpoint in the directory for 30 days.

        `price` is the *listed* endpoint's own price text (e.g. "$0.01"), not
        what this call itself costs. `category` is one of: data, ai, finance,
        identity, storage, compute, social, tooling, other.

        `preview`/`promo_code`/`promo_wallet` are the shared payment-gate
        bypasses (see `PxkeClient.ping`'s docstring) -- as of this writing the
        server only honors them on `ping()`, not here.
        """
        body: dict[str, Any] = {"url": url, "price": price, "description": description}
        if assets is not None:
            body["assets"] = assets
        if tags is not None:
            body["tags"] = tags
        if category is not None:
            body["category"] = category
        if schema is not None:
            body["schema"] = schema
        return self._paid_request(
            "POST",
            "/api/v1/x402/list",
            json_body=body,
            params=_bypass_params(preview=preview, promo_code=promo_code, promo_wallet=promo_wallet),
        )

    def place_on_board(
        self,
        link: str,
        name: str,
        pitch: str,
        *,
        preview: bool = False,
        promo_code: str | None = None,
        promo_wallet: str | None = None,
    ) -> dict[str, Any]:
        """`POST /api/v1/x402/board` -- place one link/name/pitch tile for 14 days.

        `preview`/`promo_code`/`promo_wallet` are the shared payment-gate
        bypasses (see `PxkeClient.ping`'s docstring) -- as of this writing the
        server only honors them on `ping()`, not here.
        """
        return self._paid_request(
            "POST",
            "/api/v1/x402/board",
            json_body={"link": link, "name": name, "pitch": pitch},
            params=_bypass_params(preview=preview, promo_code=promo_code, promo_wallet=promo_wallet),
        )

    def submit_grade(
        self,
        url: str,
        score: int,
        comment: str = "",
        *,
        preview: bool = False,
        promo_code: str | None = None,
        promo_wallet: str | None = None,
    ) -> dict[str, Any]:
        """`POST /api/v1/x402/grades` -- grade any http(s) endpoint 1-5; re-grading replaces.

        `preview`/`promo_code`/`promo_wallet` are the shared payment-gate
        bypasses (see `PxkeClient.ping`'s docstring) -- as of this writing the
        server only honors them on `ping()`, not here.
        """
        return self._paid_request(
            "POST",
            "/api/v1/x402/grades",
            json_body={"url": url, "score": score, "comment": comment},
            params=_bypass_params(preview=preview, promo_code=promo_code, promo_wallet=promo_wallet),
        )

    def read_score(
        self,
        url: str,
        *,
        preview: bool = False,
        promo_code: str | None = None,
        promo_wallet: str | None = None,
    ) -> dict[str, Any]:
        """`GET /api/v1/x402/grades/score` -- credibility-weighted grade aggregate for one url.

        `preview`/`promo_code`/`promo_wallet` are the shared payment-gate
        bypasses (see `PxkeClient.ping`'s docstring) -- as of this writing the
        server only honors them on `ping()`, not here.
        """
        return self._paid_request(
            "GET",
            "/api/v1/x402/grades/score",
            params={
                "url": url,
                **_bypass_params(preview=preview, promo_code=promo_code, promo_wallet=promo_wallet),
            },
        )

    def search_news(
        self,
        q: str,
        limit: int | None = None,
        *,
        preview: bool = False,
        promo_code: str | None = None,
        promo_wallet: str | None = None,
    ) -> dict[str, Any]:
        """`GET /api/v1/x402/news/search` -- ranked full-text search over published articles.

        `preview`/`promo_code`/`promo_wallet` are the shared payment-gate
        bypasses (see `PxkeClient.ping`'s docstring) -- as of this writing the
        server only honors them on `ping()`, not here.
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

    def ping(
        self,
        *,
        preview: bool = False,
        promo_code: str | None = None,
        promo_wallet: str | None = None,
    ) -> dict[str, Any]:
        """`GET /api/v1/x402/ping` -- the marketplace's lowest price ($0.001), for smoke-testing a client.

        `preview=True` bypasses payment for a redacted response (no
        facilitator call, nothing settled), rate-limited per IP server-side.
        `promo_code`/`promo_wallet` attempt an admin-issued promo-code
        bypass scoped to this route; on success you get the real response
        with no settlement. Both are silent no-ops server-side if the route
        doesn't support them or the attempt fails -- `ping()` is the
        reference route where they actually work today; see
        `docs/x402-marketplace-api.md`'s "Preview and promo codes" section
        in the PXke Algorand backend repo. Note this call still needs a
        client built with a mnemonic (or an injected `http_client`) even
        when `preview=True`, since the client can't yet tell in advance
        that no payment will be required.
        """
        return self._paid_request(
            "GET",
            "/api/v1/x402/ping",
            params=_bypass_params(preview=preview, promo_code=promo_code, promo_wallet=promo_wallet),
        )
