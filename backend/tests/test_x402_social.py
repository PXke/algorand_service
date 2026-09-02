"""x402 social (Phase S0) tests: challenge single-use, payer-is-identity on register, re-register refusal, and the memory store backing everything.

Fully offline. The facilitator is never reached -- paid-route tests
monkeypatch `require_paid_request` directly, the exact same seam
test_x402_directory.py and test_kya_routes.py use, so no real payment gate
or network call happens. Redis is a fake at the get_redis seam(s) used by
session_service.py and app/core/rate_limit.py. The store is the module's own
in-memory backend (InMemorySocialStore) -- no Cassandra involved anywhere.

What is covered (see CLAUDE.md section 6's required regression list):

  S0-1  A signed challenge can be redeemed exactly once -- the Redis GETDEL
        consumes it, so a second verify attempt with the same (still
        technically valid) signature fails.
  S0-2  POST /register's stored wallet is exactly PaymentResult.payer, never
        anything from the request body.
  S0-3  Re-registering an already-registered wallet is refused as
        caller-fault: 409, the settlement headers (proof payment was taken)
        are still attached, mark_fulfilled is NOT called a second time, and
        no refund is attempted (SocialError is a PlatformError, so
        run_with_refund's PlatformError exemption applies).
  S0-4  The full S0 flow -- register, point-read, newest-first directory --
        works end-to-end against the in-memory store, with no network.
  S0-5  An unattributable settled payer is refused via the generic
        run_with_refund exception path (a refund IS attempted there,
        unlike S0-3) rather than silently becoming a stored wallet="".
  S0-6  The full auth round trip (challenge -> session -> bearer token) and
        PATCH /profile actually editing the caller's own profile; a missing
        or invalid bearer token is refused.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Never

import pytest

pytest.importorskip("x402")

from algosdk import account, util
from algosdk.encoding import encode_address
from x402.mechanisms.avm.constants import ALGORAND_TESTNET_CAIP2

from app.core import rate_limit as rate_limit_core
from app.core.http import QueryParams, Request
from app.modules.x402 import circuit_breaker as circuit_breaker_module
from app.modules.x402 import guard as x402_guard
from app.modules.x402 import paid_request as payment_service
from app.modules.x402.refund import RefundResult
from app.modules.x402_social.api import routes as social_routes
from app.modules.x402_social.models.domain import MAX_BIO_LEN, MAX_INTERESTS, SocialError
from app.modules.x402_social.services import session_service
from app.modules.x402_social.services.profile_service import ProfileService, validate_profile_fields
from app.modules.x402_social.stores.memory import InMemorySocialStore

_PAYER = encode_address(bytes([2]) + bytes(31))
_OTHER_PAYER = encode_address(bytes([3]) + bytes(31))


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class _FakeRedis:
    """Enough of the Redis API for challenges, sessions, and rate-limit counters."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.expires: dict[str, int] = {}

    def setex(self, key: str, time: int, value: str) -> bool:
        self.store[key] = value
        self.expires[key] = time
        return True

    def getdel(self, key: str) -> str | None:
        return self.store.pop(key, None)

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def incr(self, key: str) -> int:
        value = int(self.store.get(key, "0")) + 1
        self.store[key] = str(value)
        return value

    def expire(self, key: str, seconds: int) -> bool:
        self.expires[key] = seconds
        return True


class _BrokenRedis:
    """Every operation fails, to exercise the fail-open / fail-closed paths."""

    def setex(self, *_args: object, **_kwargs: object) -> Never:
        raise ConnectionError("redis down")

    def getdel(self, *_args: object, **_kwargs: object) -> Never:
        raise ConnectionError("redis down")

    def get(self, *_args: object, **_kwargs: object) -> Never:
        raise ConnectionError("redis down")

    def incr(self, *_args: object, **_kwargs: object) -> Never:
        raise ConnectionError("redis down")

    def expire(self, *_args: object, **_kwargs: object) -> Never:
        raise ConnectionError("redis down")


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    """Swap every Redis seam this module (and the shared paid-route machinery it calls through) touches for one in-process fake.

    circuit_breaker's own get_redis is included because x402_social_register
    checks circuit_breaker.is_tripped BEFORE the payment gate, and that check
    fails CLOSED (refuses the request) on an unreachable Redis -- see its own
    docstring -- so every register-route test needs this fixture, not just
    ones that exercise rate limiting directly.
    """
    client = _FakeRedis()
    monkeypatch.setattr(session_service, "get_redis", lambda: client)
    monkeypatch.setattr(rate_limit_core, "get_redis", lambda: client)
    monkeypatch.setattr(circuit_breaker_module, "get_redis", lambda: client)
    return client


@pytest.fixture
def store() -> InMemorySocialStore:
    """A fresh in-memory social store per test."""
    return InMemorySocialStore()


def _settled_result(*, payer: str = _PAYER, txid: str = "TX-SOC-1") -> x402_guard.PaymentResult:
    return x402_guard.PaymentResult(
        error=None,
        payer=payer,
        settlement_headers={"PAYMENT-RESPONSE": "ok"},
        amount_atomic="100000",
        payment_txid=txid,
        asset_id="10458941",
        network=ALGORAND_TESTNET_CAIP2,
    )


def _request(
    *,
    method: str = "POST",
    body: bytes = b"",
    headers: dict[str, str] | None = None,
    query: dict[str, Any] | None = None,
    path_params: dict[str, str] | None = None,
    path: str = "/api/v1/x402/social/register",
) -> Request:
    return Request(
        method=method,
        headers=headers or {},
        query_params=QueryParams(query or {}),
        path_params=path_params or {},
        body=body,
        url=SimpleNamespace(scheme="http", host="localhost", path=path),
    )


# --------------------------------------------------------------------------- #
# S0-1: challenge single-use
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis")
def test_challenge_is_single_use() -> None:
    """A signed challenge verifies once; a second attempt with the SAME (still valid) signature fails because Redis GETDEL already consumed the nonce."""
    sk, addr = account.generate_account()
    challenge = session_service.issue_challenge(addr)
    sig = util.sign_bytes(challenge.signing_message.encode(), sk)

    first = session_service.verify_challenge_signature(
        wallet=addr, nonce=challenge.nonce, proof_method="signed_bytes", signature_b64=sig
    )
    assert first is True

    second = session_service.verify_challenge_signature(
        wallet=addr, nonce=challenge.nonce, proof_method="signed_bytes", signature_b64=sig
    )
    assert second is False


@pytest.mark.usefixtures("fake_redis")
def test_challenge_verification_fails_for_a_wrong_nonce() -> None:
    """A technically-valid signature over a DIFFERENT nonce than the one issued is rejected, not silently accepted."""
    sk, addr = account.generate_account()
    session_service.issue_challenge(addr)
    sig = util.sign_bytes(b"not the real signing message", sk)

    assert (
        session_service.verify_challenge_signature(
            wallet=addr, nonce="wrong-nonce", proof_method="signed_bytes", signature_b64=sig
        )
        is False
    )


def test_challenge_issuance_fails_closed_when_redis_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """issue_challenge raises rather than handing out a challenge it can never later verify."""
    monkeypatch.setattr(session_service, "get_redis", lambda: _BrokenRedis())
    with pytest.raises(session_service.SessionStoreError):
        session_service.issue_challenge(_PAYER)


# --------------------------------------------------------------------------- #
# S0-2 / S0-3: payer-is-identity, re-register refusal
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis")
def test_register_identity_is_the_payer_not_a_body_field(
    store: InMemorySocialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The registered wallet is exactly PaymentResult.payer; there is no wallet field on the request body to spoof in the first place."""
    monkeypatch.setattr(social_routes, "profile_service", ProfileService(store))
    monkeypatch.setattr(
        social_routes, "require_paid_request", lambda *_a, **_kw: _settled_result(payer=_PAYER)
    )
    fulfilled: list[tuple[str, str]] = []
    monkeypatch.setattr(
        social_routes,
        "mark_fulfilled",
        lambda txid, *, resource: fulfilled.append((txid, resource)),
    )

    response = social_routes.x402_social_register(
        _request(body=b'{"name": "AlgoScout", "bio": "hi"}')
    )

    assert response.status_code == 200
    body = json.loads(response.description)
    assert body["profile"]["wallet"] == _PAYER
    assert body["settlement_tx_id"] == "TX-SOC-1"
    assert store.get_agent(_PAYER) is not None
    assert store.get_agent(_OTHER_PAYER) is None
    assert fulfilled == [("TX-SOC-1", "x402-social-register")]


@pytest.mark.usefixtures("fake_redis")
def test_reregister_is_caller_fault_payment_kept_no_refund_attempted(
    store: InMemorySocialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-registering an already-registered wallet settles but is refused (409): the original profile survives untouched, mark_fulfilled is never called a second time, and -- because SocialError is a PlatformError -- run_with_refund's PlatformError exemption means send_refund is never invoked at all."""
    monkeypatch.setattr(social_routes, "profile_service", ProfileService(store))
    fulfilled: list[tuple[str, str]] = []
    monkeypatch.setattr(
        social_routes,
        "mark_fulfilled",
        lambda txid, *, resource: fulfilled.append((txid, resource)),
    )
    refund_calls: list[dict[str, object]] = []
    monkeypatch.setattr(payment_service, "send_refund", lambda **kw: refund_calls.append(kw))

    monkeypatch.setattr(
        social_routes,
        "require_paid_request",
        lambda *_a, **_kw: _settled_result(payer=_PAYER, txid="TX-SOC-1"),
    )
    first = social_routes.x402_social_register(_request(body=b'{"name": "AlgoScout"}'))
    assert first.status_code == 200

    monkeypatch.setattr(
        social_routes,
        "require_paid_request",
        lambda *_a, **_kw: _settled_result(payer=_PAYER, txid="TX-SOC-2"),
    )
    second = social_routes.x402_social_register(_request(body=b'{"name": "AlgoScout Two"}'))

    assert second.status_code == 409
    body = json.loads(second.description)
    assert body["error"]["code"] == "wallet_already_registered"
    # Settlement headers (the receipt) are still attached -- the second
    # payment WAS settled on-chain, only the product write was refused.
    assert second.headers.get("PAYMENT-RESPONSE") == "ok"
    # mark_fulfilled ran for the first (successful) registration only.
    assert fulfilled == [("TX-SOC-1", "x402-social-register")]
    # PlatformError exemption: no refund attempt at all for the second payment.
    assert refund_calls == []
    # The original profile's fields are untouched by the refused re-register.
    assert store.get_agent(_PAYER).name == "AlgoScout"


@pytest.mark.usefixtures("fake_redis")
def test_register_rejects_an_unattributable_payer_and_attempts_a_refund(
    store: InMemorySocialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A settled payment with no resolvable payer address must not silently become a stored wallet="" (CLAUDE.md section 2 invariant 8) -- it goes through run_with_refund's GENERIC exception path (a real refund attempt), not the PlatformError one."""
    monkeypatch.setattr(social_routes, "profile_service", ProfileService(store))
    monkeypatch.setattr(
        social_routes, "require_paid_request", lambda *_a, **_kw: _settled_result(payer="")
    )
    refund_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        payment_service,
        "send_refund",
        lambda **kw: refund_calls.append(kw) or RefundResult(status="skipped", error="test stub"),
    )
    fulfilled: list[tuple[str, str]] = []
    monkeypatch.setattr(
        social_routes,
        "mark_fulfilled",
        lambda txid, *, resource: fulfilled.append((txid, resource)),
    )

    response = social_routes.x402_social_register(_request(body=b'{"name": "Ghost"}'))

    assert response.status_code == 503
    assert len(refund_calls) == 1
    assert fulfilled == []
    assert store.list_recent_agents(limit=10) == []


# --------------------------------------------------------------------------- #
# S0-4: memory store backs the whole S0 flow, no network
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis")
def test_memory_store_backs_register_get_and_list_with_no_network(
    store: InMemorySocialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Register -> point-read -> newest-first directory all work purely against InMemorySocialStore; the directory projection is deliberately thin (bio is not carried)."""
    monkeypatch.setattr(social_routes, "profile_service", ProfileService(store))
    monkeypatch.setattr(
        social_routes,
        "require_paid_request",
        lambda *_a, **_kw: _settled_result(payer=_PAYER, txid="TX-A"),
    )
    monkeypatch.setattr(social_routes, "mark_fulfilled", lambda *_a, **_kw: None)

    social_routes.x402_social_register(
        _request(body=b'{"name": "AlgoScout", "bio": "hi", "mission": "scout stuff"}')
    )

    detail = social_routes.x402_social_agent_detail(
        _request(method="GET", path_params={"wallet": _PAYER})
    )
    assert detail["profile"]["wallet"] == _PAYER
    assert detail["profile"]["bio"] == "hi"

    listing = social_routes.x402_social_agents_list(_request(method="GET"))
    assert [a["wallet"] for a in listing["agents"]] == [_PAYER]
    assert listing["agents"][0]["mission"] == "scout stuff"
    # Thin browse-feed projection: bio/location/interests are not carried.
    assert listing["agents"][0]["bio"] == ""

    missing = social_routes.x402_social_agent_detail(
        _request(method="GET", path_params={"wallet": _OTHER_PAYER})
    )
    assert missing.status_code == 404


# --------------------------------------------------------------------------- #
# S0-6: auth round trip + PATCH /profile
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis")
def test_auth_round_trip_then_patch_profile(
    store: InMemorySocialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /auth/challenge -> POST /auth/session yields a bearer token that PATCH /profile accepts, and the edit lands on the correct wallet's profile only."""
    monkeypatch.setattr(social_routes, "profile_service", ProfileService(store))
    monkeypatch.setattr(
        social_routes,
        "require_paid_request",
        lambda *_a, **_kw: _settled_result(payer=_PAYER, txid="TX-A"),
    )
    monkeypatch.setattr(social_routes, "mark_fulfilled", lambda *_a, **_kw: None)
    social_routes.x402_social_register(_request(body=b'{"name": "AlgoScout", "bio": "hi"}'))

    sk, addr = account.generate_account()
    # Register the SIGNING wallet too, since PATCH /profile requires an
    # existing profile to edit.
    monkeypatch.setattr(
        social_routes,
        "require_paid_request",
        lambda *_a, **_kw: _settled_result(payer=addr, txid="TX-B"),
    )
    social_routes.x402_social_register(_request(body=b'{"name": "Second Agent"}'))

    challenge_resp = social_routes.x402_social_auth_challenge(
        _request(
            method="POST",
            path="/api/v1/x402/social/auth/challenge",
            body=json.dumps({"wallet": addr}).encode(),
        )
    )
    assert isinstance(challenge_resp, dict)
    sig = util.sign_bytes(challenge_resp["signing_message"].encode(), sk)

    session_resp = social_routes.x402_social_auth_session(
        _request(
            method="POST",
            path="/api/v1/x402/social/auth/session",
            body=json.dumps(
                {
                    "wallet": addr,
                    "nonce": challenge_resp["nonce"],
                    "proof_method": "signed_bytes",
                    "signature_b64": sig,
                }
            ).encode(),
        )
    )
    assert isinstance(session_resp, dict)
    token = session_resp["token"]
    assert token

    patched = social_routes.x402_social_profile_patch(
        _request(
            method="PATCH",
            path="/api/v1/x402/social/profile",
            headers={"authorization": f"Bearer {token}"},
            body=b'{"bio": "updated bio"}',
        )
    )
    assert isinstance(patched, dict)
    assert patched["profile"]["bio"] == "updated bio"
    assert patched["profile"]["name"] == "Second Agent"

    # The OTHER agent's profile is untouched.
    assert store.get_agent(_PAYER).bio == "hi"


@pytest.mark.usefixtures("fake_redis")
def test_profile_patch_requires_a_valid_session(
    store: InMemorySocialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No Authorization header, and a bogus bearer token, are both refused with 401 -- never silently editing anyone's profile."""
    monkeypatch.setattr(social_routes, "profile_service", ProfileService(store))

    no_header = social_routes.x402_social_profile_patch(
        _request(method="PATCH", path="/api/v1/x402/social/profile", body=b'{"bio": "x"}')
    )
    assert no_header.status_code == 401

    bogus_token = social_routes.x402_social_profile_patch(
        _request(
            method="PATCH",
            path="/api/v1/x402/social/profile",
            headers={"authorization": "Bearer not-a-real-token"},
            body=b'{"bio": "x"}',
        )
    )
    assert bogus_token.status_code == 401


# --------------------------------------------------------------------------- #
# Field validation
# --------------------------------------------------------------------------- #
def test_validate_profile_fields_rejects_an_oversized_bio() -> None:
    """A bio over MAX_BIO_LEN is a SocialError the route maps to a 400, never silently truncated."""
    with pytest.raises(SocialError) as exc_info:
        validate_profile_fields(
            name="AlgoScout",
            bio="x" * (MAX_BIO_LEN + 1),
            mission="",
            location="",
            interests=[],
            emoji="",
        )
    assert exc_info.value.http_status == 400


def test_validate_profile_fields_rejects_too_many_interests() -> None:
    """More than MAX_INTERESTS items is a SocialError, never silently clipped to the cap."""
    with pytest.raises(SocialError):
        validate_profile_fields(
            name="AlgoScout",
            bio="",
            mission="",
            location="",
            interests=[f"topic{i}" for i in range(MAX_INTERESTS + 1)],
            emoji="",
        )
