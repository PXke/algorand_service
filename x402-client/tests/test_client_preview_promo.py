"""`preview`/`promo_code`/`promo_wallet` kwargs: correct query params, and the 200-on-first-request bypass short-circuit (no signing, no retry).

These kwargs exist on every paid method (see client.py's `_bypass_params`).
These tests only check the client-side plumbing (what gets sent, and how a
200 first response is handled), not server behavior -- a route's live
`supports_preview`/`supports_promo` flags in `catalog()` are the source of
truth for which routes honor them.
"""

from __future__ import annotations

from pxke_x402 import PxkeClient
from pxke_x402.client import BASE_URL

from .conftest import FakePaymentHTTPClient, FakeResponse, FakeSession
from .conftest import valid_offer_headers as _offer_headers


def test_search_news_sends_no_bypass_params_by_default() -> None:
    session = FakeSession(
        [FakeResponse(402, headers=_offer_headers()), FakeResponse(200, {"items": []})]
    )
    client = PxkeClient(session=session, http_client=FakePaymentHTTPClient())

    client.search_news("tinyman")

    assert session.calls[0].params == {"q": "tinyman"}


def test_search_news_preview_sends_preview_true_and_returns_the_first_response() -> None:
    session = FakeSession([FakeResponse(200, {"items": [], "settlement_tx_id": "<preview>"})])
    fake_payment = FakePaymentHTTPClient()
    client = PxkeClient(session=session, http_client=fake_payment)

    result = client.search_news("tinyman", preview=True)

    assert result == {"items": [], "settlement_tx_id": "<preview>"}
    assert session.calls[0].params == {"q": "tinyman", "preview": True}
    # No 402, so nothing was ever signed and there was no second request.
    assert fake_payment.calls == []
    assert len(session.calls) == 1


def test_search_news_promo_sends_promo_and_promo_wallet_and_returns_the_first_response() -> None:
    session = FakeSession(
        [FakeResponse(200, {"items": [], "via": "promo", "settlement_tx_id": ""})]
    )
    fake_payment = FakePaymentHTTPClient()
    client = PxkeClient(session=session, http_client=fake_payment)

    result = client.search_news(
        "tinyman", limit=5, promo_code="LAUNCH50", promo_wallet="WALLETADDR"
    )

    assert result == {"items": [], "via": "promo", "settlement_tx_id": ""}
    assert session.calls[0].params == {
        "q": "tinyman",
        "limit": 5,
        "promo": "LAUNCH50",
        "promo_wallet": "WALLETADDR",
    }
    assert fake_payment.calls == []
    assert len(session.calls) == 1


def test_a_promo_that_fails_falls_through_to_the_normal_paid_flow() -> None:
    """A failed promo attempt is silent server-side: 402 as usual, same params resent on retry."""
    session = FakeSession(
        [
            FakeResponse(402, headers=_offer_headers()),
            FakeResponse(200, {"items": [], "settlement_tx_id": "TX1"}),
        ]
    )
    client = PxkeClient(session=session, http_client=FakePaymentHTTPClient())

    result = client.search_news("tinyman", promo_code="EXPIRED")

    assert result == {"items": [], "settlement_tx_id": "TX1"}
    assert session.calls[0].params == {"q": "tinyman", "promo": "EXPIRED"}
    assert session.calls[1].params == {"q": "tinyman", "promo": "EXPIRED"}  # resent unchanged


def test_scan_url_preview_sends_preview_as_a_query_param_and_never_signs() -> None:
    """The preview scan is the server's fixed fake report: one plain 200, no 402, nothing signed."""
    preview_report = {
        "source_url": "<preview>",
        "status": "preview",
        "risk": {"score": -1.0, "verdict": "<preview>", "malicious": None, "caution_notes": []},
        "settlement_tx_id": "<preview>",
    }
    session = FakeSession([FakeResponse(200, preview_report)])
    fake_payment = FakePaymentHTTPClient()
    client = PxkeClient(session=session, http_client=fake_payment)

    result = client.scan_url("https://example.com/file.zip", preview=True)

    assert result == preview_report
    assert result["risk"]["malicious"] is None  # a preview is never a verdict
    call = session.calls[0]
    assert call.url == f"{BASE_URL}/api/v1/x402/scan/url"
    assert call.params == {"preview": True}
    assert call.json_body == {"url": "https://example.com/file.zip"}  # url stays in the body
    assert fake_payment.calls == []
    assert len(session.calls) == 1


def test_post_methods_send_bypass_params_as_query_params_not_in_the_body() -> None:
    """scan_url / storage_create_backup are POSTs with a JSON body; preview/promo must land in the query string, not get mixed into the body."""
    session = FakeSession([FakeResponse(200, {"risk": {}})])
    client = PxkeClient(session=session, http_client=FakePaymentHTTPClient())

    client.scan_url("https://example.com/file.zip", promo_code="CODE", promo_wallet="ADDR")

    call = session.calls[0]
    assert call.params == {"promo": "CODE", "promo_wallet": "ADDR"}
    assert call.json_body == {"url": "https://example.com/file.zip"}


def test_storage_create_backup_merges_bypass_params_with_declared_size() -> None:
    session = FakeSession([FakeResponse(200, {"backup_id": "b1"})])
    client = PxkeClient(session=session, http_client=FakePaymentHTTPClient())

    client.storage_create_backup(b"hello", promo_code="CODE", promo_wallet="ADDR")

    assert session.calls[0].params == {
        "declared_size_bytes": 5,
        "promo": "CODE",
        "promo_wallet": "ADDR",
    }


def test_storage_renew_backup_merges_preview_with_its_own_wallet_param() -> None:
    session = FakeSession([FakeResponse(200, {"backup_id": "b1", "settlement_tx_id": "<preview>"})])
    client = PxkeClient(session=session, http_client=FakePaymentHTTPClient())

    client.storage_renew_backup("b1", "WALLETADDR", preview=True)

    assert session.calls[0].params == {"wallet": "WALLETADDR", "preview": True}
