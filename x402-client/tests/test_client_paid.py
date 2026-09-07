"""Paid methods: the 402 -> sign -> retry flow, faked at the payment-client seam.

`http_client=` injects a fake standing in for `x402.http.x402_http_client.
x402HTTPClientSync` so these tests never touch algosdk, algod, or the real
x402 package -- no network, no real signing, matching CLAUDE.md's "fake the
seam, never mock the network."
"""

from __future__ import annotations

import pytest

from pxke_x402 import PxkeClient
from pxke_x402.client import BASE_URL
from pxke_x402.exceptions import PxkeHTTPError, PxkePaymentError

from .conftest import FakePaymentHTTPClient, FakeResponse, FakeSession
from .conftest import valid_offer_headers as _offer_headers


def test_ping_pays_and_returns_the_receipt() -> None:
    session = FakeSession(
        [
            FakeResponse(402, headers=_offer_headers()),
            FakeResponse(200, {"ok": True, "settlement_tx_id": "TX123"}, headers={"payment-response": "..."}),
        ]
    )
    fake_payment = FakePaymentHTTPClient()
    client = PxkeClient(session=session, http_client=fake_payment)

    result = client.ping()

    assert result == {"ok": True, "settlement_tx_id": "TX123"}
    assert len(fake_payment.calls) == 1
    # The retry carried the payment headers the fake payment client returned.
    retry_call = session.calls[1]
    assert retry_call.headers["PAYMENT-SIGNATURE"] == "fake-signature"
    assert retry_call.method == "GET"
    assert retry_call.url == f"{BASE_URL}/api/v1/x402/ping"


def test_list_endpoint_sends_the_right_body_and_omits_unset_optional_fields() -> None:
    session = FakeSession(
        [
            FakeResponse(402, headers=_offer_headers()),
            FakeResponse(200, {"listing": {}, "settlement_tx_id": "TX1", "term_days": 30}),
        ]
    )
    client = PxkeClient(session=session, http_client=FakePaymentHTTPClient())

    client.list_endpoint("https://api.example.com/v1/quote", "$0.01", "FX quotes")

    first_call, retry_call = session.calls
    assert first_call.json_body == {
        "url": "https://api.example.com/v1/quote",
        "price": "$0.01",
        "description": "FX quotes",
    }
    assert retry_call.json_body == first_call.json_body  # same body resent with payment


def test_list_endpoint_includes_optional_fields_when_given() -> None:
    session = FakeSession(
        [FakeResponse(402, headers=_offer_headers()), FakeResponse(200, {"settlement_tx_id": "TX1"})]
    )
    client = PxkeClient(session=session, http_client=FakePaymentHTTPClient())

    client.list_endpoint(
        "https://api.example.com/v1/quote",
        "$0.01",
        "FX quotes",
        assets=["USDC"],
        tags=["fx", "market-data"],
        category="finance",
        schema={"type": "object"},
    )

    assert session.calls[0].json_body == {
        "url": "https://api.example.com/v1/quote",
        "price": "$0.01",
        "description": "FX quotes",
        "assets": ["USDC"],
        "tags": ["fx", "market-data"],
        "category": "finance",
        "schema": {"type": "object"},
    }


def test_a_validation_error_before_the_gate_raises_http_error_and_never_pays() -> None:
    """A malformed body is a plain 4xx, not a 402 -- nothing charged, no payment built."""
    session = FakeSession([FakeResponse(400, {"error": {"code": "bad_request", "message": "score must be 1-5"}})])
    fake_payment = FakePaymentHTTPClient()
    client = PxkeClient(session=session, http_client=fake_payment)

    with pytest.raises(PxkeHTTPError) as excinfo:
        client.submit_grade("https://example.com", 9)

    assert excinfo.value.status_code == 400
    assert "score must be 1-5" in str(excinfo.value)
    assert fake_payment.calls == []  # no payment was ever attempted
    assert len(session.calls) == 1  # no retry either


def test_a_settled_but_refused_response_raises_payment_error_with_settled_true() -> None:
    """Owner-only ownership refusal: payment settles, request is refused, nothing changes."""
    session = FakeSession(
        [
            FakeResponse(402, headers=_offer_headers()),
            FakeResponse(
                403,
                {"error": {"code": "listing_owned_by_another_payer", "message": "not your listing"}},
                headers={"payment-response": "receipt-blob"},
            ),
        ]
    )
    client = PxkeClient(session=session, http_client=FakePaymentHTTPClient())

    with pytest.raises(PxkePaymentError) as excinfo:
        client.list_endpoint("https://example.com", "$0.01", "desc")

    assert excinfo.value.status_code == 403
    assert excinfo.value.settled is True
    assert excinfo.value.settlement_tx_id is None  # not in the body for this case
    assert "settled but the request was refused" in str(excinfo.value)


def test_a_failure_building_the_payment_raises_payment_error_not_a_raw_exception() -> None:
    session = FakeSession([FakeResponse(402, headers=_offer_headers())])
    broken_payment_client = FakePaymentHTTPClient(raises=RuntimeError("bad offer encoding"))
    client = PxkeClient(session=session, http_client=broken_payment_client)

    with pytest.raises(PxkePaymentError) as excinfo:
        client.ping()

    assert "bad offer encoding" in str(excinfo.value)
    assert len(session.calls) == 1  # never got to retry


def test_settlement_tx_id_is_surfaced_on_success() -> None:
    session = FakeSession(
        [
            FakeResponse(402, headers=_offer_headers()),
            FakeResponse(200, {"grade": {"score": 5}, "settlement_tx_id": "TXABC"}),
        ]
    )
    client = PxkeClient(session=session, http_client=FakePaymentHTTPClient())

    result = client.submit_grade("https://example.com", 5, comment="great")

    assert result["settlement_tx_id"] == "TXABC"


# --------------------------------------------------------------------------- #
# Social network (Phase S0/S1)
# --------------------------------------------------------------------------- #
def test_social_register_sends_the_right_body() -> None:
    session = FakeSession(
        [
            FakeResponse(402, headers=_offer_headers()),
            FakeResponse(200, {"wallet": "AGENT1", "settlement_tx_id": "TX1"}),
        ]
    )
    client = PxkeClient(session=session, http_client=FakePaymentHTTPClient())

    result = client.social_register("Scout", bio="finds things", interests=["nft", "defi"])

    assert result["wallet"] == "AGENT1"
    _first_call, retry_call = session.calls
    assert retry_call.url == f"{BASE_URL}/api/v1/x402/social/register"
    assert retry_call.json_body == {
        "name": "Scout",
        "bio": "finds things",
        "mission": "",
        "location": "",
        "interests": ["nft", "defi"],
        "emoji": "",
    }


def test_social_create_post_and_comment_url_encode_the_post_id() -> None:
    session = FakeSession(
        [
            FakeResponse(402, headers=_offer_headers()),
            FakeResponse(200, {"post_id": "p/1", "settlement_tx_id": "TX1"}),
        ]
    )
    client = PxkeClient(session=session, http_client=FakePaymentHTTPClient())

    client.social_create_post("hello world", tags=["intro"])

    retry_call = session.calls[1]
    assert retry_call.url == f"{BASE_URL}/api/v1/x402/social/posts"
    assert retry_call.json_body == {"body_md": "hello world", "tags": ["intro"], "group_id": ""}

    session2 = FakeSession(
        [
            FakeResponse(402, headers=_offer_headers()),
            FakeResponse(200, {"comment_id": "c1", "settlement_tx_id": "TX2"}),
        ]
    )
    client2 = PxkeClient(session=session2, http_client=FakePaymentHTTPClient())

    client2.social_create_comment("post/with-slash", "nice post")

    retry_call2 = session2.calls[1]
    assert retry_call2.url == f"{BASE_URL}/api/v1/x402/social/posts/post%2Fwith-slash/comments"
    assert retry_call2.json_body == {"body_md": "nice post"}


def test_social_follow_and_join_group_send_no_body() -> None:
    session = FakeSession(
        [
            FakeResponse(402, headers=_offer_headers()),
            FakeResponse(200, {"settlement_tx_id": "TX1"}),
        ]
    )
    client = PxkeClient(session=session, http_client=FakePaymentHTTPClient())

    client.social_follow("AGENT2")

    retry_call = session.calls[1]
    assert retry_call.url == f"{BASE_URL}/api/v1/x402/social/agents/AGENT2/follow"
    assert retry_call.json_body is None


def test_social_report_and_vote_send_the_right_bodies() -> None:
    session = FakeSession(
        [
            FakeResponse(402, headers=_offer_headers()),
            FakeResponse(200, {"case": {"case_id": "c1"}, "settlement_tx_id": "TX1"}),
        ]
    )
    client = PxkeClient(session=session, http_client=FakePaymentHTTPClient())

    client.social_report("post", "p1", "spam", note="looks like spam")

    retry_call = session.calls[1]
    assert retry_call.url == f"{BASE_URL}/api/v1/x402/social/reports"
    assert retry_call.json_body == {
        "target_type": "post",
        "target_id": "p1",
        "category": "spam",
        "note": "looks like spam",
    }

    session2 = FakeSession(
        [
            FakeResponse(402, headers=_offer_headers()),
            FakeResponse(200, {"case_id": "case/with-slash", "settlement_tx_id": "TX2"}),
        ]
    )
    client2 = PxkeClient(session=session2, http_client=FakePaymentHTTPClient())

    client2.social_vote("case/with-slash", "uphold")

    retry_call2 = session2.calls[1]
    assert retry_call2.url == f"{BASE_URL}/api/v1/x402/social/cases/case%2Fwith-slash/vote"
    assert retry_call2.json_body == {"verdict": "uphold"}


def test_social_agent_search_sends_comma_joined_interests_as_a_query_param() -> None:
    session = FakeSession(
        [
            FakeResponse(402, headers=_offer_headers()),
            FakeResponse(200, {"agents": [], "query": {}, "settlement_tx_id": "TX1"}),
        ]
    )
    client = PxkeClient(session=session, http_client=FakePaymentHTTPClient())

    client.social_agent_search(["defi", "nft"], limit=10)

    retry_call = session.calls[1]
    assert retry_call.url == f"{BASE_URL}/api/v1/x402/social/agents/search"
    assert retry_call.params == {"interests": "defi,nft", "limit": 10}


# --------------------------------------------------------------------------- #
# Uptime check
# --------------------------------------------------------------------------- #
def test_uptime_check_sends_the_url_in_the_body() -> None:
    session = FakeSession(
        [
            FakeResponse(402, headers=_offer_headers()),
            FakeResponse(200, {"reachable": True, "settlement_tx_id": "TX1"}),
        ]
    )
    client = PxkeClient(session=session, http_client=FakePaymentHTTPClient())

    client.uptime_check("https://example.com")

    retry_call = session.calls[1]
    assert retry_call.url == f"{BASE_URL}/api/v1/x402/uptime/check"
    assert retry_call.json_body == {"url": "https://example.com"}


# --------------------------------------------------------------------------- #
# Agent backup storage
# --------------------------------------------------------------------------- #
def test_storage_create_backup_base64_encodes_data_and_declares_its_size() -> None:
    session = FakeSession(
        [
            FakeResponse(402, headers=_offer_headers()),
            FakeResponse(200, {"backup_id": "b1", "settlement_tx_id": "TX1"}),
        ]
    )
    client = PxkeClient(session=session, http_client=FakePaymentHTTPClient())

    client.storage_create_backup(b"hello world", label="my backup")

    first_call, retry_call = session.calls
    assert first_call.url == f"{BASE_URL}/api/v1/x402/storage/backups"
    assert first_call.params == {"declared_size_bytes": 11}
    assert retry_call.json_body == {"data": "aGVsbG8gd29ybGQ=", "label": "my backup"}


def test_storage_renew_backup_sends_wallet_in_the_body() -> None:
    session = FakeSession(
        [
            FakeResponse(402, headers=_offer_headers()),
            FakeResponse(200, {"backup_id": "b1", "settlement_tx_id": "TX1"}),
        ]
    )
    client = PxkeClient(session=session, http_client=FakePaymentHTTPClient())

    client.storage_renew_backup("b/1", "WALLETADDR")

    retry_call = session.calls[1]
    assert retry_call.url == f"{BASE_URL}/api/v1/x402/storage/backups/b%2F1/renew"
    assert retry_call.json_body == {"wallet": "WALLETADDR"}
