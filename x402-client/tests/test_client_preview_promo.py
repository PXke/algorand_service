"""`preview`/`promo_code`/`promo_wallet` kwargs: correct query params, and the
200-on-first-request bypass short-circuit (no signing, no retry).

These kwargs exist on every paid method (see client.py's `_bypass_params`),
but the server only honors them on `ping()` as of this writing -- these
tests only check the client-side plumbing (what gets sent, and how a 200
first response is handled), not server behavior on the other routes.
"""

from __future__ import annotations

from pxke_x402 import PxkeClient
from pxke_x402.client import BASE_URL

from .conftest import FakePaymentHTTPClient, FakeResponse, FakeSession


def _offer_headers() -> dict[str, str]:
    return {"payment-required": "ZmFrZS1vZmZlcg=="}


def test_ping_sends_no_bypass_params_by_default() -> None:
    session = FakeSession(
        [FakeResponse(402, headers=_offer_headers()), FakeResponse(200, {"pong": True})]
    )
    client = PxkeClient(session=session, http_client=FakePaymentHTTPClient())

    client.ping()

    assert session.calls[0].params == {}


def test_ping_preview_sends_preview_true_and_returns_the_first_response() -> None:
    session = FakeSession([FakeResponse(200, {"pong": True, "settlement_tx_id": "<preview>"})])
    fake_payment = FakePaymentHTTPClient()
    client = PxkeClient(session=session, http_client=fake_payment)

    result = client.ping(preview=True)

    assert result == {"pong": True, "settlement_tx_id": "<preview>"}
    assert session.calls[0].params == {"preview": True}
    # No 402, so nothing was ever signed and there was no second request.
    assert fake_payment.calls == []
    assert len(session.calls) == 1


def test_ping_promo_sends_promo_and_promo_wallet_and_returns_the_first_response() -> None:
    session = FakeSession(
        [FakeResponse(200, {"pong": True, "via": "promo", "settlement_tx_id": ""})]
    )
    fake_payment = FakePaymentHTTPClient()
    client = PxkeClient(session=session, http_client=fake_payment)

    result = client.ping(promo_code="LAUNCH50", promo_wallet="WALLETADDR")

    assert result == {"pong": True, "via": "promo", "settlement_tx_id": ""}
    assert session.calls[0].params == {"promo": "LAUNCH50", "promo_wallet": "WALLETADDR"}
    assert fake_payment.calls == []
    assert len(session.calls) == 1


def test_ping_promo_that_fails_falls_through_to_the_normal_paid_flow() -> None:
    """A failed promo attempt is silent server-side: 402 as usual, same params resent on retry."""
    session = FakeSession(
        [
            FakeResponse(402, headers=_offer_headers()),
            FakeResponse(200, {"pong": True, "settlement_tx_id": "TX1"}),
        ]
    )
    client = PxkeClient(session=session, http_client=FakePaymentHTTPClient())

    result = client.ping(promo_code="EXPIRED")

    assert result == {"pong": True, "settlement_tx_id": "TX1"}
    assert session.calls[0].params == {"promo": "EXPIRED"}
    assert session.calls[1].params == {"promo": "EXPIRED"}  # resent unchanged on the paid retry


def test_read_score_merges_bypass_params_with_its_own_url_param() -> None:
    session = FakeSession([FakeResponse(200, {"weighted_mean": 4.2})])
    client = PxkeClient(session=session, http_client=FakePaymentHTTPClient())

    client.read_score("https://example.com", preview=True)

    assert session.calls[0].params == {"url": "https://example.com", "preview": True}


def test_search_news_merges_bypass_params_with_q_and_limit() -> None:
    session = FakeSession([FakeResponse(200, {"items": []})])
    client = PxkeClient(session=session, http_client=FakePaymentHTTPClient())

    client.search_news("tinyman", limit=5, promo_code="CODE", promo_wallet="ADDR")

    assert session.calls[0].params == {
        "q": "tinyman",
        "limit": 5,
        "promo": "CODE",
        "promo_wallet": "ADDR",
    }


def test_read_article_sends_bypass_params_on_a_path_only_route() -> None:
    session = FakeSession([FakeResponse(200, {"article_id": "a"})])
    client = PxkeClient(session=session, http_client=FakePaymentHTTPClient())

    client.read_article("a", preview=True)

    assert session.calls[0].url == f"{BASE_URL}/api/v1/x402/news/articles/a"
    assert session.calls[0].params == {"preview": True}


def test_post_methods_send_bypass_params_as_query_params_not_in_the_body() -> None:
    """list_endpoint / place_on_board / submit_grade are POSTs with a JSON body;
    preview/promo must land in the query string, not get mixed into the body."""
    session = FakeSession([FakeResponse(200, {"listing": {}})])
    client = PxkeClient(session=session, http_client=FakePaymentHTTPClient())

    client.list_endpoint(
        "https://api.example.com/v1/quote", "$0.01", "FX quotes", promo_code="CODE",
        promo_wallet="ADDR",
    )

    call = session.calls[0]
    assert call.params == {"promo": "CODE", "promo_wallet": "ADDR"}
    assert call.json_body == {
        "url": "https://api.example.com/v1/quote",
        "price": "$0.01",
        "description": "FX quotes",
    }


def test_submit_grade_sends_preview_as_a_query_param() -> None:
    session = FakeSession([FakeResponse(200, {"grade": {}})])
    client = PxkeClient(session=session, http_client=FakePaymentHTTPClient())

    client.submit_grade("https://example.com", 5, preview=True)

    assert session.calls[0].params == {"preview": True}


def test_place_on_board_sends_promo_params() -> None:
    session = FakeSession([FakeResponse(200, {"placement": {}})])
    client = PxkeClient(session=session, http_client=FakePaymentHTTPClient())

    client.place_on_board(
        "https://agent.example.com", "Agent", "pitch", promo_code="CODE", promo_wallet="ADDR"
    )

    assert session.calls[0].params == {"promo": "CODE", "promo_wallet": "ADDR"}
