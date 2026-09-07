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
import threading
import uuid as uuid_module
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Never
from unittest.mock import patch

import pytest
from conftest import patch_cassandra

pytest.importorskip("x402")

from algosdk import account, util
from algosdk.encoding import encode_address
from x402.extensions.bazaar import bazaar_resource_server_extension, validate_discovery_extension
from x402.mechanisms.avm.constants import ALGORAND_MAINNET_CAIP2, ALGORAND_TESTNET_CAIP2

from app.core import rate_limit as rate_limit_core
from app.core.config import settings
from app.core.http import QueryParams, Request, Response
from app.core.statements import X402SocialStmts
from app.modules.admin.api import routes as admin_routes
from app.modules.x402 import circuit_breaker as circuit_breaker_module
from app.modules.x402 import guard as x402_guard
from app.modules.x402 import paid_request as payment_service
from app.modules.x402.refund import RefundResult
from app.modules.x402.settlement import (
    EUR_VALUE_UNAVAILABLE,
    InMemorySettlementStore,
    SettlementRecord,
    set_settlement_store,
)
from app.modules.x402_social.api import routes as social_routes
from app.modules.x402_social.models.domain import (
    CASE_STATE_REJECTED,
    CASE_STATE_UPHELD,
    CATEGORY_ILLEGAL_CONTENT,
    CATEGORY_NOT_HELPFUL,
    GROUP_ROLE_MEMBER,
    HARD_DELETE_SNAPSHOT_PLACEHOLDER,
    LEADERBOARD_MAX_LIMIT,
    MAX_BIO_LEN,
    MAX_INTERESTS,
    REACTION_UP,
    REMOVED_BY_ADMIN_LEVER,
    REMOVED_BY_COMMUNITY_VOTE,
    TARGET_AGENT,
    TARGET_GROUP,
    TARGET_POST,
    ReactionTotals,
    SocialError,
    StoredCase,
    StoredDmConversation,
    StoredDmMessage,
    StoredGroup,
    StoredStanding,
    compute_ban_seconds,
    compute_report_cooldown_seconds,
)
from app.modules.x402_social.services import (
    leaderboard_service,
    prose,
    session_service,
    trending_service,
)
from app.modules.x402_social.services import rate_limit as social_rate_limit
from app.modules.x402_social.services.dm_service import DmService, conversation_id_for
from app.modules.x402_social.services.graph_service import GraphService
from app.modules.x402_social.services.group_service import GroupService
from app.modules.x402_social.services.markdown_guard import validate_markdown_body
from app.modules.x402_social.services.moderation_service import ModerationService
from app.modules.x402_social.services.post_service import PostService, _new_post_or_comment_id
from app.modules.x402_social.services.profile_service import ProfileService, validate_profile_fields
from app.modules.x402_social.stores import cassandra as social_cassandra_store
from app.modules.x402_social.stores.cassandra import CassandraSocialStore
from app.modules.x402_social.stores.memory import InMemorySocialStore

_PAYER = encode_address(bytes([2]) + bytes(31))
_OTHER_PAYER = encode_address(bytes([3]) + bytes(31))
_REPORTER = encode_address(bytes([4]) + bytes(31))
_VOTER_A = encode_address(bytes([5]) + bytes(31))
_VOTER_B = encode_address(bytes([6]) + bytes(31))
_VOTER_C = encode_address(bytes([7]) + bytes(31))
_VOTER_D = encode_address(bytes([8]) + bytes(31))
_PROBE_PAYER = encode_address(bytes([9]) + bytes(31))

_RESOURCE_BY_ROUTE = {
    "x402_social_register": social_routes._REGISTER_RESOURCE,
    "x402_social_post_create": social_routes._POST_RESOURCE,
    "x402_social_comment_create": social_routes._COMMENT_RESOURCE,
    "x402_social_react": social_routes._REACT_RESOURCE,
    "x402_social_follow": social_routes._FOLLOW_RESOURCE,
    "x402_social_group_create": social_routes._GROUP_CREATE_RESOURCE,
    "x402_social_group_join": social_routes._GROUP_JOIN_RESOURCE,
}


def _always_registered(_wallet: str) -> bool:
    """is_registered stub for tests that are not themselves exercising finding 4's registration gate -- every wallet is treated as already registered."""
    return True


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class _FakeRedis:
    """Enough of the Redis API for challenges, sessions, rate-limit counters, and (S1) trending's sorted sets."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.expires: dict[str, int] = {}
        self.zsets: dict[str, dict[str, float]] = {}

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

    def zincrby(self, key: str, amount: float, member: str) -> float:
        zset = self.zsets.setdefault(key, {})
        zset[member] = zset.get(member, 0.0) + amount
        return zset[member]

    def zrange(
        self, key: str, start: int, end: int, withscores: bool = False
    ) -> list[tuple[str, float]] | list[str]:
        items = sorted(self.zsets.get(key, {}).items(), key=lambda kv: kv[1])
        sliced = items[start:] if end == -1 else items[start : end + 1]
        return sliced if withscores else [member for member, _score in sliced]

    def zrevrange(
        self, key: str, start: int, end: int, withscores: bool = False
    ) -> list[tuple[str, float]] | list[str]:
        """Highest-score-first, the direction trending_service._merge_decayed now reads with (finding 7, 2026-security-audit)."""
        items = sorted(self.zsets.get(key, {}).items(), key=lambda kv: -kv[1])
        sliced = items[start:] if end == -1 else items[start : end + 1]
        return sliced if withscores else [member for member, _score in sliced]

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self)


class _FakePipeline:
    """Enough of a Redis pipeline for trending_service's zincrby+expire+execute batches -- queues ops, applies them on execute()."""

    def __init__(self, client: _FakeRedis) -> None:
        self._client = client
        self._ops: list[tuple] = []

    def zincrby(self, key: str, amount: float, member: str) -> _FakePipeline:
        self._ops.append(("zincrby", key, amount, member))
        return self

    def expire(self, key: str, seconds: int) -> _FakePipeline:
        self._ops.append(("expire", key, seconds))
        return self

    def zrevrange(self, key: str, start: int, end: int, withscores: bool = False) -> _FakePipeline:
        """Queued for trending_service._merge_decayed's now-pipelined bucket reads (optimization pass, 2026-09-02)."""
        self._ops.append(("zrevrange", key, start, end, withscores))
        return self

    def execute(self) -> list[object]:
        results: list[object] = []
        for op in self._ops:
            if op[0] == "zincrby":
                results.append(self._client.zincrby(op[1], op[2], op[3]))
            elif op[0] == "expire":
                results.append(self._client.expire(op[1], op[2]))
            elif op[0] == "zrevrange":
                results.append(self._client.zrevrange(op[1], op[2], op[3], withscores=op[4]))
        self._ops = []
        return results


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


@pytest.fixture(autouse=True)
def _already_paid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the pre-parse 402 for header-less requests: every route test here models a request that already carries a payment (the gate is stubbed, or run against the offline facilitator), so the unpaid challenge is out of scope. Its ordering has its own tests in tests/test_x402_unpaid_challenge.py."""
    monkeypatch.setattr(social_routes, "challenge_if_unpaid", lambda *_a, **_kw: None)


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
    monkeypatch.setattr(trending_service, "get_redis", lambda: client)
    return client


@pytest.fixture
def store() -> InMemorySocialStore:
    """A fresh in-memory social store per test."""
    return InMemorySocialStore()


@pytest.fixture
def ledger() -> Iterator[InMemorySettlementStore]:
    """The shared in-memory settlement ledger, installed process-wide and torn down.

    Same fixture shape as test_x402_grading.py's own `ledger` -- installed at
    the shared seam rather than handed to leaderboard_service directly, so a
    test exercises the same modules.x402.settlement.get_settlement_store()
    wiring the real leaderboard route (and every paid route's
    record_settlement call) goes through.
    """
    settlement_store = InMemorySettlementStore()
    set_settlement_store(settlement_store)
    yield settlement_store
    set_settlement_store(None)


def _settled(
    ledger_: InMemorySettlementStore,
    *,
    payer: str,
    eur_value: float,
    tx_id: str,
    network: str = ALGORAND_TESTNET_CAIP2,
    resource: str = "x402-directory-list",
) -> None:
    """Record one real settlement directly on the ledger, with an explicit eur_value (bypassing the price oracle entirely -- these tests are exercising leaderboard_service's aggregation, not price_oracle)."""
    ledger_.record_settlement(
        SettlementRecord(
            tx_id=tx_id,
            asset_id="10458941",
            amount_atomic="1000000",
            payer=payer,
            resource=resource,
            network=network,
            settled_at_epoch=int(datetime.now(tz=UTC).timestamp()),
            eur_value=eur_value,
        )
    )


def _settled_result(
    *, payer: str = _PAYER, txid: str = "TX-SOC-1", is_promo: bool = False
) -> x402_guard.PaymentResult:
    return x402_guard.PaymentResult(
        error=None,
        payer=payer,
        settlement_headers={"PAYMENT-RESPONSE": "ok"},
        amount_atomic="100000",
        payment_txid=txid,
        asset_id="10458941",
        network=ALGORAND_TESTNET_CAIP2,
        is_promo=is_promo,
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


# =========================================================================== #
# Phase S1: the network (posts, comments, reactions, follows, groups,
# trending, dual output format). See CLAUDE.md section 6's required
# regression list -- this section covers all five named there.
# =========================================================================== #


# --------------------------------------------------------------------------- #
# markdown_guard: HTML is rejected, not stripped
# --------------------------------------------------------------------------- #
def test_markdown_guard_rejects_embedded_html_rather_than_stripping_it() -> None:
    """A <script> tag is refused outright (SocialError), never silently removed -- the design doc's own reasoning: stripping would change what the author paid to say without telling them."""
    with pytest.raises(SocialError) as exc_info:
        validate_markdown_body("hello <script>alert(1)</script> world", max_bytes=1000)
    assert exc_info.value.code == "embedded_html_rejected"
    assert exc_info.value.http_status == 400


def test_markdown_guard_rejects_a_raw_img_tag() -> None:
    """A raw <img src=...> is rejected the same way a <script> is."""
    with pytest.raises(SocialError):
        validate_markdown_body('<img src="x" onerror="evil()">', max_bytes=1000)


def test_markdown_guard_allows_a_commonmark_autolink() -> None:
    """<https://example.com> is legitimate markdown, not embedded HTML, and must NOT be rejected."""
    body = validate_markdown_body("see <https://example.com> for details", max_bytes=1000)
    assert body == "see <https://example.com> for details"


def test_markdown_guard_rejects_oversized_body() -> None:
    """A body over max_bytes is refused, never silently truncated."""
    with pytest.raises(SocialError) as exc_info:
        validate_markdown_body("x" * 100, max_bytes=10)
    assert exc_info.value.http_status == 400


def test_markdown_guard_rejects_empty_body() -> None:
    """An empty (or whitespace-only) body is refused."""
    with pytest.raises(SocialError):
        validate_markdown_body("   ", max_bytes=1000)


# --------------------------------------------------------------------------- #
# post_service: create / read / delete / comments
# --------------------------------------------------------------------------- #
def test_post_create_get_and_delete_round_trip(store: InMemorySocialStore) -> None:
    """A post is stored, readable by id, listed on the author's feed, and deletable by its author (tombstone, not a row delete)."""
    service = PostService(store, is_registered=_always_registered)
    post = service.create(
        author=_PAYER,
        body_md="hello world",
        tags=["Defi", "defi", " liquidity "],
        group_id="",
        settlement_tx_id="TX-P1",
    )
    # normalize_tags: trimmed, lowercased, de-duplicated, order preserved.
    assert post.tags == ["defi", "liquidity"]

    fetched = service.get(post.post_id)
    assert fetched is not None
    assert fetched.body_md == "hello world"

    author_feed = service.list_by_author(_PAYER, limit=10)
    assert [p.post_id for p in author_feed] == [post.post_id]

    deleted = service.delete(post.post_id, wallet=_PAYER)
    assert deleted.deleted is True
    # Still resolvable by id -- never a row delete.
    assert service.get(post.post_id).deleted is True

    # Idempotent: deleting again is a no-op success, not an error.
    again = service.delete(post.post_id, wallet=_PAYER)
    assert again.deleted is True


def test_post_delete_refuses_a_non_author(store: InMemorySocialStore) -> None:
    """Only the author of a post may delete it."""
    service = PostService(store, is_registered=_always_registered)
    post = service.create(
        author=_PAYER, body_md="mine", tags=[], group_id="", settlement_tx_id="TX-P2"
    )
    with pytest.raises(SocialError) as exc_info:
        service.delete(post.post_id, wallet=_OTHER_PAYER)
    assert exc_info.value.http_status == 403


def test_post_create_in_a_group_requires_membership(store: InMemorySocialStore) -> None:
    """A post into a group the author has not joined is refused -- caller-fault (design doc section 2.2/4.1: this can only be checked AFTER the payment gate, since the author is the settled payer)."""
    service = PostService(
        store,
        membership_lookup=lambda _group_id, _wallet: False,
        is_registered=_always_registered,
    )
    with pytest.raises(SocialError) as exc_info:
        service.create(
            author=_PAYER, body_md="hi", tags=[], group_id="some-group", settlement_tx_id="TX-P3"
        )
    assert exc_info.value.code == "not_group_member"
    assert exc_info.value.http_status == 403


def test_comment_create_and_list(store: InMemorySocialStore) -> None:
    """A comment is appended to a post's thread, oldest-first, and counted."""
    service = PostService(store, is_registered=_always_registered)
    post = service.create(
        author=_PAYER, body_md="topic", tags=[], group_id="", settlement_tx_id="TX-P4"
    )
    service.add_comment(
        post_id=post.post_id, author=_OTHER_PAYER, body_md="first", settlement_tx_id="TX-C1"
    )
    service.add_comment(
        post_id=post.post_id, author=_PAYER, body_md="second", settlement_tx_id="TX-C2"
    )
    comments = service.list_comments(post.post_id, limit=10)
    assert [c.body_md for c in comments] == ["first", "second"]
    count, truncated = service.comment_count(post.post_id)
    assert count == 2
    assert truncated is False


# --------------------------------------------------------------------------- #
# S1-A: reactions -- LWT-then-counter race safety (CLAUDE.md section 6)
# --------------------------------------------------------------------------- #
def test_second_reaction_from_same_wallet_is_refused_caller_fault(
    store: InMemorySocialStore,
) -> None:
    """A second reaction from the same wallet on the same post is refused (409), never a second counter increment."""
    service = PostService(store, is_registered=_always_registered)
    post = service.create(
        author=_PAYER, body_md="hi", tags=[], group_id="", settlement_tx_id="TX-P5"
    )
    totals = service.react(
        post_id=post.post_id, wallet=_OTHER_PAYER, value=REACTION_UP, settlement_tx_id="TX-R1"
    )
    assert totals.up == 1

    with pytest.raises(SocialError) as exc_info:
        service.react(
            post_id=post.post_id, wallet=_OTHER_PAYER, value=REACTION_UP, settlement_tx_id="TX-R2"
        )
    assert exc_info.value.code == "already_reacted"
    assert exc_info.value.http_status == 409

    # The counter was NOT bumped a second time.
    assert service.reaction_totals(post.post_id).up == 1


def test_concurrent_reactions_from_the_same_wallet_produce_exactly_one_counter_increment(
    store: InMemorySocialStore,
) -> None:
    """Two threads racing POST /react for the SAME wallet on the SAME post: the LWT wins exactly once, so the counter is incremented exactly once and every other attempt is refused, never silently dropped or double-counted."""
    service = PostService(store, is_registered=_always_registered)
    post = service.create(
        author=_PAYER, body_md="race me", tags=[], group_id="", settlement_tx_id="TX-P6"
    )

    outcomes: list[str] = []
    lock = threading.Lock()

    def attempt(i: int) -> None:
        try:
            service.react(
                post_id=post.post_id,
                wallet=_OTHER_PAYER,
                value=REACTION_UP,
                settlement_tx_id=f"TX-RACE-{i}",
            )
            with lock:
                outcomes.append("won")
        except SocialError:
            with lock:
                outcomes.append("refused")

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert outcomes.count("won") == 1
    assert outcomes.count("refused") == 11
    totals = service.reaction_totals(post.post_id)
    assert totals.up == 1
    assert totals.down == 0


# --------------------------------------------------------------------------- #
# graph_service: follow / unfollow / friends
# --------------------------------------------------------------------------- #
def test_follow_unfollow_and_mutual_friends(store: InMemorySocialStore) -> None:
    """A -> follow -> B is one-directional until B follows A back; friends() is exactly the mutual intersection."""
    service = GraphService(store, is_registered=_always_registered)
    service.follow(follower=_PAYER, followee=_OTHER_PAYER)

    assert [e.wallet for e in service.following(_PAYER, limit=10)] == [_OTHER_PAYER]
    assert [e.wallet for e in service.followers(_OTHER_PAYER, limit=10)] == [_PAYER]
    assert service.friends(_PAYER, limit=10) == []
    assert service.friends(_OTHER_PAYER, limit=10) == []

    service.follow(follower=_OTHER_PAYER, followee=_PAYER)
    assert [e.wallet for e in service.friends(_PAYER, limit=10)] == [_OTHER_PAYER]
    assert [e.wallet for e in service.friends(_OTHER_PAYER, limit=10)] == [_PAYER]

    service.unfollow(follower=_PAYER, followee=_OTHER_PAYER)
    assert service.following(_PAYER, limit=10) == []
    # No longer mutual, so no longer friends either.
    assert service.friends(_OTHER_PAYER, limit=10) == []


def test_unfollow_a_non_existent_edge_is_a_no_op(store: InMemorySocialStore) -> None:
    """DELETE follow on an edge that was never created just succeeds quietly (free route, nothing to refuse)."""
    service = GraphService(store, is_registered=_always_registered)
    service.unfollow(follower=_PAYER, followee=_OTHER_PAYER)  # must not raise


def test_refollow_is_idempotent_not_caller_fault(store: InMemorySocialStore) -> None:
    """Unlike a reaction, following an already-followed wallet a second time is NOT refused (design doc section 2.4)."""
    service = GraphService(store, is_registered=_always_registered)
    service.follow(follower=_PAYER, followee=_OTHER_PAYER)
    service.follow(follower=_PAYER, followee=_OTHER_PAYER)  # must not raise
    assert [e.wallet for e in service.following(_PAYER, limit=10)] == [_OTHER_PAYER]


# --------------------------------------------------------------------------- #
# S1-B: group-name collision is caller-fault, payment kept, no refund
# (mirrors S0-3's re-registration test shape exactly)
# --------------------------------------------------------------------------- #
def test_group_service_name_collision_is_caller_fault(store: InMemorySocialStore) -> None:
    """The SECOND wallet to claim an already-taken group name is refused (409, group_name_taken); the first group is untouched."""
    service = GroupService(store, is_registered=_always_registered)
    first = service.create(
        owner=_PAYER, name="defi-signals", description="first", settlement_tx_id="TX-G1"
    )

    with pytest.raises(SocialError) as exc_info:
        service.create(
            owner=_OTHER_PAYER, name="DeFi-Signals", description="second", settlement_tx_id="TX-G2"
        )
    assert exc_info.value.code == "group_name_taken"
    assert exc_info.value.http_status == 409

    # The original group survives untouched.
    assert store.get_group(first.group_id).owner == _PAYER
    assert store.get_group(first.group_id).description == "first"


@pytest.mark.usefixtures("fake_redis")
def test_group_create_route_name_collision_settles_but_is_refused_payment_kept_no_refund_attempted(
    store: InMemorySocialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Route-level mirror of S0-3: two settled group-create payments for the same name -- the second is refused (409), mark_fulfilled runs only for the first, and NO refund is attempted (SocialError is a PlatformError, exempted from run_with_refund's refund path)."""
    monkeypatch.setattr(
        social_routes, "group_service", GroupService(store, is_registered=_always_registered)
    )
    fulfilled: list[tuple[str, str]] = []
    monkeypatch.setattr(
        social_routes,
        "mark_fulfilled",
        lambda txid, *, resource: fulfilled.append((txid, resource)),
    )
    refund_calls: list[dict[str, object]] = []
    monkeypatch.setattr(payment_service, "send_refund", lambda **kw: refund_calls.append(kw))

    body = json.dumps({"name": "defi-signals", "description": "signals"}).encode()

    monkeypatch.setattr(
        social_routes,
        "require_paid_request",
        lambda *_a, **_kw: _settled_result(payer=_PAYER, txid="TX-G-A"),
    )
    first = social_routes.x402_social_group_create(
        _request(body=body, path="/api/v1/x402/social/groups")
    )
    assert first.status_code == 200
    first_group_id = json.loads(first.description)["group"]["group_id"]

    monkeypatch.setattr(
        social_routes,
        "require_paid_request",
        lambda *_a, **_kw: _settled_result(payer=_OTHER_PAYER, txid="TX-G-B"),
    )
    second = social_routes.x402_social_group_create(
        _request(body=body, path="/api/v1/x402/social/groups")
    )

    assert second.status_code == 409
    body_json = json.loads(second.description)
    assert body_json["error"]["code"] == "group_name_taken"
    # Settlement headers (the receipt) are still attached -- the second
    # payment WAS settled on-chain, only the product write was refused.
    assert second.headers.get("PAYMENT-RESPONSE") == "ok"
    assert fulfilled == [("TX-G-A", "x402-social-group-create")]
    assert refund_calls == []
    # The original group's owner is untouched by the refused second create.
    assert store.get_group(first_group_id).owner == _PAYER


# --------------------------------------------------------------------------- #
# group_service: join / leave / moderators / hide-post / remove-member
# --------------------------------------------------------------------------- #
def test_group_join_leave_and_owner_cannot_leave(store: InMemorySocialStore) -> None:
    """Joining is idempotent, leaving removes membership, and the owner is refused (owner_cannot_leave)."""
    service = GroupService(store, is_registered=_always_registered)
    group = service.create(owner=_PAYER, name="ai-agents", description="", settlement_tx_id="TX-G3")

    membership = service.join(
        group_id=group.group_id, wallet=_OTHER_PAYER, settlement_tx_id="TX-J1"
    )
    assert membership.role == GROUP_ROLE_MEMBER

    # Idempotent: joining again does not change the role.
    again = service.join(group_id=group.group_id, wallet=_OTHER_PAYER, settlement_tx_id="TX-J2")
    assert again.role == GROUP_ROLE_MEMBER

    service.leave(group_id=group.group_id, wallet=_OTHER_PAYER)
    assert service.get_membership(group.group_id, _OTHER_PAYER) is None

    with pytest.raises(SocialError) as exc_info:
        service.leave(group_id=group.group_id, wallet=_PAYER)
    assert exc_info.value.code == "owner_cannot_leave"


def test_group_moderator_promote_and_demote(store: InMemorySocialStore) -> None:
    """The owner can promote a member to moderator and back; a non-owner cannot."""
    service = GroupService(store, is_registered=_always_registered)
    group = service.create(
        owner=_PAYER, name="promo-group", description="", settlement_tx_id="TX-G4"
    )
    service.join(group_id=group.group_id, wallet=_OTHER_PAYER, settlement_tx_id="TX-J3")

    promoted = service.set_moderator(
        group_id=group.group_id, actor_wallet=_PAYER, target_wallet=_OTHER_PAYER
    )
    assert promoted.role == "moderator"

    demoted = service.unset_moderator(
        group_id=group.group_id, actor_wallet=_PAYER, target_wallet=_OTHER_PAYER
    )
    assert demoted.role == GROUP_ROLE_MEMBER

    # Only the owner may promote/demote.
    with pytest.raises(SocialError) as exc_info:
        service.set_moderator(
            group_id=group.group_id, actor_wallet=_OTHER_PAYER, target_wallet=_OTHER_PAYER
        )
    assert exc_info.value.http_status == 403


def test_group_hide_post_is_scoped_to_the_group_feed_only(store: InMemorySocialStore) -> None:
    """An owner hiding a post from their group's feed does NOT delete it or hide it on the author's own feed (design doc section 2.7)."""
    group_service_ = GroupService(store, is_registered=_always_registered)
    post_service_ = PostService(
        store,
        membership_lookup=lambda gid, w: group_service_.is_member(gid, w),
        is_registered=_always_registered,
    )
    group_service_._post_service = post_service_

    group = group_service_.create(
        owner=_PAYER, name="hide-test", description="", settlement_tx_id="TX-G5"
    )
    group_service_.join(group_id=group.group_id, wallet=_OTHER_PAYER, settlement_tx_id="TX-J4")
    post = post_service_.create(
        author=_OTHER_PAYER,
        body_md="spam?",
        tags=[],
        group_id=group.group_id,
        settlement_tx_id="TX-P7",
    )

    hidden = group_service_.hide_post(
        group_id=group.group_id, actor_wallet=_PAYER, post_id=post.post_id
    )
    assert hidden.hidden_group is True

    # Survives on the author's own feed, untouched.
    author_feed = post_service_.list_by_author(_OTHER_PAYER, limit=10)
    assert author_feed[0].hidden_group is False
    assert author_feed[0].deleted is False

    # Excluded from the group's own feed once hidden (route-level filtering
    # mirrors this: see x402_social_group_feed).
    group_feed = post_service_.list_group_feed(group.group_id, limit=10)
    assert group_feed[0].hidden_group is True


def test_group_remove_member_cannot_remove_the_owner(store: InMemorySocialStore) -> None:
    """An owner/moderator can remove a plain member but never the owner."""
    service = GroupService(store, is_registered=_always_registered)
    group = service.create(
        owner=_PAYER, name="remove-test", description="", settlement_tx_id="TX-G6"
    )
    service.join(group_id=group.group_id, wallet=_OTHER_PAYER, settlement_tx_id="TX-J5")

    service.remove_member(group_id=group.group_id, actor_wallet=_PAYER, target_wallet=_OTHER_PAYER)
    assert service.get_membership(group.group_id, _OTHER_PAYER) is None

    with pytest.raises(SocialError) as exc_info:
        service.remove_member(group_id=group.group_id, actor_wallet=_PAYER, target_wallet=_PAYER)
    assert exc_info.value.code == "cannot_remove_owner"


# --------------------------------------------------------------------------- #
# S1-C: home-feed fan-out cap actually truncates and reports truncated_to
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis")
def test_home_feed_route_truncates_and_reports_truncated_to(
    store: InMemorySocialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With feed_fanout_limit set below the caller's actual follow count, GET /feed only scans that many followees and reports truncated_to -- it does not silently pretend the feed is exhaustive."""
    monkeypatch.setattr(
        social_routes, "post_service", PostService(store, is_registered=_always_registered)
    )
    graph = GraphService(store, is_registered=_always_registered)
    monkeypatch.setattr(social_routes, "graph_service", graph)
    monkeypatch.setattr(
        social_routes, "group_service", GroupService(store, is_registered=_always_registered)
    )
    monkeypatch.setattr(settings, "x402_social_feed_fanout_limit", 2)

    caller = _PAYER
    followees = [encode_address(bytes([10 + i]) + bytes(31)) for i in range(4)]
    post_service_ = PostService(store, is_registered=_always_registered)
    for i, followee in enumerate(followees):
        graph.follow(follower=caller, followee=followee)
        post_service_.create(
            author=followee,
            body_md=f"post {i}",
            tags=[],
            group_id="",
            settlement_tx_id=f"TX-HF-{i}",
        )

    token, _expires = social_routes.issue_session_token(caller)
    response = social_routes.x402_social_home_feed(
        _request(
            method="GET",
            path="/api/v1/x402/social/feed",
            headers={"authorization": f"Bearer {token}"},
        )
    )
    assert isinstance(response, dict)
    assert response["truncated_to"] == 2
    # Only feed_fanout_limit (2) of the 4 followees' posts were ever scanned.
    assert len(response["posts"]) <= 2


@pytest.mark.usefixtures("fake_redis")
def test_home_feed_route_reports_no_truncation_when_the_cap_does_not_bite(
    store: InMemorySocialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With feed_fanout_limit comfortably above the caller's actual follow count, truncated_to is None."""
    monkeypatch.setattr(
        social_routes, "post_service", PostService(store, is_registered=_always_registered)
    )
    graph = GraphService(store, is_registered=_always_registered)
    monkeypatch.setattr(social_routes, "graph_service", graph)
    monkeypatch.setattr(
        social_routes, "group_service", GroupService(store, is_registered=_always_registered)
    )
    monkeypatch.setattr(settings, "x402_social_feed_fanout_limit", 50)

    caller = _PAYER
    followee = _OTHER_PAYER
    graph.follow(follower=caller, followee=followee)
    PostService(store, is_registered=_always_registered).create(
        author=followee, body_md="only post", tags=[], group_id="", settlement_tx_id="TX-HF-X"
    )

    token, _expires = social_routes.issue_session_token(caller)
    response = social_routes.x402_social_home_feed(
        _request(
            method="GET",
            path="/api/v1/x402/social/feed",
            headers={"authorization": f"Bearer {token}"},
        )
    )
    assert isinstance(response, dict)
    assert response["truncated_to"] is None
    assert len(response["posts"]) == 1


# --------------------------------------------------------------------------- #
# A3 (2026-09-03): GET /posts/{id}/comments must treat a deleted or
# platform-hidden post as not-found, the SAME "deleted/hidden_platform ==
# not found" contract every other free read on a post already applies.
# --------------------------------------------------------------------------- #
def test_comment_list_is_not_found_for_a_deleted_post(
    store: InMemorySocialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /posts/{id}/comments used to only check `post is None`, unlike post-detail/comment-create/react -- a deleted post's comments kept serving here even though the post itself was gone from every other read surface."""
    post_service_ = PostService(store, is_registered=_always_registered)
    monkeypatch.setattr(social_routes, "post_service", post_service_)

    post = post_service_.create(
        author=_OTHER_PAYER,
        body_md="will be deleted",
        tags=[],
        group_id="",
        settlement_tx_id="TX-D1",
    )
    post_service_.add_comment(
        post_id=post.post_id, author=_PAYER, body_md="a comment", settlement_tx_id="TX-D2"
    )
    post_service_.delete(post.post_id, wallet=_OTHER_PAYER)

    response = social_routes.x402_social_comment_list(
        _request(method="GET", path_params={"post_id": post.post_id})
    )
    assert isinstance(response, Response)
    assert response.status_code == 404
    body = json.loads(response.description)
    assert body["error"]["code"] == "not_found"


def test_comment_list_is_not_found_for_a_platform_hidden_post(
    store: InMemorySocialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same contract for hidden_platform (an S2 upheld-case tombstone) as for `deleted`."""
    post_service_ = PostService(store, is_registered=_always_registered)
    monkeypatch.setattr(social_routes, "post_service", post_service_)

    post = post_service_.create(
        author=_OTHER_PAYER,
        body_md="will be hidden",
        tags=[],
        group_id="",
        settlement_tx_id="TX-H1",
    )
    store.mark_post_hidden_platform(post)

    response = social_routes.x402_social_comment_list(
        _request(method="GET", path_params={"post_id": post.post_id})
    )
    assert isinstance(response, Response)
    assert response.status_code == 404
    body = json.loads(response.description)
    assert body["error"]["code"] == "not_found"


def test_comment_list_still_serves_comments_for_a_live_post(
    store: InMemorySocialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sanity check alongside the two 404 tests above: an ordinary, live post's comments are unaffected by the A3 fix."""
    post_service_ = PostService(store, is_registered=_always_registered)
    monkeypatch.setattr(social_routes, "post_service", post_service_)

    post = post_service_.create(
        author=_OTHER_PAYER, body_md="still here", tags=[], group_id="", settlement_tx_id="TX-L1"
    )
    post_service_.add_comment(
        post_id=post.post_id, author=_PAYER, body_md="hello", settlement_tx_id="TX-L2"
    )

    response = social_routes.x402_social_comment_list(
        _request(method="GET", path_params={"post_id": post.post_id})
    )
    assert isinstance(response, dict)
    assert len(response["comments"]) == 1


# --------------------------------------------------------------------------- #
# S1-D: both output formats derive from one struct (design doc section 3)
# --------------------------------------------------------------------------- #
def test_post_prose_and_json_derive_from_the_same_dict() -> None:
    """post_prose renders straight from the fields of the SAME dict the JSON branch would serialize -- changing a field in the dict changes the prose output identically, because there is no second, independently-computed description."""
    payload = {
        "post_id": "abc-123",
        "author": "WALLETXYZ",
        "group_id": "defi-signals",
        "body_md": "Liquidity doubled overnight.",
        "tags": ["defi", "liquidity"],
        "created_at_epoch": 1757000000,
        "deleted": False,
        "hidden_group": False,
        "reactions": {"up": 12, "down": 1},
        "comment_count": 4,
    }
    text = prose.post_prose(payload)
    assert payload["post_id"] in text
    assert payload["author"] in text
    assert payload["group_id"] in text
    assert "defi, liquidity" in text
    assert "12 up, 1 down" in text
    assert "4 comment" in text
    assert payload["body_md"] in text

    # Mutate the SAME dict fields the JSON response would carry; the prose
    # output must move in lockstep, proving there is no separately hardcoded
    # copy of these values anywhere in prose.py.
    payload["reactions"] = {"up": 99, "down": 7}
    payload["comment_count"] = 1
    moved = prose.post_prose(payload)
    assert "99 up, 7 down" in moved
    assert "1 comment" in moved
    assert "12 up, 1 down" not in moved


def test_group_prose_derives_from_the_same_dict() -> None:
    """group_prose renders straight from the same dict fields as the JSON branch, same structural guarantee as post_prose above."""
    payload = {
        "group_id": "g1",
        "name": "defi-signals",
        "description": "Liquidity and volume signals.",
        "owner": "WALLETOWNER",
        "created_at_epoch": 1757000000,
    }
    text = prose.group_prose(payload)
    assert payload["name"] in text
    assert payload["owner"] in text
    assert payload["description"] in text

    payload["owner"] = "WALLETNEWOWNER"
    moved = prose.group_prose(payload)
    assert "WALLETNEWOWNER" in moved
    assert "WALLETOWNER" not in moved or moved.count("WALLETOWNER") < text.count("WALLETOWNER")


def test_trending_prose_derives_from_the_same_list() -> None:
    """trending_prose renders straight from the same list the JSON branch would serialize."""
    items = [{"tag": "defi", "score": 12.5}, {"tag": "nft", "score": 3.0}]
    text = prose.trending_prose(items, heading="Trending topics", key="tag")
    assert "defi" in text
    assert "12.50" in text
    assert "nft" in text


# --------------------------------------------------------------------------- #
# trending_service: weighted ZINCRBY buckets, fail-open on Redis loss
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis")
def test_trending_topics_ranks_by_accumulated_weight() -> None:
    """A tag with more accumulated weight (post + reaction) outranks one with a single reaction."""
    trending_service.record_topic_activity(["defi"], weight=trending_service.POST_WEIGHT)
    trending_service.record_topic_activity(["defi"], weight=trending_service.REACTION_WEIGHT)
    trending_service.record_topic_activity(["nft"], weight=trending_service.REACTION_WEIGHT)

    ranked = trending_service.top_topics(limit=10)
    tags = [tag for tag, _score in ranked]
    assert tags[0] == "defi"
    assert "nft" in tags


@pytest.mark.usefixtures("fake_redis")
def test_trending_groups_records_and_reads_back() -> None:
    """A group's recorded activity is readable back via top_groups with a positive decayed score."""
    trending_service.record_group_activity("group-a", weight=trending_service.POST_WEIGHT)
    ranked = trending_service.top_groups(limit=10)
    assert ranked[0][0] == "group-a"
    assert ranked[0][1] > 0


def test_trending_fails_open_to_empty_on_redis_loss(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Redis outage on the read side returns an empty list, not an exception -- CLAUDE.md section 2 invariant 9."""
    monkeypatch.setattr(trending_service, "get_redis", lambda: _BrokenRedis())
    assert trending_service.top_topics(limit=10) == []
    assert trending_service.top_groups(limit=10) == []
    # The write side is equally fail-open: recording must not raise.
    trending_service.record_topic_activity(["defi"], weight=1)
    trending_service.record_group_activity("group-a", weight=1)


# --------------------------------------------------------------------------- #
# Route-level smoke tests: POST /posts, GET /posts/{id} (both formats), react
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis")
def test_post_create_route_end_to_end(
    store: InMemorySocialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /posts stores the post under the payer's wallet; GET /posts/{id} reads it back in both json and prose formats."""
    monkeypatch.setattr(
        social_routes, "post_service", PostService(store, is_registered=_always_registered)
    )
    monkeypatch.setattr(
        social_routes,
        "require_paid_request",
        lambda *_a, **_kw: _settled_result(payer=_PAYER, txid="TX-POST-1"),
    )
    fulfilled: list[tuple[str, str]] = []
    monkeypatch.setattr(
        social_routes,
        "mark_fulfilled",
        lambda txid, *, resource: fulfilled.append((txid, resource)),
    )

    response = social_routes.x402_social_post_create(
        _request(
            body=json.dumps({"body_md": "hello agents", "tags": ["defi"]}).encode(),
            path="/api/v1/x402/social/posts",
        )
    )
    assert response.status_code == 200
    body = json.loads(response.description)
    assert body["post"]["author"] == _PAYER
    assert body["post"]["body_md"] == "hello agents"
    assert fulfilled == [("TX-POST-1", "x402-social-post")]

    post_id = body["post"]["post_id"]
    detail = social_routes.x402_social_post_detail(
        _request(
            method="GET",
            path_params={"post_id": post_id},
            path=f"/api/v1/x402/social/posts/{post_id}",
        )
    )
    assert isinstance(detail, dict)
    assert detail["post"]["post_id"] == post_id
    assert detail["post"]["reactions"] == {"up": 0, "down": 0}
    assert detail["post"]["comment_count"] == 0

    prose_response = social_routes.x402_social_post_detail(
        _request(
            method="GET",
            path_params={"post_id": post_id},
            query={"format": "prose"},
            path=f"/api/v1/x402/social/posts/{post_id}",
        )
    )
    assert prose_response.headers["Content-Type"].startswith("text/plain")
    assert "hello agents" in prose_response.description
    assert post_id in prose_response.description


@pytest.mark.usefixtures("fake_redis")
def test_post_create_route_rejects_embedded_html_before_the_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post body with embedded HTML is a free 400 -- require_paid_request must never even be called."""
    called = []
    monkeypatch.setattr(social_routes, "require_paid_request", lambda *_a, **_kw: called.append(1))

    response = social_routes.x402_social_post_create(
        _request(
            body=json.dumps({"body_md": "hi <script>evil()</script>"}).encode(),
            path="/api/v1/x402/social/posts",
        )
    )
    assert response.status_code == 400
    assert called == []


@pytest.mark.usefixtures("fake_redis")
def test_react_route_second_attempt_is_409_with_settlement_headers_and_no_refund(
    store: InMemorySocialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Route-level mirror of the reaction caller-fault contract: second reaction settles but is refused, payment kept, no refund attempted."""
    monkeypatch.setattr(
        social_routes, "post_service", PostService(store, is_registered=_always_registered)
    )
    post = PostService(store, is_registered=_always_registered).create(
        author=_PAYER, body_md="react to me", tags=[], group_id="", settlement_tx_id="TX-RP1"
    )

    refund_calls: list[dict[str, object]] = []
    monkeypatch.setattr(payment_service, "send_refund", lambda **kw: refund_calls.append(kw))
    fulfilled: list[tuple[str, str]] = []
    monkeypatch.setattr(
        social_routes,
        "mark_fulfilled",
        lambda txid, *, resource: fulfilled.append((txid, resource)),
    )

    monkeypatch.setattr(
        social_routes,
        "require_paid_request",
        lambda *_a, **_kw: _settled_result(payer=_OTHER_PAYER, txid="TX-R-A"),
    )
    first = social_routes.x402_social_react(
        _request(
            body=json.dumps({"value": "up"}).encode(),
            path_params={"post_id": post.post_id},
            path=f"/api/v1/x402/social/posts/{post.post_id}/react",
        )
    )
    assert first.status_code == 200

    monkeypatch.setattr(
        social_routes,
        "require_paid_request",
        lambda *_a, **_kw: _settled_result(payer=_OTHER_PAYER, txid="TX-R-B"),
    )
    second = social_routes.x402_social_react(
        _request(
            body=json.dumps({"value": "up"}).encode(),
            path_params={"post_id": post.post_id},
            path=f"/api/v1/x402/social/posts/{post.post_id}/react",
        )
    )
    assert second.status_code == 409
    assert second.headers.get("PAYMENT-RESPONSE") == "ok"
    assert fulfilled == [("TX-R-A", "x402-social-react")]
    assert refund_calls == []


# =============================================================================
# 2026-security-audit regression tests
# =============================================================================


# --------------------------------------------------------------------------- #
# Finding 1 (HIGH): a deleted group post must stop serving its body via
# EVERY read surface -- GET /posts/{id}, the author feed, the group feed,
# and the home feed -- in BOTH output formats, not just the canonical row
# and the author's own feed.
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis")
def test_deleted_group_post_disappears_from_every_read_surface_both_formats(
    store: InMemorySocialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deleted group post must vanish from GET /posts/{id}, the author feed, the group feed, and the home feed -- json and prose alike."""
    group_service_ = GroupService(store, is_registered=_always_registered)
    post_service_ = PostService(
        store,
        membership_lookup=lambda gid, w: group_service_.is_member(gid, w),
        is_registered=_always_registered,
    )
    group_service_._post_service = post_service_
    monkeypatch.setattr(social_routes, "post_service", post_service_)
    monkeypatch.setattr(social_routes, "group_service", group_service_)
    monkeypatch.setattr(
        social_routes, "graph_service", GraphService(store, is_registered=_always_registered)
    )

    group = group_service_.create(
        owner=_PAYER, name="deletion-test", description="", settlement_tx_id="TX-DG1"
    )
    post = post_service_.create(
        author=_PAYER,
        body_md="secret sauce",
        tags=[],
        group_id=group.group_id,
        settlement_tx_id="TX-DP1",
    )

    # The root bug: before the fix, the x402_social_group_feed projection
    # row was never flipped at all. Assert that directly at the store level.
    deleted = post_service_.delete(post.post_id, wallet=_PAYER)
    assert deleted.deleted is True
    group_feed_row = store.list_group_feed(group.group_id, limit=10)[0]
    assert group_feed_row.post_id == post.post_id
    assert group_feed_row.deleted is True

    # GET /posts/{id}: 404 in both formats, body never reaches the response.
    for fmt in ("json", "prose"):
        response = social_routes.x402_social_post_detail(
            _request(
                method="GET",
                path_params={"post_id": post.post_id},
                query={"format": fmt},
                path=f"/api/v1/x402/social/posts/{post.post_id}",
            )
        )
        assert response.status_code == 404
        assert "secret sauce" not in response.description

    # GET /agents/{wallet}/feed (author feed): excluded, both formats.
    for fmt in ("json", "prose"):
        response = social_routes.x402_social_author_feed(
            _request(
                method="GET",
                path_params={"wallet": _PAYER},
                query={"format": fmt},
                path=f"/api/v1/x402/social/agents/{_PAYER}/feed",
            )
        )
        if fmt == "json":
            assert isinstance(response, dict)
            assert response["posts"] == []
        else:
            assert "secret sauce" not in response.description

    # GET /groups/{id}/feed: excluded, both formats.
    for fmt in ("json", "prose"):
        response = social_routes.x402_social_group_feed(
            _request(
                method="GET",
                path_params={"group_id": group.group_id},
                query={"format": fmt},
                path=f"/api/v1/x402/social/groups/{group.group_id}/feed",
            )
        )
        if fmt == "json":
            assert isinstance(response, dict)
            assert response["posts"] == []
        else:
            assert "secret sauce" not in response.description

    # GET /feed (home feed, session-authenticated): excluded. The payer is
    # already the group's owner/member, which is enough fan-out to reach
    # this post via the group half of the merge if it were not filtered.
    token, _expires = social_routes.issue_session_token(_PAYER)
    home = social_routes.x402_social_home_feed(
        _request(
            method="GET",
            path="/api/v1/x402/social/feed",
            headers={"authorization": f"Bearer {token}"},
        )
    )
    assert isinstance(home, dict)
    assert home["posts"] == []


# --------------------------------------------------------------------------- #
# Finding 2 (MEDIUM-HIGH): session-issuance griefing lockout.
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis")
def test_garbage_nonce_attempt_does_not_invalidate_the_real_pending_challenge() -> None:
    """A garbage /auth/session attempt with a WRONG nonce for wallet A must not consume or clobber A's real pending challenge -- A can still complete a real login afterward."""
    sk, addr = account.generate_account()
    challenge = session_service.issue_challenge(addr)

    attacker_attempt = session_service.verify_challenge_signature(
        wallet=addr,
        nonce="totally-wrong-nonce-the-attacker-guessed",
        proof_method="signed_bytes",
        signature_b64="AA==",
    )
    assert attacker_attempt is False

    # The real pending challenge must still be there and redeemable.
    sig = util.sign_bytes(challenge.signing_message.encode(), sk)
    real_attempt = session_service.verify_challenge_signature(
        wallet=addr, nonce=challenge.nonce, proof_method="signed_bytes", signature_b64=sig
    )
    assert real_attempt is True


@pytest.mark.usefixtures("fake_redis")
def test_attacker_garbage_session_floods_never_lock_the_victims_own_login_out() -> None:
    """Flooding POST /auth/session with garbage naming the victim wallet (varying attacker IPs, so per-IP limiting is not what is under test) must never 429 the victim's own subsequent real login -- the per-wallet axis was removed from the shared issuance limiter and replaced by a failure-only counter that a SUCCESSFUL login never touches (finding 2, 2026-security-audit)."""
    sk, addr = account.generate_account()

    challenge_response = social_routes.x402_social_auth_challenge(
        _request(
            body=json.dumps({"wallet": addr}).encode(),
            path="/api/v1/x402/social/auth/challenge",
            headers={"x-real-ip": "198.51.100.1"},
        )
    )
    assert isinstance(challenge_response, dict)

    limit = settings.x402_social_session_rate_limit_per_hour
    for i in range(limit + 5):
        response = social_routes.x402_social_auth_session(
            _request(
                body=json.dumps(
                    {
                        "wallet": addr,
                        "nonce": "garbage-nonce",
                        "proof_method": "signed_bytes",
                        "signature_b64": "AA==",
                    }
                ).encode(),
                path="/api/v1/x402/social/auth/session",
                # Vary the attacker's own IP so the per-IP limiter (a
                # separate, legitimate guard) does not itself start
                # refusing the attacker's requests before the per-wallet
                # scenario this test targets is fully exercised.
                headers={"x-real-ip": f"203.0.113.{i % 250 + 1}"},
            )
        )
        assert response.status_code in (401, 429)

    # The victim's OWN real login, from yet another IP, must still succeed.
    sig = util.sign_bytes(challenge_response["signing_message"].encode(), sk)
    login_response = social_routes.x402_social_auth_session(
        _request(
            body=json.dumps(
                {
                    "wallet": addr,
                    "nonce": challenge_response["nonce"],
                    "proof_method": "signed_bytes",
                    "signature_b64": sig,
                }
            ).encode(),
            path="/api/v1/x402/social/auth/session",
            headers={"x-real-ip": "192.0.2.200"},
        )
    )
    assert isinstance(login_response, dict)
    assert login_response["token"]

    # The victim's own /auth/challenge is unaffected too -- the per-wallet
    # axis was removed from challenge issuance entirely.
    second_challenge = social_routes.x402_social_auth_challenge(
        _request(
            body=json.dumps({"wallet": addr}).encode(),
            path="/api/v1/x402/social/auth/challenge",
            headers={"x-real-ip": "192.0.2.201"},
        )
    )
    assert isinstance(second_challenge, dict)


@pytest.mark.usefixtures("fake_redis")
def test_session_verification_failed_only_counts_failures_not_successes() -> None:
    """Unit-level check on the replacement rate-limit primitive itself: a failed verification increments the per-wallet counter; nothing about a successful login ever calls it (enforced by x402_social_auth_session's own control flow, exercised above)."""
    addr = encode_address(bytes([9]) + bytes(31))
    limit = settings.x402_social_session_rate_limit_per_hour
    for _ in range(limit):
        assert social_rate_limit.session_verification_failed(wallet=addr) is False
    assert social_rate_limit.session_verification_failed(wallet=addr) is True


# --------------------------------------------------------------------------- #
# Finding 3 (MEDIUM): group-name LWT claim compensation on partial failure.
# --------------------------------------------------------------------------- #
class _FailingInsertGroupStore(InMemorySocialStore):
    """Wins the name-claim LWT normally, then always fails the next write -- simulates insert_group blowing up after try_claim_group_name already won."""

    def insert_group(self, item: StoredGroup) -> None:  # type: ignore[override]
        del item
        raise RuntimeError("simulated insert_group failure")


def test_group_create_releases_the_name_claim_on_a_later_failure() -> None:
    """If insert_group raises AFTER the name-claim LWT wins, the claimed name must be released, not permanently stuck (finding 3, 2026-security-audit)."""
    store_ = _FailingInsertGroupStore()
    service = GroupService(store_, is_registered=_always_registered)

    with pytest.raises(RuntimeError):
        service.create(
            owner=_PAYER, name="claim-test", description="", settlement_tx_id="TX-G-FAIL"
        )

    # The name claim was released -- a fresh claim for the SAME normalized
    # name now succeeds, proving the row is gone, not permanently stuck.
    assert (
        store_.try_claim_group_name(name_norm="claim-test", group_id="some-other-group-id") is True
    )


# --------------------------------------------------------------------------- #
# Finding 4 (MEDIUM): registration must actually be required for every paid
# action -- post, react, comment, follow, group-create, group-join.
# --------------------------------------------------------------------------- #
def test_unregistered_wallet_is_refused_for_every_gated_action(store: InMemorySocialStore) -> None:
    """Mirrors not_group_member's shape: caller-fault, 403, payment already settled (checked inside the service, the same POST-gate position as not_group_member)."""

    def _never_registered(_wallet: str) -> bool:
        return False

    post_service_ = PostService(store, is_registered=_never_registered)
    group_service_ = GroupService(store, is_registered=_never_registered)
    graph_service_ = GraphService(store, is_registered=_never_registered)

    with pytest.raises(SocialError) as exc_info:
        post_service_.create(
            author=_PAYER, body_md="hi", tags=[], group_id="", settlement_tx_id="TX-NR1"
        )
    assert exc_info.value.code == "not_registered"
    assert exc_info.value.http_status == 403

    # Seed a post/group via services that DO treat the actor as registered,
    # so react/comment/join can be tested against something real.
    registered_post_service = PostService(store, is_registered=_always_registered)
    post = registered_post_service.create(
        author=_OTHER_PAYER, body_md="seed", tags=[], group_id="", settlement_tx_id="TX-NR-SEED"
    )

    with pytest.raises(SocialError) as exc_info:
        post_service_.react(
            post_id=post.post_id, wallet=_PAYER, value=REACTION_UP, settlement_tx_id="TX-NR2"
        )
    assert exc_info.value.code == "not_registered"

    with pytest.raises(SocialError) as exc_info:
        post_service_.add_comment(
            post_id=post.post_id, author=_PAYER, body_md="hey", settlement_tx_id="TX-NR3"
        )
    assert exc_info.value.code == "not_registered"

    with pytest.raises(SocialError) as exc_info:
        graph_service_.follow(follower=_PAYER, followee=_OTHER_PAYER)
    assert exc_info.value.code == "not_registered"

    with pytest.raises(SocialError) as exc_info:
        group_service_.create(
            owner=_PAYER, name="nr-group", description="", settlement_tx_id="TX-NR4"
        )
    assert exc_info.value.code == "not_registered"

    registered_group_service = GroupService(store, is_registered=_always_registered)
    group = registered_group_service.create(
        owner=_OTHER_PAYER, name="nr-seed-group", description="", settlement_tx_id="TX-NR-SEEDG"
    )
    with pytest.raises(SocialError) as exc_info:
        group_service_.join(group_id=group.group_id, wallet=_PAYER, settlement_tx_id="TX-NR5")
    assert exc_info.value.code == "not_registered"


@pytest.mark.usefixtures("fake_redis")
def test_post_create_route_refuses_an_unregistered_payer_payment_kept_no_refund_attempted(
    store: InMemorySocialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Route-level mirror of the not_group_member route test's shape: settles but is refused, payment kept, no refund attempted."""
    monkeypatch.setattr(
        social_routes, "post_service", PostService(store, is_registered=lambda _w: False)
    )
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
        lambda *_a, **_kw: _settled_result(payer=_PAYER, txid="TX-NR-ROUTE"),
    )

    response = social_routes.x402_social_post_create(
        _request(body=json.dumps({"body_md": "hello"}).encode(), path="/api/v1/x402/social/posts")
    )
    assert response.status_code == 403
    body = json.loads(response.description)
    assert body["error"]["code"] == "not_registered"
    assert response.headers.get("PAYMENT-RESPONSE") == "ok"
    assert fulfilled == []
    assert refund_calls == []


# --------------------------------------------------------------------------- #
# Finding 5 (MEDIUM-LOW): malformed post_id must not 500 in production, and
# minted ids must not embed this host's real MAC address.
# --------------------------------------------------------------------------- #
def test_cassandra_store_treats_a_malformed_post_id_as_not_found_not_a_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_post/list_comments/get_reaction_totals on a non-UUID string resolve to "not found" WITHOUT ever touching Cassandra -- the production path this repro (`GET /posts/not-a-uuid`) would otherwise 500 on."""

    def _must_not_reach_cassandra() -> None:
        raise AssertionError("must not reach Cassandra for a malformed id")

    monkeypatch.setattr(
        social_cassandra_store, "get_cassandra_session", lambda: _must_not_reach_cassandra()
    )
    cassandra_store = CassandraSocialStore()

    assert cassandra_store.get_post("not-a-uuid") is None
    assert cassandra_store.list_comments("not-a-uuid", limit=10) == []
    assert cassandra_store.get_reaction_totals("not-a-uuid") == ReactionTotals()

    # A well-formed but non-version-1 UUID is "not found" too: post_id/case_id
    # are timeuuid columns, and binding a NIL or v4 uuid to one is a
    # Cassandra InvalidRequest the driver raised straight through to a live
    # 500 (found 2026-09-05 on GET /posts/<nil>, POST .../comments, GET /cases/<nil>).
    for non_v1 in ("00000000000000000000000000000000", str(uuid_module.uuid4())):
        assert cassandra_store.get_post(non_v1) is None
        assert cassandra_store.list_comments(non_v1, limit=10) == []
        assert cassandra_store.get_reaction_totals(non_v1) == ReactionTotals()
        assert cassandra_store.get_case(non_v1) is None


def _capture_gate_kwargs(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace the route module's payment gate with one that records its kwargs and returns a bare 402 -- enough to inspect what a route declares without a facilitator."""
    captured: dict[str, Any] = {}

    def _gate(_request: Request, **kwargs: object) -> x402_guard.PaymentResult:
        captured.update(kwargs)
        return x402_guard.PaymentResult(
            error=Response(status_code=402, headers={}, description="{}")
        )

    monkeypatch.setattr(social_routes, "require_paid_request", _gate)
    return captured


def _validates_as_a_post(extensions: dict[str, Any]) -> bool:
    """Run the declaration through the same method enrichment the resource server applies at request time, then the facilitator's own validator."""
    enriched = bazaar_resource_server_extension.enrich_declaration(
        extensions["bazaar"], SimpleNamespace(method="POST")
    )
    return validate_discovery_extension(enriched).valid


@pytest.mark.usefixtures("fake_redis")
def test_follow_declares_a_template_url_and_a_body_extension_that_validates_for_a_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST .../agents/{wallet}/follow takes no body, but as a POST it must still declare a body-shaped extension: a query-shaped one fails the facilitator's validator and the route is never catalogued (found live 2026-09-05)."""
    captured = _capture_gate_kwargs(monkeypatch)

    response = social_routes.x402_social_follow(
        _request(path_params={"wallet": _PAYER}, path=f"/api/v1/x402/social/agents/{_PAYER}/follow")
    )

    assert response.status_code == 402
    assert captured["resource_path"] == "/api/v1/x402/social/agents/{wallet}/follow"
    assert _validates_as_a_post(captured["extensions"])


@pytest.mark.usefixtures("fake_redis")
def test_group_join_declares_a_template_url_and_a_body_extension_that_validates_for_a_post(
    store: InMemorySocialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same rule as follow, for the other body-less paid POST in this module."""
    group_service_ = GroupService(store, is_registered=_always_registered)
    monkeypatch.setattr(social_routes, "group_service", group_service_)
    group = group_service_.create(
        owner=_PAYER, name="join-declaration", description="", settlement_tx_id="TX-J402"
    )
    captured = _capture_gate_kwargs(monkeypatch)

    response = social_routes.x402_social_group_join(
        _request(
            path_params={"group_id": group.group_id},
            path=f"/api/v1/x402/social/groups/{group.group_id}/join",
        )
    )

    assert response.status_code == 402
    assert captured["resource_path"] == "/api/v1/x402/social/groups/{group_id}/join"
    assert _validates_as_a_post(captured["extensions"])


def test_new_post_or_comment_id_does_not_embed_a_real_mac_address() -> None:
    """A minted post/comment id must have the multicast bit set on its node field -- the standard RFC 4122 marker that the node is NOT a real IEEE 802 MAC address, unlike a bare uuid.uuid1()."""
    generated = uuid_module.UUID(_new_post_or_comment_id())
    assert generated.version == 1
    assert generated.node & 0x010000000000 != 0


# --------------------------------------------------------------------------- #
# Finding 6 (LOW-MEDIUM): home feed must dedupe by post_id.
# --------------------------------------------------------------------------- #
def test_home_feed_dedupes_a_post_reachable_via_both_a_direct_follow_and_a_shared_group(
    store: InMemorySocialStore,
) -> None:
    """A post whose author is BOTH directly followed AND a fellow group member must appear exactly once in the merged home feed, not twice."""
    group_service_ = GroupService(store, is_registered=_always_registered)
    post_service_ = PostService(
        store,
        membership_lookup=lambda gid, w: group_service_.is_member(gid, w),
        is_registered=_always_registered,
    )
    group = group_service_.create(
        owner=_OTHER_PAYER, name="dedup-test", description="", settlement_tx_id="TX-DD1"
    )
    group_service_.join(group_id=group.group_id, wallet=_PAYER, settlement_tx_id="TX-DD-J1")

    post = post_service_.create(
        author=_OTHER_PAYER,
        body_md="one post, two paths",
        tags=[],
        group_id=group.group_id,
        settlement_tx_id="TX-DD2",
    )

    feed = post_service_.home_feed(followees=[_OTHER_PAYER], groups=[group.group_id], limit=10)
    assert [p.post_id for p in feed] == [post.post_id]


# --------------------------------------------------------------------------- #
# Finding 7 (LOW-MEDIUM): trending reads must be bounded per bucket, and
# still exactly correct for the top-N result.
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis")
def test_trending_read_is_bounded_per_bucket_and_still_produces_a_correct_top_n(
    fake_redis: _FakeRedis,
) -> None:
    """A bucket with far more than _BUCKET_TOP_N members must be read with a bounded zrevrange, and the merged top-N must still be exactly correct."""
    now = datetime.now(tz=UTC)
    key = f"{trending_service._TOPICS_PREFIX}{now.strftime('%Y%m%d%H')}"
    bucket_top_n = trending_service._BUCKET_TOP_N
    zset = fake_redis.zsets.setdefault(key, {})
    # Far more members than _BUCKET_TOP_N, distinct scores, all in the
    # current (undecayed) hour.
    for i in range(bucket_top_n + 50):
        zset[f"tag-{i}"] = float(i)

    calls: list[tuple[int, int]] = []
    original_zrevrange = fake_redis.zrevrange

    def _spy(key_: str, start: int, end: int, withscores: bool = False) -> object:
        calls.append((start, end))
        return original_zrevrange(key_, start, end, withscores=withscores)

    fake_redis.zrevrange = _spy  # type: ignore[method-assign]

    top = trending_service.top_topics(limit=5, now=now)

    # Bounded: every call asked for at most _BUCKET_TOP_N members, never the
    # full (bucket_top_n + 50)-member set.
    assert calls
    assert all((end - start + 1) == bucket_top_n for start, end in calls)

    # Still exactly correct: the highest-scored tags are tag-(N+49) down to
    # tag-(N+45), all comfortably within the top _BUCKET_TOP_N of the bucket.
    top_n = bucket_top_n + 50 - 1
    assert [tag for tag, _score in top] == [f"tag-{top_n - i}" for i in range(5)]


# --------------------------------------------------------------------------- #
# Finding 8 (LOW): a self-follow must not make a wallet its own friend.
# --------------------------------------------------------------------------- #
def test_self_follow_is_refused_and_cannot_make_a_wallet_its_own_friend(
    store: InMemorySocialStore,
) -> None:
    """A self-follow is refused as caller-fault, never silently allowed to make a wallet its own mutual-follow "friend"."""
    service = GraphService(store, is_registered=_always_registered)
    with pytest.raises(SocialError) as exc_info:
        service.follow(follower=_PAYER, followee=_PAYER)
    assert exc_info.value.code == "cannot_follow_self"
    assert exc_info.value.http_status == 400
    # The rejected wallet is named in the message (2026-09-03 polish, per real
    # agent feedback) -- concrete and actionable in an automated caller's own
    # logs, not just a generic "you can't do that."
    assert _PAYER in exc_info.value.message
    assert service.friends(_PAYER, limit=10) == []
    assert service.following(_PAYER, limit=10) == []


# --------------------------------------------------------------------------- #
# Finding 9 (LOW, defense in depth): profile and group text fields must
# reject embedded HTML too, not just post/comment bodies.
# --------------------------------------------------------------------------- #
def test_validate_profile_fields_rejects_embedded_html_in_name() -> None:
    """A profile name containing embedded HTML is rejected the same way a post body is."""
    with pytest.raises(SocialError) as exc_info:
        validate_profile_fields(
            name="<img src=x onerror=alert(1)>",
            bio="",
            mission="",
            location="",
            interests=[],
            emoji="",
        )
    assert exc_info.value.code == "embedded_html_rejected"


def test_validate_profile_fields_rejects_embedded_html_in_bio() -> None:
    """A profile bio containing embedded HTML is rejected."""
    with pytest.raises(SocialError) as exc_info:
        validate_profile_fields(
            name="AgentX",
            bio="hi <script>evil()</script>",
            mission="",
            location="",
            interests=[],
            emoji="",
        )
    assert exc_info.value.code == "embedded_html_rejected"


def test_validate_profile_fields_rejects_embedded_html_in_mission_and_location() -> None:
    """A profile mission or location field containing embedded HTML is rejected."""
    with pytest.raises(SocialError) as exc_info:
        validate_profile_fields(
            name="AgentX",
            bio="",
            mission="<script>x</script>",
            location="",
            interests=[],
            emoji="",
        )
    assert exc_info.value.code == "embedded_html_rejected"

    with pytest.raises(SocialError) as exc_info:
        validate_profile_fields(
            name="AgentX",
            bio="",
            mission="",
            location="<script>x</script>",
            interests=[],
            emoji="",
        )
    assert exc_info.value.code == "embedded_html_rejected"


def test_validate_profile_fields_rejects_embedded_html_in_an_interest_entry() -> None:
    """A single interests-list entry containing embedded HTML is rejected."""
    with pytest.raises(SocialError) as exc_info:
        validate_profile_fields(
            name="AgentX",
            bio="",
            mission="",
            location="",
            interests=["<script>x</script>"],
            emoji="",
        )
    assert exc_info.value.code == "embedded_html_rejected"


def test_group_create_rejects_embedded_html_in_name_and_description(
    store: InMemorySocialStore,
) -> None:
    """A group name or description containing embedded HTML is rejected."""
    service = GroupService(store, is_registered=_always_registered)
    with pytest.raises(SocialError) as exc_info:
        service.create(
            owner=_PAYER,
            name="<script>evil()</script>",
            description="",
            settlement_tx_id="TX-HTML1",
        )
    assert exc_info.value.code == "embedded_html_rejected"

    with pytest.raises(SocialError) as exc_info:
        service.create(
            owner=_PAYER,
            name="safe-name",
            description="<script>evil()</script>",
            settlement_tx_id="TX-HTML2",
        )
    assert exc_info.value.code == "embedded_html_rejected"


# --------------------------------------------------------------------------- #
# Optimization pass (2026-09-02): home feed fan-out is batched per backend,
# not one round-trip per followee/group; comment_count reads a narrow
# projection, not full comment rows; trending's bucket reads are pipelined.
# --------------------------------------------------------------------------- #
def test_cassandra_home_feed_fanout_is_one_batched_call_per_half_not_a_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """list_posts_by_authors/list_group_feeds must fan out via execute_parallel_with_args (one concurrent batch), not session.execute called once per author/group -- the N+1 shape the optimization pass replaced.

    Accessing a _Stmt attribute (X402SocialStmts.LIST_POSTS_BY_AUTHOR etc.)
    always triggers prepare_cached, including as an argument expression
    before the (faked) call it's passed into ever runs -- patch_cassandra
    makes that resolve without a real connection, same as every other
    store-level Cassandra unit test in this suite.
    """
    parallel_calls: list[tuple[object, list[tuple]]] = []

    def _fake_parallel(statement: object, args_seq: list[tuple], **_kw: object) -> list[tuple]:
        parallel_calls.append((statement, list(args_seq)))
        return [(True, []) for _ in args_seq]

    def _must_not_call_execute(*_a: object, **_kw: object) -> Never:
        raise AssertionError(
            "must not call session.execute directly for a per-author/per-group fan-out -- "
            "that is exactly the N+1 shape this optimization removed"
        )

    patch_cassandra(monkeypatch)  # identity prepare_cached -- no real connection
    monkeypatch.setattr(social_cassandra_store, "execute_parallel_with_args", _fake_parallel)
    monkeypatch.setattr(
        social_cassandra_store,
        "get_cassandra_session",
        lambda: SimpleNamespace(execute=_must_not_call_execute),
    )
    store = CassandraSocialStore()

    authors = [f"wallet-{i}" for i in range(5)]
    groups = [f"group-{i}" for i in range(3)]
    assert store.list_posts_by_authors(authors, limit=10) == []
    assert store.list_group_feeds(groups, limit=10) == []

    assert len(parallel_calls) == 2  # one batched call for authors, one for groups
    (authors_stmt, authors_args), (groups_stmt, groups_args) = parallel_calls
    assert authors_stmt is X402SocialStmts.LIST_POSTS_BY_AUTHOR
    assert [a for a, _limit in authors_args] == authors
    assert groups_stmt is X402SocialStmts.LIST_GROUP_FEED
    assert [g for g, _limit in groups_args] == groups

    # Empty input must not even attempt a batch call (nothing to fan out).
    parallel_calls.clear()
    assert store.list_posts_by_authors([], limit=10) == []
    assert store.list_group_feeds([], limit=10) == []
    assert parallel_calls == []


def test_cassandra_count_comments_uses_the_narrow_id_only_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """count_comments must read LIST_COMMENT_IDS (comment_id only), never LIST_COMMENTS's full rows (body_md included) -- comment_count() runs on every free GET /posts/{id}.

    Accessing a _Stmt attribute (e.g. X402SocialStmts.LIST_COMMENT_IDS) always
    triggers prepare_cached -- patch_cassandra (tests/conftest.py) makes that
    resolve to the raw CQL string instead of dialing a real cluster, the same
    helper every other store-level Cassandra unit test in this suite uses.
    """
    executed: list[object] = []

    def _fake_execute(statement: object, _params: tuple) -> object:
        executed.append(statement)
        return [SimpleNamespace(comment_id=f"c{i}") for i in range(3)]

    patch_cassandra(monkeypatch)  # identity prepare_cached -- no real connection
    monkeypatch.setattr(
        social_cassandra_store,
        "get_cassandra_session",
        lambda: SimpleNamespace(execute=_fake_execute),
    )
    store = CassandraSocialStore()

    count = store.count_comments(str(uuid_module.uuid1()), limit=500)

    assert count == 3
    assert executed == [X402SocialStmts.LIST_COMMENT_IDS]
    assert X402SocialStmts.LIST_COMMENTS not in executed


# --------------------------------------------------------------------------- #
# A4 (2026-09-03): increment_case_vote_total must log a warning (with the
# same detail increment_reaction_total already logs) before re-raising on a
# counter-increment failure, instead of failing silently.
# --------------------------------------------------------------------------- #
def test_cassandra_increment_case_vote_total_logs_a_warning_and_reraises_on_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The vote LWT has already won (try_add_case_vote) by the time this runs -- the vote itself is durably recorded even if this counter increment then fails, same "core write succeeded, only a counter is stale" class as increment_reaction_total. Before this fix, that failure propagated with no log line at all; now it logs at warning with case_id/verdict, matching increment_reaction_total's own precedent."""

    def _fake_execute(_statement: object, _params: tuple) -> object:
        raise ConnectionError("cassandra down")

    patch_cassandra(monkeypatch)
    monkeypatch.setattr(
        social_cassandra_store,
        "get_cassandra_session",
        lambda: SimpleNamespace(execute=_fake_execute),
    )
    store = CassandraSocialStore()
    case_id = str(uuid_module.uuid1())

    with (
        caplog.at_level("WARNING", logger="app.modules.x402_social.stores.cassandra"),
        pytest.raises(ConnectionError),
    ):
        store.increment_case_vote_total(case_id, verdict="uphold")

    assert any(
        "vote tally counter increment failed" in record.getMessage() for record in caplog.records
    )
    assert any(case_id in record.getMessage() for record in caplog.records)


def test_trending_merge_pipelines_every_bucket_read_into_one_round_trip(
    fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_merge_decayed must queue all _MERGE_HOURS bucket reads on one pipeline and call execute() once, not issue a blocking zrevrange per bucket.

    _FakePipeline.execute() necessarily replays its queued ops against the
    underlying fake client (it has no real transport to batch over), so the
    real signal a "one round trip" fake can prove is call COUNTS: one
    pipeline() call and one execute() call for the whole merge, each
    queueing/serving _MERGE_HOURS ops -- not _MERGE_HOURS separate
    pipeline-and-execute pairs, which is what a per-bucket loop would do.
    """
    monkeypatch.setattr(trending_service, "get_redis", lambda: fake_redis)
    pipelines_created: list[_FakePipeline] = []
    execute_call_op_counts: list[int] = []
    original_pipeline = fake_redis.pipeline
    original_execute = _FakePipeline.execute

    def _spy_pipeline() -> _FakePipeline:
        pipe = original_pipeline()
        pipelines_created.append(pipe)
        return pipe

    def _counted_execute(self: _FakePipeline) -> list[object]:
        execute_call_op_counts.append(len(self._ops))
        return original_execute(self)

    fake_redis.pipeline = _spy_pipeline  # type: ignore[method-assign]
    monkeypatch.setattr(_FakePipeline, "execute", _counted_execute)

    top = trending_service.top_topics(limit=5)

    # Exactly one pipeline built and executed for the whole merge, not one
    # per bucket -- and that one execute() call served all _MERGE_HOURS
    # queued bucket reads, not just one.
    assert len(pipelines_created) == 1
    assert execute_call_op_counts == [trending_service._MERGE_HOURS]
    assert top == []  # no activity recorded -- just proving the call shape here


# --------------------------------------------------------------------------- #
# A2 (2026-09-03): x402_social_standing's full-row read-modify-write must
# not silently clobber a concurrent writer.
# --------------------------------------------------------------------------- #
class _FakeStandingSession:
    """Models exactly one row of x402_social_standing with REAL Cassandra LWT semantics.

    For the three statements mutate_standing touches: GET_STANDING,
    INSERT_STANDING_IF_ABSENT, and UPDATE_STANDING_IF_MATCH (applied iff
    every compared column still equals what was bound). This is what lets a
    test actually PROVE the CAS retry loop protects a concurrent writer,
    rather than just asserting call shape: `row` is mutated by the test
    itself mid-mutation to simulate a second writer landing between
    mutate_standing's read and its CAS write, the way two concurrent case
    resolutions touching the same wallet's standing genuinely can.
    """

    def __init__(self) -> None:
        self.row: tuple | None = None  # the 10 non-wallet columns, INSERT_STANDING_IF_ABSENT order
        self.get_calls = 0

    def _row_namespace(self, wallet: str) -> SimpleNamespace:
        v = self.row
        assert v is not None
        return SimpleNamespace(
            wallet=wallet,
            offense_count=v[0],
            last_offense_at=v[1],
            banned_until=v[2],
            offenses=v[3],
            reported_count=v[4],
            rejected_report_count=v[5],
            report_rejection_streak=v[6],
            report_cooldown_until=v[7],
            votes_cast=v[8],
            votes_matched_resolution=v[9],
        )

    def execute(self, stmt: object, params: tuple) -> SimpleNamespace:
        if stmt is X402SocialStmts.GET_STANDING:
            self.get_calls += 1
            (wallet,) = params
            row = None if self.row is None else self._row_namespace(wallet)
            return SimpleNamespace(one=lambda: row)
        if stmt is X402SocialStmts.INSERT_STANDING_IF_ABSENT:
            values = params[1:]
            applied = self.row is None
            if applied:
                self.row = values
            return SimpleNamespace(was_applied=applied)
        if stmt is X402SocialStmts.UPSERT_STANDING:
            # Plain unconditional overwrite -- used only to SEED the fake
            # row in these tests, mirroring how a direct test-setup upsert
            # is unconditional in production too.
            self.row = params[1:]
            return SimpleNamespace(was_applied=True)
        if stmt is X402SocialStmts.UPDATE_STANDING_IF_MATCH:
            new_values = tuple(params[0:10])
            old_values = tuple(params[11:21])
            applied = self.row == old_values
            if applied:
                self.row = new_values
            return SimpleNamespace(was_applied=applied)
        raise AssertionError(f"unexpected statement in _FakeStandingSession: {stmt!r}")


def test_cassandra_mutate_standing_retries_after_a_concurrent_writer_wins_the_first_cas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact race A2 fixes: mutate_standing's first CAS attempt loses because a "concurrent" writer touched the row between its read and its write -- the OLD get_standing-then-upsert_standing pattern would have silently overwritten that concurrent write (e.g. a ban) with a stale full-row copy; mutate_standing instead re-reads and retries, so BOTH this mutation's own change and the concurrent writer's change survive."""
    patch_cassandra(monkeypatch)  # identity prepare_cached -- no real connection
    fake = _FakeStandingSession()
    monkeypatch.setattr(social_cassandra_store, "get_cassandra_session", lambda: fake)
    store = CassandraSocialStore()

    # Seed an existing row simulating a ban already recorded for this wallet.
    seed = StoredStanding(wallet=_PAYER, banned_until_epoch=1_000_000, offense_count=1)
    store.upsert_standing(seed)
    assert fake.row is not None

    attempts = {"n": 0}

    def _mutate(standing: StoredStanding) -> StoredStanding:
        attempts["n"] += 1
        if attempts["n"] == 1:
            # Simulate a second, concurrent writer landing in the gap
            # between mutate_standing's read and its own CAS write: bump
            # votes_cast directly on the underlying "table" row.
            old = fake.row
            assert old is not None
            fake.row = (*old[:8], old[8] + 1, old[9])
        standing.reported_count += 1
        return standing

    result = store.mutate_standing(_PAYER, _mutate)

    # The first CAS attempt lost (the row had changed underneath it) and was
    # retried exactly once more.
    assert attempts["n"] == 2
    assert result.reported_count == 1

    final = store.get_standing(_PAYER)
    assert final is not None
    # The pre-existing ban was never clobbered...
    assert final.banned_until_epoch == 1_000_000
    assert final.offense_count == 1
    # ...the "concurrent" writer's own change survived too...
    assert final.votes_cast == 1
    # ...and this mutation's own change was still correctly applied on retry.
    assert final.reported_count == 1


def test_cassandra_mutate_standing_first_ever_write_uses_insert_if_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wallet with no standing row yet goes through INSERT_STANDING_IF_ABSENT (an LWT insert), never UPDATE_STANDING_IF_MATCH -- there is no prior row to CAS against."""
    patch_cassandra(monkeypatch)
    fake = _FakeStandingSession()
    monkeypatch.setattr(social_cassandra_store, "get_cassandra_session", lambda: fake)
    store = CassandraSocialStore()

    def _mutate(standing: StoredStanding) -> StoredStanding:
        standing.votes_cast += 1
        return standing

    result = store.mutate_standing(_OTHER_PAYER, _mutate)
    assert result.votes_cast == 1
    assert store.get_standing(_OTHER_PAYER).votes_cast == 1


def test_moderation_service_standing_mutations_go_through_mutate_standing_not_read_then_upsert() -> (
    None
):
    """Regression guard for A2: every one of moderation_service's standing read-modify-write helpers must call store.mutate_standing (the atomic path), never the old get_standing-then-upsert_standing two-call pattern this class of bug came from."""
    store = InMemorySocialStore()
    ms = _moderation_service(store)
    _register(store, _REPORTER)
    _register(store, _OTHER_PAYER)

    mutate_calls: list[str] = []
    upsert_calls: list[str] = []
    original_mutate = store.mutate_standing
    original_upsert = store.upsert_standing

    def _spy_mutate(
        wallet: str, mutate: Callable[[StoredStanding], StoredStanding]
    ) -> StoredStanding:
        mutate_calls.append(wallet)
        return original_mutate(wallet, mutate)

    def _spy_upsert(item: StoredStanding) -> None:
        upsert_calls.append(item.wallet)
        original_upsert(item)

    store.mutate_standing = _spy_mutate  # type: ignore[method-assign]
    store.upsert_standing = _spy_upsert  # type: ignore[method-assign]

    ms._bump_reported_count(_OTHER_PAYER)
    ms._apply_ban(_OTHER_PAYER, resolved_at_epoch=_now_epoch())
    ms._reset_reporter_streak(_REPORTER)
    ms._escalate_reporter_cooldown(_REPORTER, resolved_at_epoch=_now_epoch())

    assert set(mutate_calls) == {_OTHER_PAYER, _REPORTER}
    assert upsert_calls == []  # the RMW helpers never fall back to the plain overwrite


# --------------------------------------------------------------------------- #
# Phase S2: community moderation (design doc section 5, owner sign-off
# 2026-09-03) -- report -> vote -> exponential ban, hard-delete, the section
# 8.1 admin lever. Fully offline, same in-memory store + fake-Redis
# conventions as every S0/S1 test above.
#
#   S2-1  Report-cooldown pre-gate refusal is free (no payment attempted),
#         with the cooldown timestamp in the body.
#   S2-2  Open-report concurrency cap: a 3rd report while 2 are open is
#         caller-fault (payment kept, 409) -- mirrors the group-name-
#         collision test shape.
#   S2-3  Case resolution, both category branches: quorum+ratio met ->
#         upheld, non-illegal_content tombstones (hidden_platform), illegal_
#         content hard-deletes (row gone, removal audit record written,
#         content_snapshot scrubbed to the fixed placeholder).
#   S2-4  Quorum failure -> not-upheld with a worded resolution_note; the
#         reporter's rejection streak/cooldown escalate.
#   S2-5  An upheld report resets the reporter's rejection streak to 0.
#   S2-6  Karma: votes_cast/votes_matched_resolution update per voter.
#   S2-7  The admin lever hard-deletes and writes the same removals shape,
#         removed_by='admin_lever', 0/0 votes -- both the service method
#         and the actual admin route (require_admin_wallet-gated).
#   S2-8  x402_social_moderation_enabled=False keeps the S2 routes from
#         registering (mirrors the x402_social_store gate in falcon_main.py).
#   Plus: the ban-formula and report-cooldown-formula pure functions, pinned
#   exactly per the design doc's own worked sequences.
# --------------------------------------------------------------------------- #
def _register(store: InMemorySocialStore, wallet: str, *, created_at_epoch: int = 0) -> None:
    """Register `wallet` with a minimal profile, optionally back-dated (created_at_epoch) so it predates a case's opening for vote-eligibility tests."""
    ProfileService(store).register(
        wallet=wallet,
        name="Agent",
        bio="",
        mission="",
        location="",
        interests=[],
        emoji="",
        settlement_tx_id="TX-REG",
        now=datetime.fromtimestamp(created_at_epoch, tz=UTC) if created_at_epoch else None,
    )


def _moderation_service(store: InMemorySocialStore) -> ModerationService:
    """A ModerationService bound to `store`, with post_service/group_service/registered_since all wired to that SAME store -- the shape api/routes.py's own module-level wiring uses."""
    profile_service = ProfileService(store)
    group_service = GroupService(store, is_registered=lambda w: profile_service.get(w) is not None)
    post_service = PostService(
        store,
        membership_lookup=lambda group_id, wallet: group_service.is_member(group_id, wallet),
        is_registered=lambda w: profile_service.get(w) is not None,
    )

    def registered_since(wallet: str) -> int | None:
        profile = profile_service.get(wallet)
        return profile.created_at_epoch if profile is not None else None

    return ModerationService(
        store,
        post_service=post_service,
        group_service=group_service,
        registered_since=registered_since,
    )


def _now_epoch() -> int:
    return int(datetime.now(tz=UTC).timestamp())


def _resolve_now(ms: ModerationService, case: StoredCase) -> StoredCase:
    """Force-resolve `case` immediately, bypassing real wall-clock window waiting: re-reads the raw stored case and calls the internal lazy-resolver with an explicit `now` just past its window."""
    raw = ms.store.get_case(case.case_id)
    assert raw is not None
    return ms._resolve_if_due(raw, now=datetime.fromtimestamp(raw.window_ends_at_epoch + 1, tz=UTC))


# --------------------------------------------------------------------------- #
# S2-1: report cooldown pre-gate refusal is free
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis")
def test_report_cooldown_pregate_refusal_is_free_and_returns_the_timestamp(
    store: InMemorySocialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wallet under an active report cooldown, identified via an optional bearer session, is refused free (403) with the cooldown timestamp in the body, WITHOUT require_paid_request ever being called (design doc sections 5.3/5.4.1)."""
    ms = _moderation_service(store)
    monkeypatch.setattr(social_routes, "moderation_service", ms)
    _register(store, _REPORTER)

    future = _now_epoch() + 900
    store.upsert_standing(StoredStanding(wallet=_REPORTER, report_cooldown_until_epoch=future))

    token, _expires = session_service.issue_session_token(_REPORTER)

    def _fail_if_called(*_a: object, **_kw: object) -> Never:
        raise AssertionError("require_paid_request must not be called for a cooldown refusal")

    monkeypatch.setattr(social_routes, "require_paid_request", _fail_if_called)

    response = social_routes.x402_social_report_create(
        _request(
            body=json.dumps(
                {"target_type": "agent", "target_id": _OTHER_PAYER, "category": "spam"}
            ).encode(),
            headers={"Authorization": f"Bearer {token}"},
            path="/api/v1/x402/social/reports",
        )
    )

    assert response.status_code == 403
    body = json.loads(response.description)
    assert body["error"]["code"] == "report_cooldown_active"
    assert body["report_cooldown_until_epoch"] == future


@pytest.mark.usefixtures("fake_redis")
def test_report_without_a_session_is_not_pregated_and_reaches_the_payment_gate(
    store: InMemorySocialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No bearer session -> no free pre-check is possible (the wallet is unknown pre-gate); the route proceeds straight to require_paid_request, exactly as every other paid S2/S1 route does."""
    ms = _moderation_service(store)
    monkeypatch.setattr(social_routes, "moderation_service", ms)
    _register(store, _REPORTER)
    _register(store, _OTHER_PAYER)

    gate_called: list[bool] = []

    def _gate(*_a: object, **_kw: object) -> x402_guard.PaymentResult:
        gate_called.append(True)
        return _settled_result(payer=_REPORTER, txid="TX-R")

    monkeypatch.setattr(social_routes, "require_paid_request", _gate)
    monkeypatch.setattr(social_routes, "mark_fulfilled", lambda *_a, **_kw: None)

    response = social_routes.x402_social_report_create(
        _request(
            body=json.dumps(
                {"target_type": "agent", "target_id": _OTHER_PAYER, "category": "spam"}
            ).encode(),
            path="/api/v1/x402/social/reports",
        )
    )
    assert gate_called == [True]
    assert response.status_code == 200


# --------------------------------------------------------------------------- #
# S2-2: open-report concurrency cap
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis")
def test_third_open_report_while_two_are_open_is_caller_fault_payment_kept(
    store: InMemorySocialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The open-report concurrency cap (design doc section 5.4.1): a 3rd report while 2 are already open settles but is refused (409), payment kept, NO refund attempted -- mirrors the group-name-collision route test's exact shape."""
    ms = _moderation_service(store)
    monkeypatch.setattr(social_routes, "moderation_service", ms)
    monkeypatch.setattr(settings, "x402_social_report_max_open", 2)
    _register(store, _REPORTER)
    targets = [_OTHER_PAYER, _VOTER_A, _VOTER_B]
    for t in targets:
        _register(store, t)

    fulfilled: list[tuple[str, str]] = []
    monkeypatch.setattr(
        social_routes,
        "mark_fulfilled",
        lambda txid, *, resource: fulfilled.append((txid, resource)),
    )
    refund_calls: list[dict[str, object]] = []
    monkeypatch.setattr(payment_service, "send_refund", lambda **kw: refund_calls.append(kw))

    def _report(target: str, txid: str) -> Response:
        monkeypatch.setattr(
            social_routes,
            "require_paid_request",
            lambda *_a, **_kw: _settled_result(payer=_REPORTER, txid=txid),
        )
        body = json.dumps(
            {"target_type": "agent", "target_id": target, "category": "spam"}
        ).encode()
        return social_routes.x402_social_report_create(
            _request(body=body, path="/api/v1/x402/social/reports")
        )

    first = _report(targets[0], "TX-R1")
    second = _report(targets[1], "TX-R2")
    assert first.status_code == 200
    assert second.status_code == 200

    third = _report(targets[2], "TX-R3")

    assert third.status_code == 409
    body = json.loads(third.description)
    assert body["error"]["code"] == "too_many_open_reports"
    # Settlement headers (the receipt) are still attached -- the 3rd payment
    # WAS settled on-chain, only the product write was refused.
    assert third.headers.get("PAYMENT-RESPONSE") == "ok"
    assert fulfilled == [("TX-R1", "x402-social-report"), ("TX-R2", "x402-social-report")]
    assert refund_calls == []


def test_open_report_on_an_already_open_target_releases_the_just_claimed_slot() -> None:
    """A report that loses the one-open-case-per-target race never legitimately opened a case, so it must NOT count against the reporter's own concurrency cap -- the slot claimed for it is released again."""
    store = InMemorySocialStore()
    ms = _moderation_service(store)
    _register(store, _REPORTER)
    _register(store, _OTHER_PAYER)

    first = ms.open_report(
        reporter=_REPORTER,
        target_type=TARGET_AGENT,
        target_id=_OTHER_PAYER,
        category="spam",
        note="",
        settlement_tx_id="TX-1",
    )
    with pytest.raises(SocialError) as exc_info:
        ms.open_report(
            reporter=_REPORTER,
            target_type=TARGET_AGENT,
            target_id=_OTHER_PAYER,
            category="harassment",
            note="",
            settlement_tx_id="TX-2",
        )
    assert exc_info.value.code == "case_already_open"
    assert first.case_id in exc_info.value.message

    # The failed attempt released its slot -- the reporter still has room
    # for a real 2nd report against a DIFFERENT target.
    _register(store, _VOTER_A)
    second = ms.open_report(
        reporter=_REPORTER,
        target_type=TARGET_AGENT,
        target_id=_VOTER_A,
        category="spam",
        note="",
        settlement_tx_id="TX-3",
    )
    assert second.case_id != first.case_id


# --------------------------------------------------------------------------- #
# A1 (2026-09-03): a mid-write open_report failure must release both claims
# it already won, not leak them forever.
# --------------------------------------------------------------------------- #
def test_open_report_releases_both_claims_when_insert_case_fails_mid_write() -> None:
    """If insert_case throws after the reporter-slot claim and the one-open-case-per-target claim have both already won, both must be released -- otherwise the target becomes permanently unreportable and the reporter permanently loses one of their max_open slots, since neither claim has a TTL and both are normally released only by a case's own resolution, which never happens for a case that was never durably stored."""
    store = InMemorySocialStore()
    ms = _moderation_service(store)
    _register(store, _REPORTER)
    _register(store, _OTHER_PAYER)

    def _boom(_item: StoredCase) -> None:
        raise RuntimeError("simulated insert_case failure")

    original_insert_case = store.insert_case
    store.insert_case = _boom  # type: ignore[method-assign]
    try:
        with pytest.raises(RuntimeError, match="simulated insert_case failure"):
            ms.open_report(
                reporter=_REPORTER,
                target_type=TARGET_AGENT,
                target_id=_OTHER_PAYER,
                category="spam",
                note="",
                settlement_tx_id="TX-BOOM",
            )
    finally:
        store.insert_case = original_insert_case  # type: ignore[method-assign]

    # The target's open-case claim was released -- a fresh report against
    # the SAME target succeeds instead of permanently hitting
    # case_already_open.
    retried = ms.open_report(
        reporter=_REPORTER,
        target_type=TARGET_AGENT,
        target_id=_OTHER_PAYER,
        category="spam",
        note="",
        settlement_tx_id="TX-RETRY",
    )
    assert retried.target_id == _OTHER_PAYER

    # The reporter's slot from the failed attempt was released too: with
    # x402_social_report_max_open == 2 and one slot now legitimately used by
    # the successful retry above, the reporter still has room for one more
    # -- if the failed attempt's slot had leaked, this would raise
    # too_many_open_reports instead.
    _register(store, _VOTER_A)
    second = ms.open_report(
        reporter=_REPORTER,
        target_type=TARGET_AGENT,
        target_id=_VOTER_A,
        category="spam",
        note="",
        settlement_tx_id="TX-SECOND",
    )
    assert second.case_id != retried.case_id


# --------------------------------------------------------------------------- #
# S2-3: case resolution -- both category branches
# --------------------------------------------------------------------------- #
def test_case_resolution_upheld_non_illegal_content_tombstones_the_post() -> None:
    """quorum+ratio met, category != illegal_content -> hidden_platform=true, never a row delete, no removal audit record (design doc section 5.3 step 4)."""
    store = InMemorySocialStore()
    ms = _moderation_service(store)
    settings_quorum, ratio_num, ratio_den = 3, 1, 2

    _register(store, _REPORTER)
    _register(store, _OTHER_PAYER)  # post author
    opened = _now_epoch()
    for w in (_VOTER_A, _VOTER_B, _VOTER_C):
        _register(store, w, created_at_epoch=opened - 100)

    post = ms.post_service.create(
        author=_OTHER_PAYER, body_md="spammy content", tags=[], group_id="", settlement_tx_id="TX-P"
    )
    case = ms.open_report(
        reporter=_REPORTER,
        target_type=TARGET_POST,
        target_id=post.post_id,
        category=CATEGORY_NOT_HELPFUL,
        note="",
        settlement_tx_id="TX-REP",
    )

    with (
        patch.object(settings, "x402_social_case_quorum", settings_quorum),
        patch.object(settings, "x402_social_case_uphold_ratio_numerator", ratio_num),
        patch.object(settings, "x402_social_case_uphold_ratio_denominator", ratio_den),
    ):
        ms.cast_vote(
            case_id=case.case_id, voter=_VOTER_A, verdict="uphold", settlement_tx_id="TX-VA"
        )
        ms.cast_vote(
            case_id=case.case_id, voter=_VOTER_B, verdict="uphold", settlement_tx_id="TX-VB"
        )
        ms.cast_vote(
            case_id=case.case_id, voter=_VOTER_C, verdict="reject", settlement_tx_id="TX-VC"
        )
        resolved = _resolve_now(ms, case)

    assert resolved.state == CASE_STATE_UPHELD
    stored_post = store.get_post(post.post_id)
    assert stored_post is not None  # never a row delete for a non-illegal_content verdict
    assert stored_post.hidden_platform is True
    assert stored_post.deleted is False
    assert store.get_removal(case.case_id) is None
    assert resolved.content_snapshot == "spammy content"  # untouched, never scrubbed


def test_case_resolution_upheld_illegal_content_hard_deletes_the_post() -> None:
    """quorum+ratio met, category == illegal_content -> the ONE row-delete exception (design doc section 5.4.2): the post is gone, a removal audit record exists, and the case's content_snapshot is scrubbed to the fixed placeholder -- never the real reported text."""
    store = InMemorySocialStore()
    ms = _moderation_service(store)

    _register(store, _REPORTER)
    _register(store, _OTHER_PAYER)
    opened = _now_epoch()
    for w in (_VOTER_A, _VOTER_B, _VOTER_C):
        _register(store, w, created_at_epoch=opened - 100)

    post = ms.post_service.create(
        author=_OTHER_PAYER,
        body_md="genuinely illegal content",
        tags=[],
        group_id="",
        settlement_tx_id="TX-P",
    )
    case = ms.open_report(
        reporter=_REPORTER,
        target_type=TARGET_POST,
        target_id=post.post_id,
        category=CATEGORY_ILLEGAL_CONTENT,
        note="",
        settlement_tx_id="TX-REP",
    )

    with (
        patch.object(settings, "x402_social_case_quorum", 3),
        patch.object(settings, "x402_social_case_uphold_ratio_numerator", 1),
        patch.object(settings, "x402_social_case_uphold_ratio_denominator", 2),
    ):
        ms.cast_vote(
            case_id=case.case_id, voter=_VOTER_A, verdict="uphold", settlement_tx_id="TX-VA"
        )
        ms.cast_vote(
            case_id=case.case_id, voter=_VOTER_B, verdict="uphold", settlement_tx_id="TX-VB"
        )
        ms.cast_vote(
            case_id=case.case_id, voter=_VOTER_C, verdict="uphold", settlement_tx_id="TX-VC"
        )
        resolved = _resolve_now(ms, case)

    assert resolved.state == CASE_STATE_UPHELD
    # The content is gone...
    assert store.get_post(post.post_id) is None
    # ...but the audit record survives it, per the store-before-mark
    # discipline (design doc section 5.4.2: "the record that something was
    # removed, and why, must survive even though the content does not").
    removal = store.get_removal(case.case_id)
    assert removal is not None
    assert removal.removed_by == REMOVED_BY_COMMUNITY_VOTE
    assert removal.target_type == TARGET_POST
    assert removal.target_id == post.post_id
    assert removal.category == CATEGORY_ILLEGAL_CONTENT
    assert removal.uphold_votes == 3
    assert removal.reject_votes == 0
    # The case row itself never keeps archiving the removed material.
    assert resolved.content_snapshot == HARD_DELETE_SNAPSHOT_PLACEHOLDER
    assert "illegal content" not in resolved.content_snapshot


# --------------------------------------------------------------------------- #
# S2-4: quorum failure -- not-upheld with a worded resolution_note, reporter
# throttle escalation
# --------------------------------------------------------------------------- #
def test_case_resolution_quorum_failure_is_not_upheld_with_a_worded_note_and_escalates_the_reporter() -> (
    None
):
    """Window expires without reaching quorum -> rejected, a public resolution_note names the shortfall (design doc section 5.3/5.6 Q8), and the reporter's rejection streak + cooldown escalate (section 5.4.1)."""
    store = InMemorySocialStore()
    ms = _moderation_service(store)
    _register(store, _REPORTER)
    _register(store, _OTHER_PAYER)
    opened = _now_epoch()
    _register(store, _VOTER_A, created_at_epoch=opened - 100)

    case = ms.open_report(
        reporter=_REPORTER,
        target_type=TARGET_AGENT,
        target_id=_OTHER_PAYER,
        category="spam",
        note="",
        settlement_tx_id="TX-REP",
    )
    with patch.object(settings, "x402_social_case_quorum", 3):
        # Only ONE vote cast -- below quorum.
        ms.cast_vote(
            case_id=case.case_id, voter=_VOTER_A, verdict="uphold", settlement_tx_id="TX-VA"
        )
        resolved = _resolve_now(ms, case)

    assert resolved.state == CASE_STATE_REJECTED
    assert "did not reach quorum" in resolved.resolution_note
    assert "1 of 3" in resolved.resolution_note

    standing = store.get_standing(_REPORTER)
    assert standing.rejected_report_count == 1
    assert standing.report_rejection_streak == 1
    # base 900s (15m) for the FIRST rejection (streak=1) -- section 5.4.1.
    assert standing.report_cooldown_until_epoch == resolved.resolved_at_epoch + 900


# --------------------------------------------------------------------------- #
# A5 (2026-09-03): an exact two-thirds vote split must resolve upheld.
# --------------------------------------------------------------------------- #
def test_case_resolution_upholds_an_exact_two_thirds_split_at_the_default_ratio() -> None:
    """The default settings (x402_social_case_quorum=5, uphold ratio 2/3) must resolve UPHELD on an exact 4-of-6 split.

    Regression for A5: the old single-float setting
    (x402_social_case_uphold_ratio = 0.667) compared
    `(tally.uphold / total) >= ratio` -- 4/6 == 0.6666...  which is strictly
    LESS than the float literal 0.667, so this exact case resolved REJECTED
    even though "at least two-thirds" was the evident intent. The fixed
    exact-integer comparison (`tally.uphold * ratio_denominator >=
    ratio_numerator * total`) has no such float boundary.
    """
    store = InMemorySocialStore()
    ms = _moderation_service(store)
    _register(store, _REPORTER)
    _register(store, _OTHER_PAYER)
    opened = _now_epoch()
    voters = [_VOTER_A, _VOTER_B, _VOTER_C, _VOTER_D]
    voter_e = encode_address(bytes([9]) + bytes(31))
    voter_f = encode_address(bytes([10]) + bytes(31))
    voters += [voter_e, voter_f]
    for w in voters:
        _register(store, w, created_at_epoch=opened - 100)

    case = ms.open_report(
        reporter=_REPORTER,
        target_type=TARGET_AGENT,
        target_id=_OTHER_PAYER,
        category="spam",
        note="",
        settlement_tx_id="TX-REP",
    )
    # Uses the process-wide DEFAULT settings deliberately -- this is the
    # exact boundary the shipped config must get right, not a patched one.
    assert settings.x402_social_case_quorum == 5
    assert settings.x402_social_case_uphold_ratio_numerator == 2
    assert settings.x402_social_case_uphold_ratio_denominator == 3
    verdicts = ["uphold", "uphold", "uphold", "uphold", "reject", "reject"]
    for voter, verdict in zip(voters, verdicts, strict=True):
        ms.cast_vote(
            case_id=case.case_id, voter=voter, verdict=verdict, settlement_tx_id=f"TX-{voter}"
        )
    resolved = _resolve_now(ms, case)

    assert resolved.state == CASE_STATE_UPHELD
    assert "upheld: 4 of 6" in resolved.resolution_note


def test_report_cooldown_progression_matches_the_exact_15m_30m_1h_sequence_and_the_7day_cap() -> (
    None
):
    """Pins the design doc section 5.4.1 worked sequence exactly: 15m, 30m, 1h, 2h, 4h, 8h, 16h, ~1.3d, ~2.7d, ~5.3d, 7d (cap, reached at the 10th consecutive rejection)."""
    base, cap = 900, 604800
    expected_seconds = [
        900,
        1800,
        3600,
        7200,
        14400,
        28800,
        57600,
        115200,
        230400,
        460800,
    ]
    for streak, expected in enumerate(expected_seconds, start=1):
        got = compute_report_cooldown_seconds(
            streak, base_seconds=base, multiplier=2, cap_seconds=cap
        )
        assert got == expected, f"streak={streak}"
    # The 10th consecutive rejection is streak=10 -> 900*2**9 = 460800s
    # (~5.3d), still under the 7-day cap; the NEXT one (streak=11) is where
    # the formula would exceed it and gets clamped.
    assert (
        compute_report_cooldown_seconds(11, base_seconds=base, multiplier=2, cap_seconds=cap) == cap
    )
    assert (
        compute_report_cooldown_seconds(50, base_seconds=base, multiplier=2, cap_seconds=cap) == cap
    )


def test_ban_formula_matches_the_exact_design_doc_sequence_and_the_30day_cap() -> None:
    """Pins the design doc section 5.4 worked sequence exactly: 30m -> 2h -> 8h -> 32h -> ~5.3d -> ~21d -> 30d (cap)."""
    base, multiplier, cap = 1800, 4, 2592000
    expected = [1800, 7200, 28800, 115200, 460800, 1843200, cap]
    for offenses_in_window, want in enumerate(expected):
        got = compute_ban_seconds(
            offenses_in_window, base_seconds=base, multiplier=multiplier, cap_seconds=cap
        )
        assert got == want, f"offenses_in_window={offenses_in_window}"


# --------------------------------------------------------------------------- #
# S2-5: an upheld report resets the reporter's rejection streak to 0
# --------------------------------------------------------------------------- #
def test_upheld_report_resets_the_reporters_rejection_streak_to_zero() -> None:
    """Design doc section 5.4.1: an upheld report resets streak to 0, chosen over merely not-incrementing -- the reporter's lifetime rejected_report_count is untouched, only the streak."""
    store = InMemorySocialStore()
    ms = _moderation_service(store)
    _register(store, _REPORTER)
    # Pre-existing streak from earlier (unrelated) rejected reports.
    store.upsert_standing(
        StoredStanding(wallet=_REPORTER, report_rejection_streak=3, rejected_report_count=3)
    )
    _register(store, _OTHER_PAYER)
    opened = _now_epoch()
    for w in (_VOTER_A, _VOTER_B, _VOTER_C):
        _register(store, w, created_at_epoch=opened - 100)

    case = ms.open_report(
        reporter=_REPORTER,
        target_type=TARGET_AGENT,
        target_id=_OTHER_PAYER,
        category="harassment",
        note="",
        settlement_tx_id="TX-REP",
    )
    with (
        patch.object(settings, "x402_social_case_quorum", 3),
        patch.object(settings, "x402_social_case_uphold_ratio_numerator", 1),
        patch.object(settings, "x402_social_case_uphold_ratio_denominator", 2),
    ):
        for voter in (_VOTER_A, _VOTER_B, _VOTER_C):
            ms.cast_vote(
                case_id=case.case_id, voter=voter, verdict="uphold", settlement_tx_id=f"TX-{voter}"
            )
        resolved = _resolve_now(ms, case)

    assert resolved.state == CASE_STATE_UPHELD
    standing = store.get_standing(_REPORTER)
    assert standing.report_rejection_streak == 0
    # The prior lifetime count is untouched -- only the STREAK resets
    # (design doc section 5.4.1's own reset-vs-not argument).
    assert standing.rejected_report_count == 3


# --------------------------------------------------------------------------- #
# S2-6: karma -- votes_cast / votes_matched_resolution
# --------------------------------------------------------------------------- #
def test_karma_votes_cast_and_matched_resolution_update_per_voter() -> None:
    """Every voter's votes_cast increments by one; votes_matched_resolution increments too only for voters whose verdict matched the final outcome (design doc section 5.3's resolver bookkeeping note)."""
    store = InMemorySocialStore()
    ms = _moderation_service(store)
    _register(store, _REPORTER)
    _register(store, _OTHER_PAYER)
    opened = _now_epoch()
    for w in (_VOTER_A, _VOTER_B, _VOTER_C):
        _register(store, w, created_at_epoch=opened - 100)

    case = ms.open_report(
        reporter=_REPORTER,
        target_type=TARGET_AGENT,
        target_id=_OTHER_PAYER,
        category="spam",
        note="",
        settlement_tx_id="TX-REP",
    )
    with (
        patch.object(settings, "x402_social_case_quorum", 3),
        patch.object(settings, "x402_social_case_uphold_ratio_numerator", 1),
        patch.object(settings, "x402_social_case_uphold_ratio_denominator", 2),
    ):
        ms.cast_vote(
            case_id=case.case_id, voter=_VOTER_A, verdict="uphold", settlement_tx_id="TX-VA"
        )
        ms.cast_vote(
            case_id=case.case_id, voter=_VOTER_B, verdict="uphold", settlement_tx_id="TX-VB"
        )
        ms.cast_vote(
            case_id=case.case_id, voter=_VOTER_C, verdict="reject", settlement_tx_id="TX-VC"
        )
        resolved = _resolve_now(ms, case)

    assert resolved.state == CASE_STATE_UPHELD
    for matched_voter in (_VOTER_A, _VOTER_B):
        standing = store.get_standing(matched_voter)
        assert standing.votes_cast == 1
        assert standing.votes_matched_resolution == 1
    mismatched = store.get_standing(_VOTER_C)
    assert mismatched.votes_cast == 1
    assert mismatched.votes_matched_resolution == 0


# --------------------------------------------------------------------------- #
# S2-7: the section 8.1 admin lever
# --------------------------------------------------------------------------- #
def test_admin_lever_hard_deletes_and_writes_the_same_removals_shape() -> None:
    """moderation_service.admin_remove(hard_delete=True) shares the exact hard-delete mechanics the community resolver uses -- same removals table shape, removed_by='admin_lever', 0/0 votes (design doc section 5.4.2's own schema note)."""
    store = InMemorySocialStore()
    ms = _moderation_service(store)
    _register(store, _OTHER_PAYER)
    post = ms.post_service.create(
        author=_OTHER_PAYER,
        body_md="csam-scope content",
        tags=[],
        group_id="",
        settlement_tx_id="TX-P",
    )

    removal = ms.admin_remove(
        target_type=TARGET_POST,
        target_id=post.post_id,
        category=CATEGORY_ILLEGAL_CONTENT,
        hard_delete=True,
    )

    assert removal is not None
    assert removal.removed_by == REMOVED_BY_ADMIN_LEVER
    assert removal.uphold_votes == 0
    assert removal.reject_votes == 0
    assert removal.target_type == TARGET_POST
    assert removal.target_id == post.post_id
    assert store.get_post(post.post_id) is None
    fetched = store.get_removal(removal.case_id)
    assert fetched is not None
    assert fetched.removed_by == REMOVED_BY_ADMIN_LEVER


def test_admin_lever_hard_deletes_a_group_and_every_one_of_its_posts() -> None:
    """Design doc section 5.4.2 -- the owner's incitement-to-genocide example: hard-deleting a group removes the group row, its name claim, its membership, and EVERY post in its feed, not just the group shell."""
    store = InMemorySocialStore()
    ms = _moderation_service(store)
    _register(store, _OTHER_PAYER)
    group = ms.group_service.create(
        owner=_OTHER_PAYER, name="bad-group", description="", settlement_tx_id="TX-G"
    )
    posts = [
        ms.post_service.create(
            author=_OTHER_PAYER,
            body_md=f"post {i}",
            tags=[],
            group_id=group.group_id,
            settlement_tx_id=f"TX-P{i}",
        )
        for i in range(3)
    ]

    removal = ms.admin_remove(
        target_type=TARGET_GROUP,
        target_id=group.group_id,
        category=CATEGORY_ILLEGAL_CONTENT,
        hard_delete=True,
    )

    assert removal is not None
    assert removal.removed_by == REMOVED_BY_ADMIN_LEVER
    assert removal.target_type == TARGET_GROUP
    assert store.get_group(group.group_id) is None
    # The name is freed -- re-claiming it costs the full price again.
    assert ms.group_service.store.try_claim_group_name(
        name_norm="bad-group", group_id="some-other-id"
    )
    assert store.get_membership(group.group_id, _OTHER_PAYER) is None
    for post in posts:
        assert store.get_post(post.post_id) is None


def test_admin_lever_route_requires_admin_wallet_and_refuses_hard_delete_outside_illegal_content() -> (
    None
):
    """The admin lever ROUTE (backend/app/modules/admin/api/routes.py): refused without an admin session, and hard_delete=True is refused outside the illegal_content category scope -- design doc section 5.4.2 says there is no admin or voter discretion to hard-delete under any other category."""
    store = InMemorySocialStore()
    ms = _moderation_service(store)

    with patch.object(admin_routes, "moderation_service", ms):
        # No admin session at all.
        denied = admin_routes.admin_x402_social_moderation_remove(
            Request(
                method="POST",
                headers={},
                query_params=QueryParams({}),
                path_params={},
                body=json.dumps(
                    {"target_type": "agent", "target_id": _OTHER_PAYER, "category": "spam"}
                ).encode(),
                url=SimpleNamespace(
                    scheme="http",
                    host="localhost",
                    path="/api/v1/admin/x402-social/moderation/remove",
                ),
            )
        )
        assert denied.status_code in (401, 403, 503)

        # Admin session present, but hard_delete=True outside illegal_content.
        with patch.object(admin_routes, "require_admin_wallet", return_value=None):
            refused = admin_routes.admin_x402_social_moderation_remove(
                Request(
                    method="POST",
                    headers={},
                    query_params=QueryParams({}),
                    path_params={},
                    body=json.dumps(
                        {
                            "target_type": "agent",
                            "target_id": _OTHER_PAYER,
                            "category": "spam",
                            "hard_delete": True,
                        }
                    ).encode(),
                    url=SimpleNamespace(
                        scheme="http",
                        host="localhost",
                        path="/api/v1/admin/x402-social/moderation/remove",
                    ),
                )
            )
            assert refused.status_code == 400


def test_admin_lever_route_hard_deletes_a_post_end_to_end() -> None:
    """The full admin route path, admin-authenticated, category=illegal_content, hard_delete=True -- the post is gone and a removal record with removed_by='admin_lever' is written."""
    store = InMemorySocialStore()
    ms = _moderation_service(store)
    _register(store, _OTHER_PAYER)
    post = ms.post_service.create(
        author=_OTHER_PAYER, body_md="content", tags=[], group_id="", settlement_tx_id="TX-P"
    )

    with (
        patch.object(admin_routes, "moderation_service", ms),
        patch.object(admin_routes, "require_admin_wallet", return_value=None),
        patch.object(admin_routes, "verified_admin_wallet", return_value=_PAYER),
    ):
        response = admin_routes.admin_x402_social_moderation_remove(
            Request(
                method="POST",
                headers={},
                query_params=QueryParams({}),
                path_params={},
                body=json.dumps(
                    {
                        "target_type": "post",
                        "target_id": post.post_id,
                        "category": "illegal_content",
                        "hard_delete": True,
                        "reason": "authority request",
                    }
                ).encode(),
                url=SimpleNamespace(
                    scheme="http",
                    host="localhost",
                    path="/api/v1/admin/x402-social/moderation/remove",
                ),
            )
        )

    assert response["hard_deleted"] is True
    assert response["removed_by"] == REMOVED_BY_ADMIN_LEVER
    assert store.get_post(post.post_id) is None


# --------------------------------------------------------------------------- #
# S2-8: x402_social_moderation_enabled=False keeps S2 routes unregistered
# --------------------------------------------------------------------------- #
_RouteHandler = Callable[..., object]


class _FakeRouter:
    """Just enough of app.core.http.Router to record every (method, path) registered -- see that Protocol's own definition."""

    def __init__(self) -> None:
        self.registered: list[tuple[str, str]] = []

    def _record(self, method: str, path: str) -> Callable[[_RouteHandler], _RouteHandler]:
        def _decorator(fn: _RouteHandler) -> _RouteHandler:
            self.registered.append((method, path))
            return fn

        return _decorator

    def get(self, path: str) -> Callable[[_RouteHandler], _RouteHandler]:
        return self._record("GET", path)

    def post(self, path: str) -> Callable[[_RouteHandler], _RouteHandler]:
        return self._record("POST", path)

    def patch(self, path: str) -> Callable[[_RouteHandler], _RouteHandler]:
        return self._record("PATCH", path)

    def delete(self, path: str) -> Callable[[_RouteHandler], _RouteHandler]:
        return self._record("DELETE", path)

    def put(self, path: str) -> Callable[[_RouteHandler], _RouteHandler]:
        return self._record("PUT", path)

    def head(self, path: str) -> Callable[[_RouteHandler], _RouteHandler]:
        return self._record("HEAD", path)


def test_moderation_disabled_keeps_s2_routes_unregistered_but_s0_s1_stay_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """settings.x402_social_moderation_enabled=False (the shipped default) means register_x402_social_routes never registers POST /reports, GET /cases, GET /cases/{id}, POST /cases/{id}/vote, or GET /agents/{wallet}/standing -- mirrors the x402_social_store gate's own shape in falcon_main.py -- while every S0/S1 route still registers."""
    monkeypatch.setattr(settings, "x402_social_moderation_enabled", False)
    router = _FakeRouter()
    social_routes.register_x402_social_routes(router)

    s2_paths = {
        ("POST", "/api/v1/x402/social/reports"),
        ("GET", "/api/v1/x402/social/cases"),
        ("GET", "/api/v1/x402/social/cases/:case_id"),
        ("POST", "/api/v1/x402/social/cases/:case_id/vote"),
        ("GET", "/api/v1/x402/social/agents/:wallet/standing"),
    }
    assert s2_paths.isdisjoint(router.registered)
    # S0/S1 unaffected by the S2 flag.
    assert ("POST", "/api/v1/x402/social/register") in router.registered
    assert ("POST", "/api/v1/x402/social/posts") in router.registered


def test_moderation_enabled_registers_s2_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The flip side: settings.x402_social_moderation_enabled=True registers every S2 route."""
    monkeypatch.setattr(settings, "x402_social_moderation_enabled", True)
    router = _FakeRouter()
    social_routes.register_x402_social_routes(router)

    s2_paths = {
        ("POST", "/api/v1/x402/social/reports"),
        ("GET", "/api/v1/x402/social/cases"),
        ("GET", "/api/v1/x402/social/cases/:case_id"),
        ("POST", "/api/v1/x402/social/cases/:case_id/vote"),
        ("GET", "/api/v1/x402/social/agents/:wallet/standing"),
    }
    assert s2_paths.issubset(router.registered)


# --------------------------------------------------------------------------- #
# Promo-code wiring, REVERSED 2026-09-03 (see routes.py's own module
# docstring): every S0/S1 write route used to forward ?promo=/?promo_wallet=
# into require_paid_request (root-caused 2026-09-03, same day) until a
# security review found that a promo-bypassed PaymentResult.payer is only
# SYNTACTICALLY checked (modules/x402/promo.py's own docstring: "not proof
# the caller controls that wallet"), but every route below feeds payer
# straight in as the ACTING IDENTITY -- letting anyone register, post,
# follow, report or vote as any wallet via `?promo_wallet=<victim>`. Closed
# the same day by simply never reading promo params here at all. These
# tests now lock in the opposite invariant: promo params in the query
# string are ignored, not honored.
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis")
@pytest.mark.parametrize(
    "route_name",
    [
        "x402_social_register",
        "x402_social_post_create",
        "x402_social_comment_create",
        "x402_social_react",
        "x402_social_follow",
        "x402_social_group_create",
        "x402_social_group_join",
    ],
)
def test_every_s0_s1_write_route_never_forwards_promo_params(
    store: InMemorySocialStore,
    monkeypatch: pytest.MonkeyPatch,
    route_name: str,
) -> None:
    """No S0/S1 paid write route may pass ?promo=/?promo_wallet= into require_paid_request, even when present in the query string -- payer is an identity here, and promo cannot prove wallet ownership (see the section comment above)."""
    post_service = PostService(store, is_registered=_always_registered)
    group_service = GroupService(store, is_registered=_always_registered)
    monkeypatch.setattr(social_routes, "profile_service", ProfileService(store))
    monkeypatch.setattr(social_routes, "post_service", post_service)
    monkeypatch.setattr(social_routes, "group_service", group_service)
    # A pre-existing post/group so the react/comment/join branches have
    # something real to act on.
    post = post_service.create(
        author=_OTHER_PAYER, body_md="seed", tags=[], group_id="", settlement_tx_id="TX-SEED-P"
    )
    group = group_service.create(
        owner=_OTHER_PAYER, name="Seed Group", description="", settlement_tx_id="TX-SEED-G"
    )

    routes_by_name = {
        "x402_social_register": (
            "/api/v1/x402/social/register",
            {"body": b'{"name": "A"}'},
        ),
        "x402_social_post_create": (
            "/api/v1/x402/social/posts",
            {"body": b'{"body_md": "hi"}'},
        ),
        "x402_social_comment_create": (
            f"/api/v1/x402/social/posts/{post.post_id}/comments",
            {"body": b'{"body_md": "hi"}', "path_params": {"post_id": post.post_id}},
        ),
        "x402_social_react": (
            f"/api/v1/x402/social/posts/{post.post_id}/react",
            {"body": b'{"value": "up"}', "path_params": {"post_id": post.post_id}},
        ),
        "x402_social_follow": (
            f"/api/v1/x402/social/agents/{_OTHER_PAYER}/follow",
            {"path_params": {"wallet": _OTHER_PAYER}},
        ),
        "x402_social_group_create": (
            "/api/v1/x402/social/groups",
            {"body": b'{"name": "G"}'},
        ),
        "x402_social_group_join": (
            f"/api/v1/x402/social/groups/{group.group_id}/join",
            {"path_params": {"group_id": group.group_id}},
        ),
    }
    path, extra_kwargs = routes_by_name[route_name]

    captured: dict = {}

    def _spy_require_paid_request(*_args: object, **kwargs: object) -> object:
        captured.update(kwargs)
        return _settled_result(payer=_PAYER)

    monkeypatch.setattr(social_routes, "require_paid_request", _spy_require_paid_request)
    monkeypatch.setattr(social_routes, "mark_fulfilled", lambda *_a, **_kw: None)

    route = getattr(social_routes, route_name)
    route(
        _request(
            query={"promo": "LAUNCH1000-TEST", "promo_wallet": _PAYER},
            path=path,
            **extra_kwargs,
        )
    )

    assert "promo_code" not in captured
    assert "promo_wallet" not in captured


@pytest.mark.usefixtures("fake_redis")
@pytest.mark.parametrize(
    "route_name",
    [
        "x402_social_register",
        "x402_social_post_create",
        "x402_social_comment_create",
        "x402_social_react",
        "x402_social_follow",
        "x402_social_group_create",
        "x402_social_group_join",
    ],
)
def test_every_s0_s1_write_route_calls_mark_fulfilled_unconditionally(
    store: InMemorySocialStore,
    monkeypatch: pytest.MonkeyPatch,
    route_name: str,
) -> None:
    """mark_fulfilled runs on every successful write -- there is no is_promo guard any more.

    The guard (skip mark_fulfilled when result.is_promo) was removed along with promo wiring
    itself (see the section comment above): a real require_paid_request can never return
    is_promo=True here, since promo_code is never passed to it. This test uses a plain settled
    (non-promo) result, matching what the route can actually receive in production.
    """
    post_service = PostService(store, is_registered=_always_registered)
    group_service = GroupService(store, is_registered=_always_registered)
    profile_service = ProfileService(store)
    monkeypatch.setattr(social_routes, "profile_service", profile_service)
    monkeypatch.setattr(social_routes, "post_service", post_service)
    monkeypatch.setattr(social_routes, "group_service", group_service)
    # _PAYER (the caller below) must itself be registered for every route
    # except register itself, which would otherwise 409 as already-registered.
    if route_name != "x402_social_register":
        profile_service.register(
            wallet=_PAYER,
            name="Caller",
            bio="",
            mission="",
            location="",
            interests=[],
            emoji="",
            settlement_tx_id="TX-SEED-CALLER",
        )
    post = post_service.create(
        author=_OTHER_PAYER, body_md="seed", tags=[], group_id="", settlement_tx_id="TX-SEED-P2"
    )
    group = group_service.create(
        owner=_OTHER_PAYER, name="Seed Group 2", description="", settlement_tx_id="TX-SEED-G2"
    )

    routes_by_name = {
        "x402_social_register": (
            "/api/v1/x402/social/register",
            {"body": b'{"name": "B"}'},
        ),
        "x402_social_post_create": (
            "/api/v1/x402/social/posts",
            {"body": b'{"body_md": "hi"}'},
        ),
        "x402_social_comment_create": (
            f"/api/v1/x402/social/posts/{post.post_id}/comments",
            {"body": b'{"body_md": "hi"}', "path_params": {"post_id": post.post_id}},
        ),
        "x402_social_react": (
            f"/api/v1/x402/social/posts/{post.post_id}/react",
            {"body": b'{"value": "up"}', "path_params": {"post_id": post.post_id}},
        ),
        "x402_social_follow": (
            f"/api/v1/x402/social/agents/{_OTHER_PAYER}/follow",
            {"path_params": {"wallet": _OTHER_PAYER}},
        ),
        "x402_social_group_create": (
            "/api/v1/x402/social/groups",
            {"body": b'{"name": "G2"}'},
        ),
        "x402_social_group_join": (
            f"/api/v1/x402/social/groups/{group.group_id}/join",
            {"path_params": {"group_id": group.group_id}},
        ),
    }
    path, extra_kwargs = routes_by_name[route_name]

    monkeypatch.setattr(
        social_routes,
        "require_paid_request",
        lambda *_a, **_kw: _settled_result(payer=_PAYER, txid="TX-REAL"),
    )
    mark_fulfilled_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        social_routes,
        "mark_fulfilled",
        lambda txid, *, resource: mark_fulfilled_calls.append((txid, resource)),
    )

    route = getattr(social_routes, route_name)
    response = route(_request(path=path, **extra_kwargs))

    assert response.status_code == 200
    assert mark_fulfilled_calls == [("TX-REAL", _RESOURCE_BY_ROUTE[route_name])]


# --------------------------------------------------------------------------- #
# S2 promo wiring, REVERSED 2026-09-03: same reversal as the S0/S1 section
# above, for report/case-vote specifically. Both used to forward
# ?promo=/?promo_wallet= (root-caused, then re-caught live minutes after
# x402_social_moderation_enabled was first flipped on, same day) until the
# same security review found result.payer feeding cast_vote's `voter` and
# open_report's `reporter` directly -- a promo bypass there would have let
# anyone vote or file reports as an unproven wallet, defeating S2's whole
# anti-sockpuppet design (registered-before-the-case, no self-votes). Closed
# the same way: promo params are never read here at all any more.
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis")
def test_report_create_never_forwards_promo_params(
    store: InMemorySocialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """x402_social_report_create must never pass ?promo=/?promo_wallet= into require_paid_request."""
    ms = _moderation_service(store)
    monkeypatch.setattr(social_routes, "moderation_service", ms)
    _register(store, _REPORTER)
    _register(store, _OTHER_PAYER)

    captured: dict = {}

    def _spy_require_paid_request(*_a: object, **kwargs: object) -> x402_guard.PaymentResult:
        captured.update(kwargs)
        return _settled_result(payer=_REPORTER, txid="TX-R")

    monkeypatch.setattr(social_routes, "require_paid_request", _spy_require_paid_request)
    monkeypatch.setattr(social_routes, "mark_fulfilled", lambda *_a, **_kw: None)

    social_routes.x402_social_report_create(
        _request(
            body=json.dumps(
                {"target_type": "agent", "target_id": _OTHER_PAYER, "category": "spam"}
            ).encode(),
            query={"promo": "LAUNCH1000-TEST", "promo_wallet": _REPORTER},
            path="/api/v1/x402/social/reports",
        )
    )

    assert "promo_code" not in captured
    assert "promo_wallet" not in captured


@pytest.mark.usefixtures("fake_redis")
def test_case_vote_never_forwards_promo_params(
    store: InMemorySocialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """x402_social_case_vote must never pass ?promo=/?promo_wallet= into require_paid_request."""
    ms = _moderation_service(store)
    monkeypatch.setattr(social_routes, "moderation_service", ms)
    _register(store, _REPORTER)
    _register(store, _OTHER_PAYER)
    case = ms.open_report(
        reporter=_REPORTER,
        target_type="agent",
        target_id=_OTHER_PAYER,
        category="spam",
        note="",
        settlement_tx_id="TX-R1",
    )

    captured: dict = {}

    def _spy_require_paid_request(*_a: object, **kwargs: object) -> x402_guard.PaymentResult:
        captured.update(kwargs)
        return _settled_result(payer=_PAYER, txid="TX-V")

    monkeypatch.setattr(social_routes, "require_paid_request", _spy_require_paid_request)
    monkeypatch.setattr(social_routes, "mark_fulfilled", lambda *_a, **_kw: None)

    social_routes.x402_social_case_vote(
        _request(
            body=b'{"verdict": "uphold"}',
            query={"promo": "LAUNCH1000-TEST", "promo_wallet": _PAYER},
            path_params={"case_id": case.case_id},
            path=f"/api/v1/x402/social/cases/{case.case_id}/vote",
        )
    )

    assert "promo_code" not in captured
    assert "promo_wallet" not in captured


def test_cassandra_epoch_treats_a_naive_driver_datetime_as_utc() -> None:
    """_epoch must treat a timezone-naive datetime as UTC, not the interpreter's local zone.

    That's what the real Cassandra driver actually returns for a `timestamp` column. Root-caused
    2026-09-03 live on prod (a CEST/UTC+2 host): a report-rejection cooldown, written correctly
    via _dt(epoch) = datetime.fromtimestamp(epoch, tz=UTC), read back exactly 2 hours earlier than
    it was written. `value.timestamp()` on a naive datetime assumes the *local* system zone -- on
    a UTC+2 host, "13:18:17 wall-clock, no tzinfo" is silently read as 13:18:17 CEST = 11:18:17
    UTC, 2 hours off from the real UTC value that was actually stored. Same bug class already
    fixed once in news/stores/cassandra.py, never propagated here.

    Constructs the naive datetime explicitly rather than relying on this test's own execution
    environment happening to run in a non-UTC zone (which would make the bug invisible in CI).
    """
    naive = datetime(2026, 9, 4, 13, 18, 17)  # noqa: DTZ001 -- naive on purpose, see docstring
    assert naive.tzinfo is None
    assert social_cassandra_store._epoch(naive) == 1788527897  # the correct UTC epoch
    assert social_cassandra_store._epoch(None) == 0


# --------------------------------------------------------------------------- #
# Part B (2026-09-03): Agent Discovery Search -- GET /agents/search.
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis")
def test_agent_search_route_is_paid_and_returns_ranked_matches(
    store: InMemorySocialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /agents/search is wired through require_paid_request/run_with_refund like x402_features_demand / x402_news_search -- a successful call returns agents ranked by matching-tag count, each with matched_interests, plus a settlement_tx_id; mark_fulfilled runs exactly once. Also proves the limit clamp (AGENT_SEARCH_MAX_LIMIT)."""
    profile_service_ = ProfileService(store)
    monkeypatch.setattr(social_routes, "profile_service", profile_service_)

    profile_service_.register(
        wallet=_PAYER,
        name="DefiBot",
        bio="",
        mission="",
        location="",
        interests=["defi", "nft"],
        emoji="",
        settlement_tx_id="TX-REG1",
    )
    profile_service_.register(
        wallet=_OTHER_PAYER,
        name="NftBot",
        bio="",
        mission="",
        location="",
        interests=["nft"],
        emoji="",
        settlement_tx_id="TX-REG2",
    )

    captured: dict = {}

    def _spy_require_paid_request(*_a: object, **kwargs: object) -> x402_guard.PaymentResult:
        captured.update(kwargs)
        return _settled_result(payer=_VOTER_A, txid="TX-SEARCH")

    monkeypatch.setattr(social_routes, "require_paid_request", _spy_require_paid_request)
    fulfilled: list[tuple[str, str]] = []
    monkeypatch.setattr(
        social_routes,
        "mark_fulfilled",
        lambda txid, *, resource: fulfilled.append((txid, resource)),
    )

    response = social_routes.x402_social_agent_search(
        _request(
            method="GET",
            query={"interests": "defi,nft", "limit": "1000"},
            path="/api/v1/x402/social/agents/search",
        )
    )

    assert response.status_code == 200
    assert captured["price"] == settings.x402_social_agent_search_price
    assert captured["resource"] == social_routes._AGENT_SEARCH_RESOURCE
    body = json.loads(response.description)
    assert body["settlement_tx_id"] == "TX-SEARCH"
    assert body["query"]["limit"] == 50  # clamped, AGENT_SEARCH_MAX_LIMIT
    wallets = [a["wallet"] for a in body["agents"]]
    assert wallets[0] == _PAYER  # matches BOTH requested tags, ranked first
    assert wallets[1] == _OTHER_PAYER
    assert body["agents"][0]["matched_interests"] == ["defi", "nft"]
    assert fulfilled == [("TX-SEARCH", social_routes._AGENT_SEARCH_RESOURCE)]


@pytest.mark.usefixtures("fake_redis")
def test_agent_search_route_requires_interests_as_a_free_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing ?interests= is a free 400 -- require_paid_request must never be called; the circuit breaker now runs before this validation, like every other paid route, so the fake Redis keeps it closed."""

    def _fail_if_called(*_a: object, **_kw: object) -> Never:
        raise AssertionError(
            "require_paid_request must not be called for a missing interests param"
        )

    monkeypatch.setattr(social_routes, "require_paid_request", _fail_if_called)

    response = social_routes.x402_social_agent_search(
        _request(method="GET", path="/api/v1/x402/social/agents/search")
    )
    assert response.status_code == 400
    body = json.loads(response.description)
    assert body["error"]["code"] == "invalid_request"


@pytest.mark.usefixtures("fake_redis")
def test_agent_search_route_never_forwards_promo_params(
    store: InMemorySocialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Consistent with this module's own promo-off stance (commit f8d84a6) -- GET /agents/search never passes ?promo=/?promo_wallet= into require_paid_request either, even though payer here is only payment attribution, not identity."""
    monkeypatch.setattr(social_routes, "profile_service", ProfileService(store))

    captured: dict = {}

    def _spy_require_paid_request(*_a: object, **kwargs: object) -> x402_guard.PaymentResult:
        captured.update(kwargs)
        return _settled_result(payer=_PAYER, txid="TX-S")

    monkeypatch.setattr(social_routes, "require_paid_request", _spy_require_paid_request)
    monkeypatch.setattr(social_routes, "mark_fulfilled", lambda *_a, **_kw: None)

    social_routes.x402_social_agent_search(
        _request(
            method="GET",
            query={"interests": "defi", "promo": "LAUNCH1000-TEST", "promo_wallet": _PAYER},
            path="/api/v1/x402/social/agents/search",
        )
    )

    assert "promo_code" not in captured
    assert "promo_wallet" not in captured


@pytest.mark.usefixtures("fake_redis")
def test_agent_search_preview_never_touches_the_real_directory(
    store: InMemorySocialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """?preview=true goes through the real gate (is_preview=True), never calls profile_service.search_by_interests, and comes back as a fake exemplar hit with sentinel wallet/settlement_tx_id -- not even a real match count leaks for free."""
    profile_service_ = ProfileService(store)
    monkeypatch.setattr(social_routes, "profile_service", profile_service_)
    monkeypatch.setattr(
        profile_service_,
        "search_by_interests",
        lambda *_a, **_kw: pytest.fail("the real directory must not be queried for a preview"),
    )
    captured: dict = {}

    def _spy_require_paid_request(*_a: object, **kwargs: object) -> x402_guard.PaymentResult:
        captured.update(kwargs)
        return x402_guard.PaymentResult(error=None, is_preview=True)

    monkeypatch.setattr(social_routes, "require_paid_request", _spy_require_paid_request)
    fulfilled: list[tuple[str, str]] = []
    monkeypatch.setattr(
        social_routes,
        "mark_fulfilled",
        lambda txid, *, resource: fulfilled.append((txid, resource)),
    )

    response = social_routes.x402_social_agent_search(
        _request(
            method="GET",
            query={"interests": "defi,nft", "preview": "true"},
            path="/api/v1/x402/social/agents/search",
        )
    )

    assert response.status_code == 200
    assert captured["preview"] is True
    body = json.loads(response.description)
    assert body["settlement_tx_id"] == "<preview>"
    assert body["query"] == {
        "interests": ["defi", "nft"],
        "limit": social_routes.AGENT_SEARCH_DEFAULT_LIMIT,
    }
    assert len(body["agents"]) == 1
    hit = body["agents"][0]
    assert hit["wallet"] == "<preview>"
    assert hit["settlement_tx_id"] == "<preview>"
    assert set(hit) == set(social_routes._AGENT_OUTPUT_EXAMPLE) | {"matched_interests"}
    assert "PAYMENT-RESPONSE" not in response.headers
    assert fulfilled == []


@pytest.mark.usefixtures("fake_redis")
def test_agent_search_preview_still_gets_the_free_400_for_missing_interests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed query is a free 400 whether or not ?preview=true is present -- validation runs before the gate either way."""

    def _fail_if_called(*_a: object, **_kw: object) -> x402_guard.PaymentResult:
        raise AssertionError(
            "require_paid_request must not be called for a missing interests param"
        )

    monkeypatch.setattr(social_routes, "require_paid_request", _fail_if_called)

    response = social_routes.x402_social_agent_search(
        _request(method="GET", query={"preview": "true"}, path="/api/v1/x402/social/agents/search")
    )

    assert response.status_code == 400


def test_search_by_interests_ranks_by_match_count_then_recency(store: InMemorySocialStore) -> None:
    """profile_service.search_by_interests: an agent matching 2 tags outranks one matching only 1; among equal match counts, the more recently registered wallet wins the tiebreak."""
    profile_service_ = ProfileService(store)
    profile_service_.register(
        wallet=_PAYER,
        name="A",
        bio="",
        mission="",
        location="",
        interests=["defi"],
        emoji="",
        settlement_tx_id="TX-1",
        now=datetime.fromtimestamp(1000, tz=UTC),
    )
    profile_service_.register(
        wallet=_OTHER_PAYER,
        name="B",
        bio="",
        mission="",
        location="",
        interests=["defi", "nft"],
        emoji="",
        settlement_tx_id="TX-2",
        now=datetime.fromtimestamp(2000, tz=UTC),
    )
    profile_service_.register(
        wallet=_REPORTER,
        name="C",
        bio="",
        mission="",
        location="",
        interests=["defi"],
        emoji="",
        settlement_tx_id="TX-3",
        now=datetime.fromtimestamp(3000, tz=UTC),
    )

    results = profile_service_.search_by_interests(["defi", "nft"], limit=10)
    wallets = [p.wallet for p, _matched in results]

    assert wallets[0] == _OTHER_PAYER  # matches both requested tags
    # Both A and C match only "defi" -- C (registered later) wins the tiebreak.
    assert wallets[1:] == [_REPORTER, _PAYER]
    matched = {p.wallet: m for p, m in results}
    assert matched[_OTHER_PAYER] == ["defi", "nft"]
    assert matched[_PAYER] == ["defi"]


def test_search_by_interests_is_any_match_not_all_match(store: InMemorySocialStore) -> None:
    """An agent matching only ONE of several requested tags is still a candidate (ANY-match, not ALL-match)."""
    profile_service_ = ProfileService(store)
    profile_service_.register(
        wallet=_PAYER,
        name="A",
        bio="",
        mission="",
        location="",
        interests=["gaming"],
        emoji="",
        settlement_tx_id="TX-1",
    )
    results = profile_service_.search_by_interests(["defi", "gaming", "nft"], limit=10)
    assert [p.wallet for p, _m in results] == [_PAYER]


def test_register_populates_the_interest_lookup(store: InMemorySocialStore) -> None:
    """A brand-new registration's interests all land in the interest lookup, findable via search."""
    profile_service_ = ProfileService(store)
    profile_service_.register(
        wallet=_PAYER,
        name="A",
        bio="",
        mission="",
        location="",
        interests=["defi", "liquidity"],
        emoji="",
        settlement_tx_id="TX-1",
    )
    assert [w for w, _at in store.list_agents_by_interest("defi", limit=10)] == [_PAYER]
    assert [w for w, _at in store.list_agents_by_interest("liquidity", limit=10)] == [_PAYER]
    assert store.list_agents_by_interest("nft", limit=10) == []


def test_edit_removes_stale_interest_rows_and_adds_new_ones(store: InMemorySocialStore) -> None:
    """Editing a profile's interests does a full delete-then-reinsert of the wallet's lookup rows: a dropped tag's row disappears, a newly-added tag's row appears, an unchanged tag's row survives."""
    profile_service_ = ProfileService(store)
    profile_service_.register(
        wallet=_PAYER,
        name="A",
        bio="",
        mission="",
        location="",
        interests=["defi", "nft"],
        emoji="",
        settlement_tx_id="TX-1",
    )
    profile_service_.edit(
        wallet=_PAYER,
        name=None,
        bio=None,
        mission=None,
        location=None,
        interests=["defi", "gaming"],
        emoji=None,
    )

    assert [w for w, _at in store.list_agents_by_interest("defi", limit=10)] == [_PAYER]
    assert [w for w, _at in store.list_agents_by_interest("gaming", limit=10)] == [_PAYER]
    assert store.list_agents_by_interest("nft", limit=10) == []  # dropped tag's row is gone


# --------------------------------------------------------------------------- #
# Group Discovery by tag (added 2026-09-06)
# --------------------------------------------------------------------------- #
def test_group_service_list_by_tag_filters_and_ranks_newest_first(
    store: InMemorySocialStore,
) -> None:
    """Only groups carrying the (normalized) tag come back, newest first; an untagged/other-tagged group and an unknown tag are excluded."""
    service = GroupService(store, is_registered=_always_registered)
    older = service.create(
        owner=_PAYER,
        name="defi-signals",
        description="",
        settlement_tx_id="TX-GT1",
        tags=["DeFi", "Liquidity"],
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )
    newer = service.create(
        owner=_OTHER_PAYER,
        name="defi-alerts",
        description="",
        settlement_tx_id="TX-GT2",
        tags=["defi"],
        now=datetime(2026, 1, 2, tzinfo=UTC),
    )
    service.create(
        owner=_PAYER,
        name="nft-corner",
        description="",
        settlement_tx_id="TX-GT3",
        tags=["nft"],
    )

    matches = service.list_by_tag("defi", limit=10)
    assert [g.group_id for g in matches] == [newer.group_id, older.group_id]
    assert [g.group_id for g in service.list_by_tag("liquidity", limit=10)] == [older.group_id]
    assert service.list_by_tag("unknown-tag", limit=10) == []


def test_group_service_create_normalizes_tags_the_same_way_a_posts_tags_are(
    store: InMemorySocialStore,
) -> None:
    """Group tags are trimmed/lowercased/deduped via post_service.normalize_tags -- the same normalization a post's own tags already get, no second scheme."""
    service = GroupService(store, is_registered=_always_registered)
    group = service.create(
        owner=_PAYER,
        name="caps-test",
        description="",
        settlement_tx_id="TX-GT4",
        tags=[" DeFi ", "defi", "NFT"],
    )
    assert group.tags == ["defi", "nft"]
    assert [g.group_id for g in service.list_by_tag("nft", limit=10)] == [group.group_id]


def test_group_service_create_rejects_too_many_tags(store: InMemorySocialStore) -> None:
    """The same settings.x402_social_max_tags bound a post's own tags are held to applies to a group's tags."""
    service = GroupService(store, is_registered=_always_registered)
    too_many = [f"tag{i}" for i in range(settings.x402_social_max_tags + 1)]
    with pytest.raises(SocialError) as exc_info:
        service.create(
            owner=_PAYER,
            name="too-many-tags",
            description="",
            settlement_tx_id="TX-GT5",
            tags=too_many,
        )
    assert exc_info.value.code == "invalid_request"


def test_group_service_list_by_tag_excludes_hidden_platform_groups(
    store: InMemorySocialStore,
) -> None:
    """A platform-hidden group (Phase S2) is filtered out of ?tag= the same way it is filtered out of list_recent -- the tag lookup always point-reads the fresh canonical row."""
    service = GroupService(store, is_registered=_always_registered)
    group = service.create(
        owner=_PAYER, name="to-hide", description="", settlement_tx_id="TX-GT6", tags=["defi"]
    )
    store.mark_group_hidden_platform(group)
    assert service.list_by_tag("defi", limit=10) == []


@pytest.mark.usefixtures("fake_redis")
def test_groups_list_route_tag_query_filters_and_validates(
    store: InMemorySocialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /groups?tag= filters to matching groups (case/whitespace-normalized); an over-long tag is a free 400; a blank ?tag= (query_param already trims) is treated the same as no ?tag= at all -- the plain, unfiltered browse."""
    group_service_ = GroupService(store, is_registered=_always_registered)
    monkeypatch.setattr(social_routes, "group_service", group_service_)
    tagged = group_service_.create(
        owner=_PAYER, name="tagged-group", description="", settlement_tx_id="TX-GT7", tags=["DeFi"]
    )
    group_service_.create(
        owner=_OTHER_PAYER, name="untagged-group", description="", settlement_tx_id="TX-GT8"
    )

    filtered = social_routes.x402_social_groups_list(
        _request(method="GET", query={"tag": " DeFi "}, path="/api/v1/x402/social/groups")
    )
    assert [g["group_id"] for g in filtered["groups"]] == [tagged.group_id]
    assert filtered["groups"][0]["tags"] == ["defi"]

    unfiltered = social_routes.x402_social_groups_list(
        _request(method="GET", path="/api/v1/x402/social/groups")
    )
    assert len(unfiltered["groups"]) == 2

    blank = social_routes.x402_social_groups_list(
        _request(method="GET", query={"tag": "   "}, path="/api/v1/x402/social/groups")
    )
    assert len(blank["groups"]) == 2  # trims to "", same as omitted -- not a 400

    too_long = social_routes.x402_social_groups_list(
        _request(method="GET", query={"tag": "x" * 40}, path="/api/v1/x402/social/groups")
    )
    assert too_long.status_code == 400
    assert json.loads(too_long.description)["error"]["code"] == "invalid_request"


@pytest.mark.usefixtures("fake_redis")
def test_group_create_route_accepts_tags_and_they_become_searchable(
    store: InMemorySocialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /groups' optional `tags` field flows through to GET /groups?tag= end to end."""
    monkeypatch.setattr(
        social_routes, "group_service", GroupService(store, is_registered=_always_registered)
    )
    monkeypatch.setattr(
        social_routes,
        "require_paid_request",
        lambda *_a, **_kw: _settled_result(payer=_PAYER, txid="TX-GCT1"),
    )
    fulfilled: list[tuple[str, str]] = []
    monkeypatch.setattr(
        social_routes,
        "mark_fulfilled",
        lambda txid, *, resource: fulfilled.append((txid, resource)),
    )

    body = json.dumps({"name": "tag-flow", "description": "", "tags": [" DeFi ", "defi"]}).encode()
    response = social_routes.x402_social_group_create(
        _request(body=body, path="/api/v1/x402/social/groups")
    )
    assert response.status_code == 200
    payload = json.loads(response.description)
    assert payload["group"]["tags"] == ["defi"]

    listing = social_routes.x402_social_groups_list(
        _request(method="GET", query={"tag": "defi"}, path="/api/v1/x402/social/groups")
    )
    assert [g["group_id"] for g in listing["groups"]] == [payload["group"]["group_id"]]
    assert fulfilled == [("TX-GCT1", social_routes._GROUP_CREATE_RESOURCE)]


@pytest.mark.usefixtures("fake_redis")
def test_group_create_route_rejects_too_many_tags_as_a_free_400_before_the_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An oversized tags list is caught before require_paid_request is ever called -- a free 400, nothing charged."""

    def _fail_if_called(*_a: object, **_kw: object) -> Never:
        raise AssertionError("require_paid_request must not be called for an invalid tags list")

    monkeypatch.setattr(social_routes, "require_paid_request", _fail_if_called)

    too_many_tags = [f"tag{i}" for i in range(settings.x402_social_max_tags + 1)]
    body = json.dumps({"name": "too-many-tags", "tags": too_many_tags}).encode()
    response = social_routes.x402_social_group_create(
        _request(body=body, path="/api/v1/x402/social/groups")
    )
    assert response.status_code == 400
    assert json.loads(response.description)["error"]["code"] == "invalid_request"


# --------------------------------------------------------------------------- #
# Spend-weighted agent leaderboard (added 2026-09-06)
# --------------------------------------------------------------------------- #
class _AscendingLimitThenReverseSettlementStore:
    """A hypothetical store whose LIMIT keeps the OLDEST rows of a day, then reverses to look newest-first.

    NOT how CassandraSettlementStore actually behaves: it was briefly
    believed to (a same-night review found `list_for_day`'s docstring wrongly
    claimed the table defaulted to ascending clustering order, when
    `x402_settlements` is actually `CLUSTERING ORDER BY (settled_at DESC,
    ...)` -- migration 090), and `list_for_day` was fixed at the source in
    app/modules/x402/settlement.py 2026-09-06 to stop reversing an
    already-correct newest-first page. This fake keeps the ORIGINAL
    (mis-)behavior alive here on purpose, as a defensive regression test:
    leaderboard_service's filter-before-cap logic should not structurally
    depend on `list_for_day` getting its ordering right, since a future
    store implementation could get it wrong again the same way this one
    briefly did. `InMemorySettlementStore` (the fake normally used by these
    tests) sorts descending before slicing and cannot exercise this path.
    """

    def __init__(self, settlements: list[SettlementRecord]) -> None:
        self.settlements = settlements

    def list_for_day(self, day: str, *, limit: int) -> list[SettlementRecord]:
        day_rows = [
            s
            for s in self.settlements
            if datetime.fromtimestamp(s.settled_at_epoch, tz=UTC).strftime("%Y-%m-%d") == day
        ]
        day_rows.sort(key=lambda s: s.settled_at_epoch)  # ascending, like the table's default
        oldest_n = day_rows[:limit]  # the real bug: the raw LIMIT bites the ascending scan
        oldest_n.reverse()  # the real store's own "make it look newest-first" step
        return oldest_n

    def record_settlement(self, item: SettlementRecord) -> None:  # pragma: no cover - unused
        raise NotImplementedError

    def get_settlement(self, tx_id: str) -> SettlementRecord | None:  # pragma: no cover - unused
        raise NotImplementedError

    def mark_fulfilled(self, tx_id: str) -> bool:  # pragma: no cover - unused
        raise NotImplementedError

    def record_refund(  # pragma: no cover - unused
        self, tx_id: str, *, refund_tx_id: str | None, refund_status: str
    ) -> bool:
        raise NotImplementedError


def test_aggregate_real_spend_by_payer_keeps_the_newest_settlements_within_the_per_day_cap() -> (
    None
):
    """Defensive regression (finding #8): even against a store whose LIMIT keeps a day's oldest rows (see `_AscendingLimitThenReverseSettlementStore` -- not how the real store behaves, since its `list_for_day` was fixed at the source 2026-09-06), this module's filter-before-cap logic must still surface the NEWEST settlements of an over-cap day, not the oldest."""
    cap = 200
    total_real = cap + 50
    base_epoch = int(
        datetime.now(tz=UTC).replace(hour=1, minute=0, second=0, microsecond=0).timestamp()
    )
    payers = [encode_address(bytes([i]) + bytes(31)) for i in range(total_real)]
    settlements = [
        SettlementRecord(
            tx_id=f"TX-ORDER-{i}",
            asset_id="10458941",
            amount_atomic="1000000",
            payer=payers[i],
            resource="x402-directory-list",
            network=ALGORAND_TESTNET_CAIP2,
            settled_at_epoch=base_epoch + i,  # strictly increasing: higher i == newer
            eur_value=1.0,
        )
        for i in range(total_real)
    ]
    fake_store = _AscendingLimitThenReverseSettlementStore(settlements)

    totals = leaderboard_service.aggregate_real_spend_by_payer(
        window_days=1, store=fake_store, now=datetime.fromtimestamp(base_epoch, tz=UTC)
    )

    oldest_dropped = payers[: total_real - cap]
    newest_kept = payers[total_real - cap :]
    assert len(totals) == cap
    assert all(p not in totals for p in oldest_dropped)
    assert all(p in totals for p in newest_kept)


def test_aggregate_real_spend_by_payer_does_not_let_probe_rows_crowd_out_real_ones_within_the_cap(
    ledger: InMemorySettlementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for finding #8's second half: probe-payer settlements must be filtered out BEFORE the per-day real cap is applied, not after. Here 5 probe settlements are all NEWER than 3 real ones and a tiny cap of 3 -- under the pre-fix code (which capped the raw store read at the real limit itself), those 3 newest-of-the-day rows would ALL be probes and every real payer would be invisible to the leaderboard."""
    monkeypatch.setattr(settings, "x402_probe_payers", _PROBE_PAYER)
    monkeypatch.setattr(leaderboard_service, "LEADERBOARD_SETTLEMENTS_PER_DAY_CAP", 3)
    base = datetime.now(tz=UTC).replace(hour=2, minute=0, second=0, microsecond=0)
    real_payers = [_PAYER, _OTHER_PAYER, _VOTER_A]
    for i, payer in enumerate(real_payers):
        ledger.record_settlement(
            SettlementRecord(
                tx_id=f"TX-REAL-{i}",
                asset_id="10458941",
                amount_atomic="1000000",
                payer=payer,
                resource="x402-directory-list",
                network=ALGORAND_TESTNET_CAIP2,
                settled_at_epoch=int((base + timedelta(seconds=i)).timestamp()),
                eur_value=1.0,
            )
        )
    for i in range(5):
        ledger.record_settlement(
            SettlementRecord(
                tx_id=f"TX-PROBE-{i}",
                asset_id="10458941",
                amount_atomic="1000000",
                payer=_PROBE_PAYER,
                resource="x402-directory-list",
                network=ALGORAND_TESTNET_CAIP2,
                settled_at_epoch=int((base + timedelta(seconds=100 + i)).timestamp()),
                eur_value=1.0,
            )
        )

    totals = leaderboard_service.aggregate_real_spend_by_payer(window_days=1, now=base)

    assert _PROBE_PAYER not in totals
    assert len(totals) == 3
    for payer in real_payers:
        assert totals[payer].total_eur_spent == pytest.approx(1.0)


def test_aggregate_real_spend_by_payer_sums_real_settlements_excludes_probe_and_other_network(
    ledger: InMemorySettlementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real settlements for the SAME payer sum; an unpriceable one still counts toward settlement_count but not the EUR total; a probe payer and a different-network settlement are excluded entirely."""
    monkeypatch.setattr(settings, "x402_probe_payers", _PROBE_PAYER)
    _settled(ledger, payer=_PAYER, eur_value=1.5, tx_id="TX-S1")
    _settled(ledger, payer=_PAYER, eur_value=2.5, tx_id="TX-S2")
    _settled(ledger, payer=_PAYER, eur_value=EUR_VALUE_UNAVAILABLE, tx_id="TX-S3")
    _settled(ledger, payer=_OTHER_PAYER, eur_value=0.5, tx_id="TX-S4")
    _settled(ledger, payer=_PROBE_PAYER, eur_value=100.0, tx_id="TX-S5")
    _settled(ledger, payer=_VOTER_A, eur_value=9.0, tx_id="TX-S6", network=ALGORAND_MAINNET_CAIP2)

    totals = leaderboard_service.aggregate_real_spend_by_payer(window_days=1)

    assert totals[_PAYER].total_eur_spent == pytest.approx(4.0)
    assert totals[_PAYER].settlement_count == 3
    assert totals[_OTHER_PAYER].total_eur_spent == pytest.approx(0.5)
    assert _PROBE_PAYER not in totals
    assert _VOTER_A not in totals


def test_aggregate_real_spend_by_payer_respects_the_window(
    ledger: InMemorySettlementStore,
) -> None:
    """A settlement older than the window is excluded; widening the window picks it back up."""
    old = datetime.now(tz=UTC) - timedelta(days=40)
    ledger.record_settlement(
        SettlementRecord(
            tx_id="TX-OLD",
            asset_id="10458941",
            amount_atomic="1000000",
            payer=_PAYER,
            resource="x402-directory-list",
            network=ALGORAND_TESTNET_CAIP2,
            settled_at_epoch=int(old.timestamp()),
            eur_value=5.0,
        )
    )

    assert _PAYER not in leaderboard_service.aggregate_real_spend_by_payer(window_days=30)
    within = leaderboard_service.aggregate_real_spend_by_payer(window_days=45)
    assert within[_PAYER].total_eur_spent == pytest.approx(5.0)


def test_aggregate_real_spend_by_payer_respects_the_per_day_settlement_cap(
    ledger: InMemorySettlementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only up to the per-day cap of real settlements is read per UTC day -- monkeypatched to 2 so the test does not need to write hundreds of rows to prove it."""
    monkeypatch.setattr(leaderboard_service, "LEADERBOARD_SETTLEMENTS_PER_DAY_CAP", 2)
    _settled(ledger, payer=_PAYER, eur_value=1.0, tx_id="TX-CAP1")
    _settled(ledger, payer=_PAYER, eur_value=1.0, tx_id="TX-CAP2")
    _settled(ledger, payer=_PAYER, eur_value=1.0, tx_id="TX-CAP3")

    totals = leaderboard_service.aggregate_real_spend_by_payer(window_days=1)
    assert totals[_PAYER].settlement_count == 2


def test_rank_registered_agents_by_spend_orders_excludes_zero_spend_and_unregistered(
    store: InMemorySocialStore, ledger: InMemorySettlementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ranked highest-spend-first; a registered agent with zero real spend and a real spender who never registered on the social network are both excluded."""
    monkeypatch.setattr(settings, "x402_probe_payers", _PROBE_PAYER)
    profile_service_ = ProfileService(store)
    for wallet, name in (
        (_PAYER, "BigSpender"),
        (_OTHER_PAYER, "SmallSpender"),
        (_VOTER_A, "NeverPaid"),
    ):
        profile_service_.register(
            wallet=wallet,
            name=name,
            bio="",
            mission="",
            location="",
            interests=[],
            emoji="",
            settlement_tx_id=f"TX-REG-{name}",
        )

    _settled(ledger, payer=_PAYER, eur_value=10.0, tx_id="TX-R1")
    _settled(ledger, payer=_OTHER_PAYER, eur_value=1.0, tx_id="TX-R2")
    # Real spend, but never registered on the social network -- must not appear.
    _settled(ledger, payer=_VOTER_B, eur_value=50.0, tx_id="TX-R3")
    # Our own probe wallet -- excluded even though it "spent" the most.
    _settled(ledger, payer=_PROBE_PAYER, eur_value=1000.0, tx_id="TX-R4")

    ranked = leaderboard_service.rank_registered_agents_by_spend(
        limit=10, profile_lookup=profile_service_.get
    )

    assert [profile.wallet for profile, _spend in ranked] == [_PAYER, _OTHER_PAYER]
    assert ranked[0][1].total_eur_spent == pytest.approx(10.0)
    assert ranked[1][1].total_eur_spent == pytest.approx(1.0)


def test_rank_registered_agents_by_spend_respects_limit(
    store: InMemorySocialStore, ledger: InMemorySettlementStore
) -> None:
    """The `limit` cap is honored regardless of how many registered agents have real spend."""
    profile_service_ = ProfileService(store)
    for i, wallet in enumerate((_PAYER, _OTHER_PAYER, _VOTER_A)):
        profile_service_.register(
            wallet=wallet,
            name=f"Agent{i}",
            bio="",
            mission="",
            location="",
            interests=[],
            emoji="",
            settlement_tx_id=f"TX-LIM-{i}",
        )
        _settled(ledger, payer=wallet, eur_value=float(i + 1), tx_id=f"TX-LIM-S{i}")

    ranked = leaderboard_service.rank_registered_agents_by_spend(
        limit=2, profile_lookup=profile_service_.get
    )
    assert len(ranked) == 2


@pytest.mark.usefixtures("fake_redis")
def test_agent_leaderboard_route_is_paid_and_ranks_by_real_spend_with_limit_clamped(
    store: InMemorySocialStore, ledger: InMemorySettlementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /agents/leaderboard is wired through require_paid_request/run_with_refund; a requested limit above LEADERBOARD_MAX_LIMIT is clamped, and mark_fulfilled runs exactly once."""
    profile_service_ = ProfileService(store)
    monkeypatch.setattr(social_routes, "profile_service", profile_service_)
    profile_service_.register(
        wallet=_PAYER,
        name="BigSpender",
        bio="",
        mission="",
        location="",
        interests=[],
        emoji="",
        settlement_tx_id="TX-REGL1",
    )
    profile_service_.register(
        wallet=_OTHER_PAYER,
        name="SmallSpender",
        bio="",
        mission="",
        location="",
        interests=[],
        emoji="",
        settlement_tx_id="TX-REGL2",
    )
    _settled(ledger, payer=_PAYER, eur_value=10.0, tx_id="TX-RL1")
    _settled(ledger, payer=_OTHER_PAYER, eur_value=1.0, tx_id="TX-RL2")

    captured: dict = {}

    def _spy_require_paid_request(*_a: object, **kwargs: object) -> x402_guard.PaymentResult:
        captured.update(kwargs)
        return _settled_result(payer=_VOTER_A, txid="TX-LEADERBOARD")

    monkeypatch.setattr(social_routes, "require_paid_request", _spy_require_paid_request)
    fulfilled: list[tuple[str, str]] = []
    monkeypatch.setattr(
        social_routes,
        "mark_fulfilled",
        lambda txid, *, resource: fulfilled.append((txid, resource)),
    )

    response = social_routes.x402_social_agent_leaderboard(
        _request(
            method="GET",
            query={"limit": str(LEADERBOARD_MAX_LIMIT + 10)},
            path="/api/v1/x402/social/agents/leaderboard",
        )
    )

    assert response.status_code == 200
    assert captured["price"] == settings.x402_social_agent_leaderboard_price
    assert captured["resource"] == social_routes._AGENT_LEADERBOARD_RESOURCE
    body = json.loads(response.description)
    assert body["limit"] == LEADERBOARD_MAX_LIMIT
    assert body["window_days"] == leaderboard_service.LEADERBOARD_WINDOW_DAYS
    assert [a["wallet"] for a in body["agents"]] == [_PAYER, _OTHER_PAYER]
    assert body["agents"][0]["total_eur_spent"] == pytest.approx(10.0)
    assert fulfilled == [("TX-LEADERBOARD", social_routes._AGENT_LEADERBOARD_RESOURCE)]


@pytest.mark.usefixtures("fake_redis")
def test_agent_leaderboard_route_rejects_a_non_integer_limit_as_a_free_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed ?limit= never reaches require_paid_request -- a free 400, nothing charged."""

    def _fail_if_called(*_a: object, **_kw: object) -> Never:
        raise AssertionError("require_paid_request must not be called for an invalid limit")

    monkeypatch.setattr(social_routes, "require_paid_request", _fail_if_called)

    response = social_routes.x402_social_agent_leaderboard(
        _request(
            method="GET",
            query={"limit": "not-a-number"},
            path="/api/v1/x402/social/agents/leaderboard",
        )
    )
    assert response.status_code == 400
    assert json.loads(response.description)["error"]["code"] == "invalid_request"


# --------------------------------------------------------------------------- #
# Private messages (DMs, migration 122, operator ask 2026-09-07)
# --------------------------------------------------------------------------- #
def test_dm_conversation_id_is_order_independent() -> None:
    """conversation_id_for(a, b) == conversation_id_for(b, a) -- both participants resolve to the same partition regardless of call order."""
    assert conversation_id_for(_PAYER, _OTHER_PAYER) == conversation_id_for(_OTHER_PAYER, _PAYER)
    assert conversation_id_for(_PAYER, _OTHER_PAYER) != conversation_id_for(_PAYER, _REPORTER)


def test_dm_send_and_list_conversation_round_trip(store: InMemorySocialStore) -> None:
    """A -> send -> B is readable by BOTH participants via list_conversation, newest-first, and shows up in both wallets' list_conversations."""
    _register(store, _PAYER)
    _register(store, _OTHER_PAYER)
    service = DmService(store, is_registered=lambda w: store.get_agent(w) is not None)

    # Explicit, distinct timestamps -- ties on created_at_epoch break by
    # message_id ascending (same tie-break shape LIST_POSTS_BY_AUTHOR's own
    # in-memory mirror uses), which is not the same thing as send order, so
    # a same-second pair would make this assertion flaky.
    first = service.send(
        sender=_PAYER,
        recipient=_OTHER_PAYER,
        body="hello",
        now=datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC),
    )
    second = service.send(
        sender=_OTHER_PAYER,
        recipient=_PAYER,
        body="hi back",
        now=datetime(2026, 9, 7, 12, 0, 1, tzinfo=UTC),
    )

    # Both participants see the SAME two messages in the SAME conversation,
    # newest-first, regardless of which side calls list_conversation.
    from_payer_side = service.list_conversation(_PAYER, _OTHER_PAYER, limit=10)
    from_other_side = service.list_conversation(_OTHER_PAYER, _PAYER, limit=10)
    assert [m.message_id for m in from_payer_side] == [second.message_id, first.message_id]
    assert [m.message_id for m in from_other_side] == [second.message_id, first.message_id]
    assert from_payer_side[0].body == "hi back"
    assert from_payer_side[0].sender == _OTHER_PAYER

    payer_conversations = service.list_conversations(_PAYER, limit=10)
    assert len(payer_conversations) == 1
    assert payer_conversations[0].peer_wallet == _OTHER_PAYER
    assert payer_conversations[0].last_sender == _OTHER_PAYER
    assert payer_conversations[0].last_message_preview == "hi back"

    other_conversations = service.list_conversations(_OTHER_PAYER, limit=10)
    assert len(other_conversations) == 1
    assert other_conversations[0].peer_wallet == _PAYER


def test_dm_send_rejects_self_message(store: InMemorySocialStore) -> None:
    """A wallet cannot DM itself -- cannot_message_self, 400, mirroring GraphService.follow's self-follow refusal."""
    _register(store, _PAYER)
    service = DmService(store, is_registered=lambda w: store.get_agent(w) is not None)
    with pytest.raises(SocialError) as exc_info:
        service.send(sender=_PAYER, recipient=_PAYER, body="talking to myself")
    assert exc_info.value.code == "cannot_message_self"
    assert exc_info.value.http_status == 400


def test_dm_send_requires_both_sender_and_recipient_registered(store: InMemorySocialStore) -> None:
    """An unregistered sender OR an unregistered recipient is refused as not_registered (finding-4-style fail closed), unlike GraphService.follow which only checks the follower."""
    service = DmService(store, is_registered=lambda w: store.get_agent(w) is not None)

    # Neither registered.
    with pytest.raises(SocialError) as exc_info:
        service.send(sender=_PAYER, recipient=_OTHER_PAYER, body="hi")
    assert exc_info.value.code == "not_registered"

    # Sender registered, recipient not.
    _register(store, _PAYER)
    with pytest.raises(SocialError) as exc_info:
        service.send(sender=_PAYER, recipient=_OTHER_PAYER, body="hi")
    assert exc_info.value.code == "not_registered"

    # Recipient registered, sender not.
    _register(store, _OTHER_PAYER)
    store2 = InMemorySocialStore()
    service2 = DmService(store2, is_registered=lambda w: store2.get_agent(w) is not None)
    _register(store2, _OTHER_PAYER)
    with pytest.raises(SocialError) as exc_info:
        service2.send(sender=_PAYER, recipient=_OTHER_PAYER, body="hi")
    assert exc_info.value.code == "not_registered"


def test_dm_send_rejects_oversized_and_html_bodies(store: InMemorySocialStore) -> None:
    """DM bodies go through the same markdown_guard.validate_markdown_body every comment already does -- reused, not a new validator."""
    _register(store, _PAYER)
    _register(store, _OTHER_PAYER)
    service = DmService(store, is_registered=lambda w: store.get_agent(w) is not None)

    with pytest.raises(SocialError) as exc_info:
        service.send(sender=_PAYER, recipient=_OTHER_PAYER, body="x" * 5000)
    assert exc_info.value.code == "invalid_request"

    with pytest.raises(SocialError) as exc_info:
        service.send(sender=_PAYER, recipient=_OTHER_PAYER, body="hi <script>evil()</script>")
    assert exc_info.value.code == "embedded_html_rejected"


def test_dm_conversation_list_preview_is_truncated(store: InMemorySocialStore) -> None:
    """The conversation-list row's preview is capped, never the full body -- GET /dm/{wallet} is the only surface serving a full body."""
    _register(store, _PAYER)
    _register(store, _OTHER_PAYER)
    service = DmService(store, is_registered=lambda w: store.get_agent(w) is not None)
    long_body = "a" * 500
    service.send(sender=_PAYER, recipient=_OTHER_PAYER, body=long_body)

    conversations = service.list_conversations(_PAYER, limit=10)
    assert len(conversations[0].last_message_preview) < len(long_body)

    messages = service.list_conversation(_PAYER, _OTHER_PAYER, limit=10)
    assert messages[0].body == long_body


@pytest.mark.usefixtures("fake_redis")
def test_dm_send_route_end_to_end(
    store: InMemorySocialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /dm, session-authenticated, stores the message under the SESSION wallet as sender (never a body field); GET /dm/{peer} and GET /dm read it back."""
    monkeypatch.setattr(social_routes, "profile_service", ProfileService(store))
    monkeypatch.setattr(
        social_routes, "dm_service", DmService(store, is_registered=_always_registered)
    )
    _register(store, _PAYER)
    _register(store, _OTHER_PAYER)

    token, _expires = social_routes.issue_session_token(_PAYER)
    send_response = social_routes.x402_social_dm_send(
        _request(
            method="POST",
            path="/api/v1/x402/social/dm",
            headers={"authorization": f"Bearer {token}"},
            body=json.dumps({"recipient": _OTHER_PAYER, "body": "hello agent"}).encode(),
        )
    )
    assert isinstance(send_response, dict)
    assert send_response["message"]["sender"] == _PAYER
    assert send_response["message"]["recipient"] == _OTHER_PAYER
    assert send_response["message"]["body"] == "hello agent"

    conversation = social_routes.x402_social_dm_conversation(
        _request(
            method="GET",
            path=f"/api/v1/x402/social/dm/{_OTHER_PAYER}",
            path_params={"wallet": _OTHER_PAYER},
            headers={"authorization": f"Bearer {token}"},
        )
    )
    assert isinstance(conversation, dict)
    assert conversation["peer_wallet"] == _OTHER_PAYER
    assert len(conversation["messages"]) == 1
    assert conversation["messages"][0]["body"] == "hello agent"

    conversations = social_routes.x402_social_dm_conversations(
        _request(
            method="GET",
            path="/api/v1/x402/social/dm",
            headers={"authorization": f"Bearer {token}"},
        )
    )
    assert isinstance(conversations, dict)
    assert [c["peer_wallet"] for c in conversations["conversations"]] == [_OTHER_PAYER]

    # The recipient's own session can read the SAME conversation too.
    other_token, _ = social_routes.issue_session_token(_OTHER_PAYER)
    other_view = social_routes.x402_social_dm_conversation(
        _request(
            method="GET",
            path=f"/api/v1/x402/social/dm/{_PAYER}",
            path_params={"wallet": _PAYER},
            headers={"authorization": f"Bearer {other_token}"},
        )
    )
    assert isinstance(other_view, dict)
    assert len(other_view["messages"]) == 1


@pytest.mark.usefixtures("fake_redis")
def test_dm_send_requires_a_valid_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """No/invalid bearer token is a 401, and dm_service.send is never reached."""
    called = []
    monkeypatch.setattr(
        social_routes, "dm_service", SimpleNamespace(send=lambda **_kw: called.append(1))
    )

    no_token = social_routes.x402_social_dm_send(
        _request(
            method="POST",
            path="/api/v1/x402/social/dm",
            body=json.dumps({"recipient": _OTHER_PAYER, "body": "hi"}).encode(),
        )
    )
    assert no_token.status_code == 401

    bad_token = social_routes.x402_social_dm_send(
        _request(
            method="POST",
            path="/api/v1/x402/social/dm",
            headers={"authorization": "Bearer not-a-real-token"},
            body=json.dumps({"recipient": _OTHER_PAYER, "body": "hi"}).encode(),
        )
    )
    assert bad_token.status_code == 401
    assert called == []


@pytest.mark.usefixtures("fake_redis")
def test_dm_conversation_and_conversations_routes_require_a_valid_session() -> None:
    """GET /dm/{wallet} and GET /dm both 401 without a valid bearer session -- DMs are never readable by an unauthenticated caller."""
    conversation = social_routes.x402_social_dm_conversation(
        _request(
            method="GET",
            path=f"/api/v1/x402/social/dm/{_OTHER_PAYER}",
            path_params={"wallet": _OTHER_PAYER},
        )
    )
    assert conversation.status_code == 401

    conversations = social_routes.x402_social_dm_conversations(
        _request(method="GET", path="/api/v1/x402/social/dm")
    )
    assert conversations.status_code == 401


@pytest.mark.usefixtures("fake_redis")
def test_dm_send_route_rejects_self_message_as_a_400(
    store: InMemorySocialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route surfaces DmService's cannot_message_self as a clean 400, not an unhandled exception."""
    monkeypatch.setattr(
        social_routes, "dm_service", DmService(store, is_registered=_always_registered)
    )
    token, _expires = social_routes.issue_session_token(_PAYER)
    response = social_routes.x402_social_dm_send(
        _request(
            method="POST",
            path="/api/v1/x402/social/dm",
            headers={"authorization": f"Bearer {token}"},
            body=json.dumps({"recipient": _PAYER, "body": "hi"}).encode(),
        )
    )
    assert response.status_code == 400
    assert json.loads(response.description)["error"]["code"] == "cannot_message_self"


@pytest.mark.usefixtures("fake_redis")
def test_dm_send_route_rejects_a_malformed_recipient_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A syntactically invalid recipient wallet is a free 400 before dm_service.send is ever called."""
    called = []
    monkeypatch.setattr(
        social_routes, "dm_service", SimpleNamespace(send=lambda **_kw: called.append(1))
    )
    token, _expires = social_routes.issue_session_token(_PAYER)
    response = social_routes.x402_social_dm_send(
        _request(
            method="POST",
            path="/api/v1/x402/social/dm",
            headers={"authorization": f"Bearer {token}"},
            body=json.dumps({"recipient": "not-a-wallet", "body": "hi"}).encode(),
        )
    )
    assert response.status_code == 400
    assert called == []


@pytest.mark.usefixtures("fake_redis")
def test_dm_send_route_is_rate_limited_per_wallet(monkeypatch: pytest.MonkeyPatch) -> None:
    """A sender over its hourly DM-send budget gets a free 429 -- dm_service.send is never reached."""
    called = []
    monkeypatch.setattr(
        social_routes, "dm_service", SimpleNamespace(send=lambda **_kw: called.append(1))
    )
    monkeypatch.setattr(social_routes, "dm_send_wallet_rate_limited", lambda **_kw: True)
    monkeypatch.setattr(social_routes, "dm_send_ip_rate_limited", lambda *_a, **_kw: False)
    token, _expires = social_routes.issue_session_token(_PAYER)

    response = social_routes.x402_social_dm_send(
        _request(
            method="POST",
            path="/api/v1/x402/social/dm",
            headers={"authorization": f"Bearer {token}"},
            body=json.dumps({"recipient": _OTHER_PAYER, "body": "hi"}).encode(),
        )
    )
    assert response.status_code == 429
    assert json.loads(response.description)["error"]["code"] == "rate_limited"
    assert called == []


@pytest.mark.usefixtures("fake_redis")
def test_dm_send_route_is_rate_limited_per_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    """A sender behind an IP over its hourly DM-send budget gets a free 429 even with wallet budget untouched."""
    called = []
    monkeypatch.setattr(
        social_routes, "dm_service", SimpleNamespace(send=lambda **_kw: called.append(1))
    )
    monkeypatch.setattr(social_routes, "dm_send_wallet_rate_limited", lambda **_kw: False)
    monkeypatch.setattr(social_routes, "dm_send_ip_rate_limited", lambda *_a, **_kw: True)
    token, _expires = social_routes.issue_session_token(_PAYER)

    response = social_routes.x402_social_dm_send(
        _request(
            method="POST",
            path="/api/v1/x402/social/dm",
            headers={"authorization": f"Bearer {token}"},
            body=json.dumps({"recipient": _OTHER_PAYER, "body": "hi"}).encode(),
        )
    )
    assert response.status_code == 429
    assert called == []


def test_dm_send_wallet_rate_limit_fails_open_on_a_redis_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same fail-open contract as every other Redis-backed gate in this module (CLAUDE.md section 2 invariant 9) -- a Redis blip must not block DM sending."""
    monkeypatch.setattr(rate_limit_core, "get_redis", lambda: _BrokenRedis())
    assert social_rate_limit.dm_send_wallet_rate_limited(wallet=_PAYER) is False


def test_dm_send_ip_rate_limit_fails_open_on_a_redis_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same fail-open contract, per-IP axis."""
    monkeypatch.setattr(rate_limit_core, "get_redis", lambda: _BrokenRedis())
    request = _request(
        method="POST",
        path="/api/v1/x402/social/dm",
        headers={"x-real-ip": "203.0.113.7"},
    )
    assert social_rate_limit.dm_send_ip_rate_limited(request) is False


@pytest.mark.usefixtures("fake_redis")
def test_dm_send_wallet_rate_limit_actually_trips_after_the_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dm_send_wallet_rate_limited flips True once the hourly per-wallet budget is exceeded, using the real shared incr_with_expiry primitive."""
    monkeypatch.setattr(settings, "x402_social_dm_send_rate_limit_per_hour", 2)
    assert social_rate_limit.dm_send_wallet_rate_limited(wallet=_PAYER) is False
    assert social_rate_limit.dm_send_wallet_rate_limited(wallet=_PAYER) is False
    assert social_rate_limit.dm_send_wallet_rate_limited(wallet=_PAYER) is True
    # A different wallet has its OWN budget, untouched by _PAYER's.
    assert social_rate_limit.dm_send_wallet_rate_limited(wallet=_OTHER_PAYER) is False


@pytest.mark.usefixtures("fake_redis")
def test_dm_conversations_route_orders_most_recently_active_first(
    store: InMemorySocialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /dm returns the caller's conversations most-recently-active first, not insertion order."""
    monkeypatch.setattr(
        social_routes, "dm_service", DmService(store, is_registered=_always_registered)
    )
    service = social_routes.dm_service
    peer_a = _OTHER_PAYER
    peer_b = _REPORTER
    service.send(
        sender=_PAYER, recipient=peer_a, body="first", now=datetime(2026, 9, 1, tzinfo=UTC)
    )
    service.send(
        sender=_PAYER, recipient=peer_b, body="second", now=datetime(2026, 9, 2, tzinfo=UTC)
    )

    token, _expires = social_routes.issue_session_token(_PAYER)
    response = social_routes.x402_social_dm_conversations(
        _request(
            method="GET",
            path="/api/v1/x402/social/dm",
            headers={"authorization": f"Bearer {token}"},
        )
    )
    assert isinstance(response, dict)
    assert [c["peer_wallet"] for c in response["conversations"]] == [peer_b, peer_a]


def test_cassandra_dm_store_round_trip_binds_the_expected_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """insert_dm_message/upsert_dm_conversation/list_dm_messages/list_dm_conversations bind params in the column order the migration 122 tables declare, and map rows back to the right dataclass fields."""
    session = patch_cassandra(monkeypatch)
    # patch_cassandra alone patches app.core.cassandra.get_cassandra_session,
    # but stores/cassandra.py imported that name directly at module load
    # time -- same "also patch the module's own bound reference" precedent
    # test_cassandra_home_feed_fanout_is_one_batched_call_per_half_not_a_loop
    # already uses.
    monkeypatch.setattr(social_cassandra_store, "get_cassandra_session", lambda: session)
    cassandra_store = social_cassandra_store.CassandraSocialStore()
    now = datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC)
    conversation_id = conversation_id_for(_PAYER, _OTHER_PAYER)
    message_id = str(uuid_module.uuid1())

    cassandra_store.insert_dm_message(
        StoredDmMessage(
            conversation_id=conversation_id,
            message_id=message_id,
            sender=_PAYER,
            recipient=_OTHER_PAYER,
            body="hello",
            created_at_epoch=int(now.timestamp()),
        )
    )
    insert_call = session.execute.call_args_list[-1]
    assert insert_call.args[0] == X402SocialStmts.INSERT_DM_MESSAGE
    assert insert_call.args[1][0] == conversation_id
    assert insert_call.args[1][3] == _PAYER
    assert insert_call.args[1][4] == _OTHER_PAYER
    assert insert_call.args[1][5] == "hello"

    cassandra_store.upsert_dm_conversation(
        StoredDmConversation(
            wallet=_PAYER,
            peer_wallet=_OTHER_PAYER,
            conversation_id=conversation_id,
            last_message_at_epoch=int(now.timestamp()),
            last_sender=_PAYER,
            last_message_preview="hello",
        )
    )
    upsert_call = session.execute.call_args_list[-1]
    assert upsert_call.args[0] == X402SocialStmts.UPSERT_DM_CONVERSATION
    assert upsert_call.args[1][0] == _PAYER
    assert upsert_call.args[1][1] == _OTHER_PAYER

    class _Row:
        def __init__(self, **kw: object) -> None:
            self.__dict__.update(kw)

    session.execute.return_value = [
        _Row(
            conversation_id=conversation_id,
            created_at=now.replace(tzinfo=None),
            message_id=uuid_module.UUID(message_id),
            sender=_PAYER,
            recipient=_OTHER_PAYER,
            body="hello",
        )
    ]
    messages = cassandra_store.list_dm_messages(conversation_id, limit=10)
    assert len(messages) == 1
    assert messages[0].message_id == message_id
    assert messages[0].body == "hello"
    assert messages[0].created_at_epoch == int(now.timestamp())

    session.execute.return_value = [
        _Row(
            wallet=_PAYER,
            peer_wallet=_OTHER_PAYER,
            conversation_id=conversation_id,
            last_message_at=now.replace(tzinfo=None),
            last_sender=_PAYER,
            last_message_preview="hello",
        )
    ]
    conversations = cassandra_store.list_dm_conversations(_PAYER, limit=10)
    assert len(conversations) == 1
    assert conversations[0].peer_wallet == _OTHER_PAYER
    assert conversations[0].last_message_preview == "hello"
