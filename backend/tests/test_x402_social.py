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
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, Never

import pytest

pytest.importorskip("x402")

from algosdk import account, util
from algosdk.encoding import encode_address
from x402.mechanisms.avm.constants import ALGORAND_TESTNET_CAIP2

from app.core import rate_limit as rate_limit_core
from app.core.config import settings
from app.core.http import QueryParams, Request
from app.modules.x402 import circuit_breaker as circuit_breaker_module
from app.modules.x402 import guard as x402_guard
from app.modules.x402 import paid_request as payment_service
from app.modules.x402.refund import RefundResult
from app.modules.x402_social.api import routes as social_routes
from app.modules.x402_social.models.domain import (
    GROUP_ROLE_MEMBER,
    MAX_BIO_LEN,
    MAX_INTERESTS,
    REACTION_UP,
    ReactionTotals,
    SocialError,
    StoredGroup,
)
from app.modules.x402_social.services import prose, session_service, trending_service
from app.modules.x402_social.services import rate_limit as social_rate_limit
from app.modules.x402_social.services.graph_service import GraphService
from app.modules.x402_social.services.group_service import GroupService
from app.modules.x402_social.services.markdown_guard import validate_markdown_body
from app.modules.x402_social.services.post_service import PostService, _new_post_or_comment_id
from app.modules.x402_social.services.profile_service import ProfileService, validate_profile_fields
from app.modules.x402_social.stores import cassandra as social_cassandra_store
from app.modules.x402_social.stores.cassandra import CassandraSocialStore
from app.modules.x402_social.stores.memory import InMemorySocialStore

_PAYER = encode_address(bytes([2]) + bytes(31))
_OTHER_PAYER = encode_address(bytes([3]) + bytes(31))


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

    def execute(self) -> list[object]:
        results: list[object] = []
        for op in self._ops:
            if op[0] == "zincrby":
                results.append(self._client.zincrby(op[1], op[2], op[3]))
            elif op[0] == "expire":
                results.append(self._client.expire(op[1], op[2]))
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
