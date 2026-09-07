"""x402 agent backup storage tests: connector, metadata stores, auth, pricing/capacity rules, and the HTTP routes.

Fully offline. Redis is a fake at every get_redis seam (auth_service,
app.core.rate_limit, circuit_breaker), the metadata store is the module's
own in-memory backend unless a test specifically exercises the Cassandra row
mapper against a fake session (mirrors test_x402_directory.py's own
`_row_to_listing` pattern), and the storage connector is either a real
LocalDiskStorageBackend rooted at a pytest tmp_path or a small in-memory fake
for the service/route-level tests that don't care about real disk I/O.
Wallet-signature proofs are REAL ed25519 signatures from algosdk test
accounts, not stubs -- same convention test_x402_social.py uses for its own
challenge/session tests.
"""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Never
from unittest.mock import patch

import pytest

pytest.importorskip("x402")

from algosdk import account, util

from app.core import rate_limit as rate_limit_core
from app.core.config import settings
from app.core.http import QueryParams, Request, Response
from app.modules.admin.api import routes as admin_routes
from app.modules.x402 import circuit_breaker
from app.modules.x402 import guard as x402_guard
from app.modules.x402 import paid_request as paid_request_module
from app.modules.x402_storage.api import routes as storage_routes
from app.modules.x402_storage.backends.local import LocalDiskPathError, LocalDiskStorageBackend
from app.modules.x402_storage.models.domain import (
    STATUS_ACTIVE,
    STATUS_DELETED,
    StorageCapacityUnavailable,
    StorageError,
    StoredBackup,
    StoredBackupVersion,
    expiry_day_utc,
)
from app.modules.x402_storage.services import auth_service
from app.modules.x402_storage.services import reaper as reaper_module
from app.modules.x402_storage.services.backup_service import (
    BackupService,
    at_remaining_cap,
    compute_expiry_epoch,
    compute_price,
    kb_units,
    validate_declared_size,
    validate_retention_days,
)
from app.modules.x402_storage.services.reaper import reap_expired
from app.modules.x402_storage.stores.memory import InMemoryBackupStore

_PAY_TO = "A" * 58


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class _FakeRedis:
    """Enough of the Redis API for challenges (setex/getdel), rate-limit counters (incr/expire), and the refund circuit breaker (get/set/delete)."""

    def __init__(self) -> None:
        """Start with an empty key/value store."""
        self.store: dict[str, str] = {}

    def setex(self, key: str, _time: int, value: str) -> bool:
        """Setex."""
        self.store[key] = value
        return True

    def getdel(self, key: str) -> str | None:
        """Getdel."""
        return self.store.pop(key, None)

    def get(self, key: str) -> str | None:
        """Get."""
        return self.store.get(key)

    def set(self, key: str, value: str, *_a: object, **kw: object) -> bool:
        """Set, honoring nx=True so the reaper lock can contend."""
        if kw.get("nx") and key in self.store:
            return False
        self.store[key] = value
        return True

    def delete(self, key: str) -> int:
        """Delete."""
        return 1 if self.store.pop(key, None) is not None else 0

    def incr(self, key: str) -> int:
        """Incr."""
        value = int(self.store.get(key, "0")) + 1
        self.store[key] = str(value)
        return value

    def expire(self, _key: str, _seconds: int) -> bool:
        """Expire."""
        return True


class _BrokenRedis:
    """Every operation fails, to exercise the fail-open (rate limit) and fail-safe (auth store) paths."""

    def setex(self, *_a: object, **_kw: object) -> Never:
        """Setex."""
        raise ConnectionError("redis down")

    def getdel(self, *_a: object, **_kw: object) -> Never:
        """Getdel."""
        raise ConnectionError("redis down")

    def incr(self, *_a: object, **_kw: object) -> Never:
        """Incr."""
        raise ConnectionError("redis down")

    def expire(self, *_a: object, **_kw: object) -> Never:
        """Expire."""
        raise ConnectionError("redis down")


class _FakeBackend:
    """An in-memory StorageBackend double for backup_service tests that don't need real disk I/O."""

    def __init__(self, *, usage: int = 0, fail_usage: bool = False, fail_get: bool = False) -> None:
        """Start empty, with the given canned usage_bytes()/failure behavior."""
        self._blobs: dict[str, bytes] = {}
        self._next = 0
        self._usage = usage
        self._fail_usage = fail_usage
        self._fail_get = fail_get

    def put(self, data: bytes) -> dict[str, str]:
        """Put."""
        key = str(self._next)
        self._next += 1
        self._blobs[key] = data
        return {"key": key}

    def get(self, connector_params: dict[str, str]) -> bytes | None:
        """Get."""
        if self._fail_get:
            return None
        return self._blobs.get(connector_params.get("key", ""))

    def delete(self, connector_params: dict[str, str]) -> None:
        """Delete."""
        self._blobs.pop(connector_params.get("key", ""), None)

    def usage_bytes(self) -> int:
        """Usage bytes."""
        if self._fail_usage:
            raise RuntimeError("simulated usage_bytes failure")
        return self._usage


def _request(
    *,
    method: str = "GET",
    body: bytes = b"",
    headers: dict[str, str] | None = None,
    query: dict[str, Any] | None = None,
    path_params: dict[str, str] | None = None,
    path: str = "/api/v1/x402/storage/backups",
) -> Request:
    """Build a framework-neutral Request for a route call."""
    return Request(
        method=method,
        headers=headers or {},
        query_params=QueryParams(query or {}),
        path_params=path_params or {},
        body=body,
        url=SimpleNamespace(scheme="http", host="localhost", path=path),
    )


def _settled_result(payer: str, *, txid: str = "TX1") -> x402_guard.PaymentResult:
    """A canned successful (non-preview, non-promo) PaymentResult, as if require_paid_request had just settled a real payment."""
    return x402_guard.PaymentResult(
        error=None,
        payer=payer,
        settlement_headers={"PAYMENT-RESPONSE": "ok"},
        amount_atomic="20000",
        payment_txid=txid,
        asset_id="10458941",
        network="algorand:testnet",
    )


def _must_not_charge(*_a: object, **_kw: object) -> Never:
    """A require_paid_request stand-in that fails the test if the payment gate is ever reached."""
    raise AssertionError("must not reach the payment gate")


@pytest.fixture(autouse=True)
def _already_paid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the pre-parse 402 for header-less requests: every route test here models a request that already carries a payment (the gate is stubbed), so the unpaid challenge is out of scope. Its ordering has its own tests in tests/test_x402_unpaid_challenge.py."""
    monkeypatch.setattr(storage_routes, "challenge_if_unpaid", lambda *_a, **_kw: None)


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    """Fake redis."""
    client = _FakeRedis()
    monkeypatch.setattr(auth_service, "get_redis", lambda: client)
    monkeypatch.setattr(reaper_module, "get_redis", lambda: client)
    monkeypatch.setattr(rate_limit_core, "get_redis", lambda **_kw: client)
    monkeypatch.setattr(circuit_breaker, "get_redis", lambda **_kw: client)
    return client


@pytest.fixture
def store() -> InMemoryBackupStore:
    """Store."""
    return InMemoryBackupStore()


def _stored(
    store: InMemoryBackupStore,
    *,
    wallet: str,
    backup_id: str = "11111111-1111-1111-1111-111111111111",
    size_bytes: int = 10,
    content_hash: str = "h" * 64,
    status: str = STATUS_ACTIVE,
    expires_in_days: int = 90,
    now: datetime | None = None,
) -> StoredBackup:
    """Write and return one backup row directly into `store`, bypassing BackupService.create()."""
    moment = now or datetime.now(tz=UTC)
    row = StoredBackup(
        wallet=wallet,
        backup_id=backup_id,
        connector="local",
        connector_params={"path": "ab/x.bin"},
        size_bytes=size_bytes,
        content_hash=content_hash,
        label="",
        created_at_epoch=int(moment.timestamp()),
        expires_at_epoch=int((moment + timedelta(days=expires_in_days)).timestamp()),
        status=status,
        settlement_tx_id="TX0",
    )
    store.upsert(row)
    return row


# --------------------------------------------------------------------------- #
# backends/local.py -- the connector
# --------------------------------------------------------------------------- #
def test_local_backend_round_trips_put_get_delete(tmp_path: Path) -> None:
    """Local backend round trips put get delete."""
    backend = LocalDiskStorageBackend(str(tmp_path))
    params = backend.put(b"hello world")
    assert backend.get(params) == b"hello world"
    backend.delete(params)
    assert backend.get(params) is None


def test_local_backend_shards_by_first_two_uuid_hex_chars(tmp_path: Path) -> None:
    """Local backend shards by first two uuid hex chars."""
    backend = LocalDiskStorageBackend(str(tmp_path))
    params = backend.put(b"data")
    rel_path = params["path"]
    shard, filename = rel_path.split("/")
    assert len(shard) == 2
    assert filename.endswith(".bin")
    assert (tmp_path / shard / filename).is_file()


def test_local_backend_usage_bytes_reflects_writes_and_deletes(tmp_path: Path) -> None:
    """Local backend usage bytes reflects writes and deletes."""
    backend = LocalDiskStorageBackend(str(tmp_path))
    assert backend.usage_bytes() == 0
    p1 = backend.put(b"1234567890")  # 10 bytes
    assert backend.usage_bytes() == 10
    p2 = backend.put(b"abc")  # 3 bytes
    assert backend.usage_bytes() == 13
    backend.delete(p1)
    assert backend.usage_bytes() == 3
    backend.delete(p2)
    assert backend.usage_bytes() == 0


def test_local_backend_get_refuses_a_path_that_escapes_the_root(tmp_path: Path) -> None:
    """Local backend get refuses a path that escapes the root."""
    backend = LocalDiskStorageBackend(str(tmp_path))
    # A REAL file outside the root, with content the escape must never
    # surface -- a nonexistent target (e.g. "../../etc/passwd", which may
    # not even resolve to a real file under a deeply-nested tmp_path) would
    # let this test pass for the wrong reason: is_file() already being False
    # regardless of whether the escape guard ran at all.
    outside = tmp_path.parent / "escaped-x402-storage-get-secret.bin"
    outside.write_bytes(b"secret bytes outside the root")
    try:
        # A well-formed-looking but malicious connector_params dict --
        # server-generated in practice, but this is exactly the
        # belt-and-suspenders case _resolve() exists for.
        assert backend.get({"path": "../escaped-x402-storage-get-secret.bin"}) is None
    finally:
        outside.unlink(missing_ok=True)


def test_local_backend_delete_refuses_a_path_that_escapes_the_root(tmp_path: Path) -> None:
    """Local backend delete refuses a path that escapes the root."""
    backend = LocalDiskStorageBackend(str(tmp_path))
    outside = tmp_path.parent / "escaped-x402-storage-test-file.bin"
    outside.write_bytes(b"do not touch me")
    try:
        backend.delete({"path": "../escaped-x402-storage-test-file.bin"})
        assert outside.is_file(), "delete() must never remove a file outside its configured root"
    finally:
        outside.unlink(missing_ok=True)


def test_local_backend_resolve_raises_for_direct_callers_on_an_escaping_path(
    tmp_path: Path,
) -> None:
    """The lower-level _resolve() itself raises -- get()/delete() just catch it and degrade gracefully; this proves the actual guard, not just its callers' handling of it."""
    backend = LocalDiskStorageBackend(str(tmp_path))
    with pytest.raises(LocalDiskPathError):
        backend._resolve({"path": "../outside.bin"})


def test_local_backend_get_returns_none_for_missing_or_empty_params(tmp_path: Path) -> None:
    """Local backend get returns none for missing or empty params."""
    backend = LocalDiskStorageBackend(str(tmp_path))
    assert backend.get({}) is None
    assert backend.get({"path": "zz/does-not-exist.bin"}) is None


def test_local_backend_delete_is_a_no_op_when_already_gone(tmp_path: Path) -> None:
    """Local backend delete is a no op when already gone."""
    backend = LocalDiskStorageBackend(str(tmp_path))
    backend.delete({"path": "zz/never-existed.bin"})  # must not raise


# --------------------------------------------------------------------------- #
# backends/factory.py
# --------------------------------------------------------------------------- #
def test_factory_resolves_local_only_when_root_is_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Factory resolves local only when root is configured."""
    from app.modules.x402_storage.backends import factory

    monkeypatch.setattr(settings, "x402_storage_local_root", "")
    factory.reset_local_backend_cache()
    assert factory.get_storage_backend("local") is None

    monkeypatch.setattr(settings, "x402_storage_local_root", str(tmp_path))
    factory.reset_local_backend_cache()
    backend = factory.get_storage_backend("local")
    assert isinstance(backend, LocalDiskStorageBackend)


def test_factory_returns_none_for_an_unimplemented_connector(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Factory returns none for an unimplemented connector."""
    from app.modules.x402_storage.backends import factory

    monkeypatch.setattr(settings, "x402_storage_local_root", str(tmp_path))
    factory.reset_local_backend_cache()
    assert factory.get_storage_backend("wasabi") is None


# --------------------------------------------------------------------------- #
# stores/memory.py
# --------------------------------------------------------------------------- #
def test_memory_store_upsert_get_roundtrip(store: InMemoryBackupStore) -> None:
    """Memory store upsert get roundtrip."""
    row = _stored(store, wallet="W1")
    assert store.get("W1", row.backup_id) == row
    assert store.get("W1", "unknown-id") is None
    assert store.get("W2", row.backup_id) is None  # different partition


def test_memory_store_list_recent_orders_newest_first(store: InMemoryBackupStore) -> None:
    """Memory store list recent orders newest first."""
    now = datetime.now(tz=UTC)
    older = _stored(
        store,
        wallet="W1",
        backup_id="a" * 8 + "-1111-1111-1111-111111111111",
        now=now - timedelta(days=1),
    )
    newer = _stored(store, wallet="W1", backup_id="b" * 8 + "-1111-1111-1111-111111111111", now=now)
    rows = store.list_recent("W1", limit=10)
    assert [r.backup_id for r in rows] == [newer.backup_id, older.backup_id]


def test_memory_store_list_recent_respects_limit(store: InMemoryBackupStore) -> None:
    """Memory store list recent respects limit."""
    for i in range(5):
        _stored(store, wallet="W1", backup_id=f"{i:08d}-1111-1111-1111-111111111111")
    assert len(store.list_recent("W1", limit=2)) == 2


# --------------------------------------------------------------------------- #
# stores/cassandra.py -- row mapping against a fake session (no real Cassandra)
# --------------------------------------------------------------------------- #
class _CassandraResult:
    """A real __iter__ (looked up via the type, not the instance dict -- a plain SimpleNamespace attribute would never be picked up by `for row in result`) plus a .one() accessor, covering both execute() call shapes CassandraBackupStore uses."""

    def __init__(self, rows: list[SimpleNamespace]) -> None:
        """Hold the canned rows this fake execute() call should yield."""
        self._rows = rows

    def one(self) -> SimpleNamespace | None:
        """The first row, or None -- mirrors the real driver's ResultSet.one()."""
        return self._rows[0] if self._rows else None

    def __iter__(self) -> Iterator[SimpleNamespace]:
        """Iterate the canned rows -- mirrors the real driver's ResultSet."""
        return iter(self._rows)


def test_cassandra_store_round_trips_through_the_row_mapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cassandra store round trips through the row mapper."""
    from app.modules.x402_storage.stores import cassandra as cassandra_store

    now = datetime(2026, 9, 4, tzinfo=UTC)
    backup_id = "11111111-1111-1111-1111-111111111111"
    row = SimpleNamespace(
        wallet="W1",
        backup_id=uuid.UUID(backup_id),
        connector="local",
        connector_params={"path": "11/x.bin"},
        size_bytes=42,
        content_hash="h" * 64,
        label="my label",
        created_at=now,
        expires_at=now + timedelta(days=180),
        status=STATUS_ACTIVE,
        settlement_tx_id="TX1",
    )
    executed: list[tuple[str, tuple]] = []

    class _Session:
        def execute(self, stmt: str, params: tuple) -> _CassandraResult:
            """Execute."""
            executed.append((stmt, params))
            return _CassandraResult([row])

    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)
    monkeypatch.setattr(cassandra_store, "get_cassandra_session", lambda: _Session())

    cass_store = cassandra_store.CassandraBackupStore()
    got = cass_store.get("W1", backup_id)
    assert got is not None
    assert got.size_bytes == 42
    assert got.content_hash == "h" * 64
    assert got.created_at_epoch == int(now.timestamp())
    assert "x402_storage_backups" in executed[0][0]
    assert executed[0][1] == ("W1", uuid.UUID(backup_id))

    recent = cass_store.list_recent("W1", limit=5)
    assert len(recent) == 1
    assert recent[0].backup_id == backup_id


def test_cassandra_store_get_returns_none_for_a_malformed_backup_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cassandra store get returns none for a malformed backup id."""
    from app.modules.x402_storage.stores import cassandra as cassandra_store

    class _Session:
        def execute(self, *_a: object, **_kw: object) -> Never:
            """Execute."""
            raise AssertionError("must not query Cassandra for an unparseable id")

    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)
    monkeypatch.setattr(cassandra_store, "get_cassandra_session", lambda: _Session())
    assert cassandra_store.CassandraBackupStore().get("W1", "not-a-uuid") is None
    # A well-formed but non-version-1 UUID is "not found" too: backup_id is a
    # timeuuid column, and binding a NIL or v4 uuid to one is a Cassandra
    # InvalidRequest the driver raised straight through to a live 500
    # (found 2026-09-05 on POST /storage/backups/<nil>/renew).
    assert cassandra_store.CassandraBackupStore().get("W1", "0" * 32) is None
    assert cassandra_store.CassandraBackupStore().get("W1", str(uuid.uuid4())) is None


@pytest.mark.usefixtures("fake_redis")
def test_renew_402_advertises_the_route_template_not_one_url_per_backup(
    monkeypatch: pytest.MonkeyPatch, store: InMemoryBackupStore
) -> None:
    """The renew offer must carry the {backup_id} template as its resource path so the Bazaar catalogs one entry, not one per backup (found live 2026-09-05: no resource_path was passed)."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _r: False)
    owner = "O" * 58
    monkeypatch.setattr(
        storage_routes, "backup_service", BackupService(store=store, backend=_FakeBackend())
    )
    backup = _stored(store, wallet=owner, expires_in_days=40)
    captured: dict[str, Any] = {}

    def _gate(_request: Request, **kwargs: object) -> x402_guard.PaymentResult:
        captured.update(kwargs)
        return x402_guard.PaymentResult(
            error=Response(status_code=402, headers={}, description="{}")
        )

    monkeypatch.setattr(storage_routes, "require_paid_request", _gate)

    response = storage_routes.x402_storage_renew_backup(
        _request(
            method="POST",
            path_params={"backup_id": backup.backup_id},
            query={"wallet": owner},
        )
    )

    assert response.status_code == 402
    assert captured["resource_path"] == "/api/v1/x402/storage/backups/{backup_id}/renew"


def test_cassandra_store_upsert_writes_the_expiry_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A first insert writes the canonical row then the expiry projection (store before mark)."""
    from app.modules.x402_storage.stores import cassandra as cassandra_store

    now = datetime(2026, 9, 4, tzinfo=UTC)
    backup_id = "11111111-1111-1111-1111-111111111111"
    executed: list[str] = []

    class _Session:
        def execute(self, stmt: str, params: tuple) -> _CassandraResult:
            """Execute."""
            executed.append(stmt)
            _ = params
            return _CassandraResult([])

    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)
    monkeypatch.setattr(cassandra_store, "get_cassandra_session", lambda: _Session())

    item = StoredBackup(
        wallet="W1",
        backup_id=backup_id,
        connector="local",
        connector_params={"path": "11/x.bin"},
        size_bytes=4,
        content_hash="h" * 64,
        created_at_epoch=int(now.timestamp()),
        expires_at_epoch=int((now + timedelta(days=90)).timestamp()),
        status=STATUS_ACTIVE,
        settlement_tx_id="T",
    )
    cassandra_store.CassandraBackupStore().upsert(item)
    assert any("x402_storage_backups" in stmt and "INSERT" in stmt for stmt in executed)
    assert any("x402_storage_by_expiry" in stmt and "INSERT" in stmt for stmt in executed)


# --------------------------------------------------------------------------- #
# services/auth_service.py -- wallet-signature challenge/verify
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis")
def test_challenge_verifies_for_the_real_key_holder() -> None:
    """Challenge verifies for the real key holder."""
    sk, addr = account.generate_account()
    challenge = auth_service.issue_challenge(addr)
    sig = util.sign_bytes(challenge.signing_message.encode(), sk)

    assert (
        auth_service.verify_challenge_signature(
            wallet=addr, nonce=challenge.nonce, proof_method="signed_bytes", signature_b64=sig
        )
        is True
    )


@pytest.mark.usefixtures("fake_redis")
def test_challenge_rejects_a_signature_from_the_wrong_wallet() -> None:
    """Challenge rejects a signature from the wrong wallet."""
    _sk_owner, owner = account.generate_account()
    sk_attacker, _attacker = account.generate_account()
    challenge = auth_service.issue_challenge(owner)
    # The attacker signs the OWNER's real challenge with their OWN key.
    forged_sig = util.sign_bytes(challenge.signing_message.encode(), sk_attacker)

    assert (
        auth_service.verify_challenge_signature(
            wallet=owner,
            nonce=challenge.nonce,
            proof_method="signed_bytes",
            signature_b64=forged_sig,
        )
        is False
    )


@pytest.mark.usefixtures("fake_redis")
def test_challenge_is_single_use_a_replayed_signature_fails_the_second_time() -> None:
    """Challenge is single use a replayed signature fails the second time."""
    sk, addr = account.generate_account()
    challenge = auth_service.issue_challenge(addr)
    sig = util.sign_bytes(challenge.signing_message.encode(), sk)

    assert (
        auth_service.verify_challenge_signature(
            wallet=addr, nonce=challenge.nonce, proof_method="signed_bytes", signature_b64=sig
        )
        is True
    )
    # Same signature, same nonce, replayed: GETDEL already consumed it.
    assert (
        auth_service.verify_challenge_signature(
            wallet=addr, nonce=challenge.nonce, proof_method="signed_bytes", signature_b64=sig
        )
        is False
    )


@pytest.mark.usefixtures("fake_redis")
def test_challenge_rejects_an_expired_challenge() -> None:
    """Challenge rejects an expired challenge."""
    sk, addr = account.generate_account()
    challenge = auth_service.issue_challenge(addr)
    sig = util.sign_bytes(challenge.signing_message.encode(), sk)
    # Simulate the TTL having passed by rewriting the stored expires_at into
    # the past (GETDEL still returns it -- the freshness check is our own,
    # not Redis's TTL, which is a belt-and-suspenders margin above this).
    from app.core import serialization

    key = auth_service._challenge_key(addr, challenge.nonce)
    stale = serialization.loads(auth_service.get_redis().store[key])
    stale["expires_at"] = int(datetime.now(tz=UTC).timestamp()) - 10
    auth_service.get_redis().store[key] = serialization.dumps(stale)

    assert (
        auth_service.verify_challenge_signature(
            wallet=addr, nonce=challenge.nonce, proof_method="signed_bytes", signature_b64=sig
        )
        is False
    )


@pytest.mark.usefixtures("fake_redis")
def test_challenge_verification_fails_for_an_unknown_nonce() -> None:
    """Challenge verification fails for an unknown nonce."""
    _sk, addr = account.generate_account()
    assert (
        auth_service.verify_challenge_signature(
            wallet=addr, nonce="never-issued", proof_method="signed_bytes", signature_b64="AA=="
        )
        is False
    )


def test_issue_challenge_raises_on_a_redis_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Issue challenge raises on a redis failure."""
    monkeypatch.setattr(auth_service, "get_redis", lambda: _BrokenRedis())
    with pytest.raises(auth_service.StorageAuthStoreError):
        auth_service.issue_challenge("W1")


def test_verify_challenge_signature_raises_on_a_redis_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify challenge signature raises on a redis failure."""
    monkeypatch.setattr(auth_service, "get_redis", lambda: _BrokenRedis())
    with pytest.raises(auth_service.StorageAuthStoreError):
        auth_service.verify_challenge_signature(
            wallet="W1", nonce="n", proof_method="signed_bytes", signature_b64="AA=="
        )


# --------------------------------------------------------------------------- #
# services/backup_service.py -- pricing, caps, capacity
# --------------------------------------------------------------------------- #
def test_kb_units_rounds_up_with_a_floor_of_one() -> None:
    """Kb units rounds up with a floor of one."""
    assert kb_units(0) == 1
    assert kb_units(1) == 1
    assert kb_units(1024) == 1
    assert kb_units(1024 + 1) == 2


def test_compute_price_confirmed_example_10mib_full_term_is_2_cents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Owner+reviewer confirmed example (2026-09-06): 10 MiB for the full 90-day term is exactly $0.02, a 10x cut from the old flat $0.02/MB * whole-MB-rounded rate."""
    monkeypatch.setattr(settings, "x402_storage_term_days", 90)
    monkeypatch.setattr(settings, "x402_storage_price_per_kb_per_90d", "$0.000001953125")
    monkeypatch.setattr(settings, "x402_storage_price_floor", "$0.001")
    assert compute_price(10 * 1024 * 1024, 90) == "$0.020000"
    # Same result when retention_days is omitted (defaults to the full term).
    assert compute_price(10 * 1024 * 1024) == "$0.020000"


def test_compute_price_scales_linearly_with_retention_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Half the retention (of a size well above the floor) is half the price."""
    monkeypatch.setattr(settings, "x402_storage_term_days", 90)
    monkeypatch.setattr(settings, "x402_storage_price_per_kb_per_90d", "$0.000001953125")
    monkeypatch.setattr(settings, "x402_storage_price_floor", "$0.001")
    full = compute_price(10 * 1024 * 1024, 90)
    half = compute_price(10 * 1024 * 1024, 45)
    assert full == "$0.020000"
    assert half == "$0.010000"


def test_compute_price_bills_by_kb_not_rounded_up_to_a_whole_mb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file well under 1 MB is billed proportionally to its own KB size, not inflated to a full-MB charge (regression: the old formula rounded any sub-MB size up to one whole MB unit)."""
    monkeypatch.setattr(settings, "x402_storage_term_days", 90)
    monkeypatch.setattr(settings, "x402_storage_price_per_kb_per_90d", "$0.000001953125")
    monkeypatch.setattr(settings, "x402_storage_price_floor", "$0.001")
    # 700 KiB, well above where the floor would otherwise dominate (512 KiB
    # at the full term) but well below a full MiB.
    price = compute_price(700 * 1024, 90)
    assert price == "$0.001367"
    full_mb_price = compute_price(1024 * 1024, 90)
    assert Decimal(price.lstrip("$")) < Decimal(full_mb_price.lstrip("$"))


def test_compute_price_floors_a_tiny_short_retention_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 1 KB file at a 1-day retention would price out to a fraction of a cent under the raw linear formula -- the floor takes over instead of charging (or trying to settle) an unsettleable amount."""
    monkeypatch.setattr(settings, "x402_storage_term_days", 90)
    monkeypatch.setattr(settings, "x402_storage_price_per_kb_per_90d", "$0.000001953125")
    monkeypatch.setattr(settings, "x402_storage_price_floor", "$0.001")
    assert compute_price(1024, 1) == "$0.001000"


def test_validate_retention_days_rejects_out_of_range(monkeypatch: pytest.MonkeyPatch) -> None:
    """0, negative, and over-the-ceiling retention_days are all rejected."""
    monkeypatch.setattr(settings, "x402_storage_term_days", 90)
    with pytest.raises(StorageError):
        validate_retention_days(0)
    with pytest.raises(StorageError):
        validate_retention_days(-1)
    with pytest.raises(StorageError):
        validate_retention_days(91)
    validate_retention_days(1)  # in range: fine
    validate_retention_days(90)  # at the ceiling: fine


def test_validate_declared_size_rejects_non_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Validate declared size rejects non positive."""
    monkeypatch.setattr(settings, "x402_storage_max_backup_mb", 10)
    with pytest.raises(StorageError):
        validate_declared_size(0)
    with pytest.raises(StorageError):
        validate_declared_size(-1)


def test_validate_declared_size_rejects_over_the_per_blob_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validate declared size rejects over the per blob cap."""
    monkeypatch.setattr(settings, "x402_storage_max_backup_mb", 1)
    validate_declared_size(1024 * 1024)  # exactly at the cap: fine
    with pytest.raises(StorageError) as excinfo:
        validate_declared_size(1024 * 1024 + 1)
    assert excinfo.value.http_status == 413


def test_create_succeeds_and_records_content_hash(store: InMemoryBackupStore) -> None:
    """Create succeeds and records content hash."""
    backend = _FakeBackend()
    service = BackupService(store=store, backend=backend)
    data = b"opaque agent state"
    backup = service.create(
        wallet="W1", data=data, declared_size_bytes=len(data), label="cfg", settlement_tx_id="TX1"
    )
    assert backup.content_hash == hashlib.sha256(data).hexdigest()
    assert backup.size_bytes == len(data)
    assert store.get("W1", backup.backup_id) == backup


def test_create_refuses_when_actual_size_exceeds_declared_and_writes_nothing(
    store: InMemoryBackupStore,
) -> None:
    """Create refuses when actual size exceeds declared and writes nothing."""
    backend = _FakeBackend()
    service = BackupService(store=store, backend=backend)
    with pytest.raises(StorageError) as excinfo:
        service.create(
            wallet="W1", data=b"12345", declared_size_bytes=3, label="", settlement_tx_id="TX1"
        )
    assert excinfo.value.code == "size_mismatch"
    assert excinfo.value.http_status == 422
    assert store.list_recent("W1", limit=10) == []
    assert backend.usage_bytes() == 0  # nothing was written to the connector either


def test_create_refuses_when_no_connector_is_configured(store: InMemoryBackupStore) -> None:
    """Create refuses when no connector is configured."""
    service = BackupService(
        store=store, backend=None
    )  # backend_for() falls through to the real factory
    with pytest.raises(StorageCapacityUnavailable):
        service.create(
            wallet="W1", data=b"x", declared_size_bytes=1, label="", settlement_tx_id="TX1"
        )


def test_create_refuses_when_writing_would_exceed_the_global_ceiling(
    store: InMemoryBackupStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Create refuses when writing would exceed the global ceiling."""
    monkeypatch.setattr(settings, "x402_storage_local_max_total_mb", 1)  # 1 MB ceiling
    backend = _FakeBackend(usage=1024 * 1024 - 5)  # 5 bytes of headroom left
    service = BackupService(store=store, backend=backend)
    with pytest.raises(StorageCapacityUnavailable):
        service.create(
            wallet="W1",
            data=b"0123456789",
            declared_size_bytes=10,
            label="",
            settlement_tx_id="TX1",
        )
    assert store.list_recent("W1", limit=10) == []


def test_create_refuses_when_usage_bytes_itself_fails(store: InMemoryBackupStore) -> None:
    """Create refuses when usage bytes itself fails."""
    backend = _FakeBackend(fail_usage=True)
    service = BackupService(store=store, backend=backend)
    with pytest.raises(StorageCapacityUnavailable):
        service.create(
            wallet="W1", data=b"x", declared_size_bytes=1, label="", settlement_tx_id="TX1"
        )


def test_get_live_hides_expired_and_deleted_rows(store: InMemoryBackupStore) -> None:
    """Get live hides expired and deleted rows."""
    service = BackupService(store=store, backend=_FakeBackend())
    now = datetime.now(tz=UTC)
    live = _stored(store, wallet="W1", backup_id="1" * 8 + "-1111-1111-1111-111111111111", now=now)
    expired = _stored(
        store,
        wallet="W1",
        backup_id="2" * 8 + "-1111-1111-1111-111111111111",
        now=now - timedelta(days=400),
    )
    deleted = _stored(
        store,
        wallet="W1",
        backup_id="3" * 8 + "-1111-1111-1111-111111111111",
        status=STATUS_DELETED,
        now=now,
    )
    assert service.get_live("W1", live.backup_id, now=now) is not None
    assert service.get_live("W1", expired.backup_id, now=now) is None
    assert service.get_live("W1", deleted.backup_id, now=now) is None
    # A DIFFERENT wallet can never resolve another wallet's row, live or not.
    assert service.get_live("W2", live.backup_id, now=now) is None


def test_list_live_excludes_expired_and_deleted_and_is_newest_first(
    store: InMemoryBackupStore,
) -> None:
    """List live excludes expired and deleted and is newest first."""
    service = BackupService(store=store, backend=_FakeBackend())
    now = datetime.now(tz=UTC)
    older = _stored(
        store,
        wallet="W1",
        backup_id="1" * 8 + "-1111-1111-1111-111111111111",
        now=now - timedelta(days=1),
    )
    newer = _stored(store, wallet="W1", backup_id="2" * 8 + "-1111-1111-1111-111111111111", now=now)
    _stored(
        store,
        wallet="W1",
        backup_id="3" * 8 + "-1111-1111-1111-111111111111",
        now=now - timedelta(days=400),
    )
    items = service.list_live("W1", limit=10, now=now)
    assert [i.backup_id for i in items] == [newer.backup_id, older.backup_id]


def test_delete_marks_the_row_deleted_then_removes_connector_bytes(
    store: InMemoryBackupStore,
) -> None:
    """Delete marks the row deleted, then removes connector bytes."""
    backend = _FakeBackend()
    service = BackupService(store=store, backend=backend)
    backup = service.create(
        wallet="W1", data=b"bytes", declared_size_bytes=5, label="", settlement_tx_id="T"
    )
    assert backend.get(backup.connector_params) == b"bytes"

    service.delete(backup)

    assert backend.get(backup.connector_params) is None
    row = store.get("W1", backup.backup_id)
    assert row is not None
    assert row.status == STATUS_DELETED


def test_delete_marks_the_row_deleted_even_if_the_connector_delete_fails(
    store: InMemoryBackupStore,
) -> None:
    """Delete marks the row deleted even if the connector delete fails."""

    class _BoomBackend(_FakeBackend):
        def delete(self, _connector_params: dict[str, str]) -> None:
            """Simulate a connector delete failure."""
            raise RuntimeError("disk error")

    backend = _BoomBackend()
    service = BackupService(store=store, backend=backend)
    backup = service.create(
        wallet="W1", data=b"bytes", declared_size_bytes=5, label="", settlement_tx_id="T"
    )

    service.delete(backup)  # must not raise

    row = store.get("W1", backup.backup_id)
    assert row is not None
    assert row.status == STATUS_DELETED


def test_renew_caps_remaining_term_instead_of_stacking(
    store: InMemoryBackupStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Renewing a still-live backup refreshes up to max_remaining_days from now, not a stacked extra term."""
    monkeypatch.setattr(settings, "x402_storage_term_days", 90)
    monkeypatch.setattr(settings, "x402_storage_max_remaining_days", 90)
    service = BackupService(store=store, backend=_FakeBackend())
    now = datetime.now(tz=UTC)
    backup = _stored(store, wallet="W1", now=now, expires_in_days=80)

    renewed = service.renew(backup, settlement_tx_id="TX-RENEW", now=now)

    expected = int((now + timedelta(days=90)).timestamp())
    assert abs(renewed.expires_at_epoch - expected) <= 1
    assert renewed.settlement_tx_id == "TX-RENEW"


def test_renew_resurrects_an_already_expired_backup_from_now(
    store: InMemoryBackupStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Renew resurrects an already expired backup from now, still capped at max remaining."""
    monkeypatch.setattr(settings, "x402_storage_term_days", 90)
    monkeypatch.setattr(settings, "x402_storage_max_remaining_days", 90)
    service = BackupService(store=store, backend=_FakeBackend())
    now = datetime.now(tz=UTC)
    backup = _stored(store, wallet="W1", now=now - timedelta(days=91), expires_in_days=90)
    assert backup.is_live(now_epoch=int(now.timestamp())) is False

    renewed = service.renew(backup, settlement_tx_id="TX-RENEW", now=now)

    expected = int((now + timedelta(days=90)).timestamp())
    assert abs(renewed.expires_at_epoch - expected) <= 1


# --------------------------------------------------------------------------- #
# api/routes.py -- auth challenge route
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis")
def test_auth_challenge_route_issues_a_nonce() -> None:
    """Auth challenge route issues a nonce."""
    response = storage_routes.x402_storage_auth_challenge(
        _request(
            method="POST",
            body=json.dumps({"wallet": "A" * 58}).encode(),
            path="/api/v1/x402/storage/auth/challenge",
        )
    )
    assert isinstance(response, dict)
    assert response["nonce"]
    assert "signed_bytes" in response["proof_methods"]


@pytest.mark.usefixtures("fake_redis")
def test_auth_challenge_route_rejects_a_malformed_body() -> None:
    """Auth challenge route rejects a malformed body."""
    response = storage_routes.x402_storage_auth_challenge(
        _request(method="POST", body=b"{not json", path="/api/v1/x402/storage/auth/challenge")
    )
    assert isinstance(response, Response)
    assert response.status_code == 400


@pytest.mark.usefixtures("fake_redis")
def test_auth_challenge_route_is_rate_limited_per_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Auth challenge route is rate limited per ip."""
    monkeypatch.setattr(settings, "x402_storage_rate_limit_per_hour", 1)
    body = json.dumps({"wallet": "A" * 58}).encode()

    first = storage_routes.x402_storage_auth_challenge(
        _request(
            method="POST",
            body=body,
            headers={"X-Real-IP": "203.0.113.1"},
            path="/api/v1/x402/storage/auth/challenge",
        )
    )
    assert isinstance(first, dict)
    second = storage_routes.x402_storage_auth_challenge(
        _request(
            method="POST",
            body=body,
            headers={"X-Real-IP": "203.0.113.1"},
            path="/api/v1/x402/storage/auth/challenge",
        )
    )
    assert isinstance(second, Response)
    assert second.status_code == 429


# --------------------------------------------------------------------------- #
# api/routes.py -- authenticated free routes (list/detail/delete)
# --------------------------------------------------------------------------- #
def _authed_query(wallet: str, nonce: str, sig: str) -> dict[str, str]:
    """The query params a wallet-signature-authenticated route reads."""
    return {"wallet": wallet, "nonce": nonce, "proof_method": "signed_bytes", "signature_b64": sig}


@pytest.mark.usefixtures("fake_redis")
def test_list_and_get_and_delete_require_a_valid_wallet_signature(
    monkeypatch: pytest.MonkeyPatch, store: InMemoryBackupStore
) -> None:
    """List and get and delete require a valid wallet signature."""
    monkeypatch.setattr(
        storage_routes, "backup_service", BackupService(store=store, backend=_FakeBackend())
    )
    sk, addr = account.generate_account()
    _stored(store, wallet=addr)

    # No auth params at all.
    response = storage_routes.x402_storage_list_backups(_request())
    assert response.status_code == 400

    # A garbage signature over the right nonce.
    challenge = auth_service.issue_challenge(addr)
    response = storage_routes.x402_storage_list_backups(
        _request(query=_authed_query(addr, challenge.nonce, "not-a-real-signature=="))
    )
    assert response.status_code == 401

    # The real, valid signature works.
    challenge2 = auth_service.issue_challenge(addr)
    sig = util.sign_bytes(challenge2.signing_message.encode(), sk)
    response = storage_routes.x402_storage_list_backups(
        _request(query=_authed_query(addr, challenge2.nonce, sig))
    )
    assert isinstance(response, dict)
    assert len(response["items"]) == 1


@pytest.mark.usefixtures("fake_redis")
def test_a_wallet_can_never_see_or_delete_another_wallets_backup(
    monkeypatch: pytest.MonkeyPatch, store: InMemoryBackupStore
) -> None:
    """A wallet can never see or delete another wallets backup."""
    monkeypatch.setattr(
        storage_routes, "backup_service", BackupService(store=store, backend=_FakeBackend())
    )
    _sk_owner, owner = account.generate_account()
    sk_other, other = account.generate_account()
    backup = _stored(store, wallet=owner)

    challenge = auth_service.issue_challenge(other)
    sig = util.sign_bytes(challenge.signing_message.encode(), sk_other)

    response = storage_routes.x402_storage_get_backup(
        _request(
            query=_authed_query(other, challenge.nonce, sig),
            path_params={"backup_id": backup.backup_id},
        )
    )
    assert response.status_code == 404

    challenge2 = auth_service.issue_challenge(other)
    sig2 = util.sign_bytes(challenge2.signing_message.encode(), sk_other)
    response = storage_routes.x402_storage_delete_backup(
        _request(
            query=_authed_query(other, challenge2.nonce, sig2),
            path_params={"backup_id": backup.backup_id},
        )
    )
    assert response.status_code == 404
    # The real owner's row is untouched.
    assert store.get(owner, backup.backup_id).status == STATUS_ACTIVE


@pytest.mark.usefixtures("fake_redis")
def test_get_backup_restores_bytes_and_verifies_the_hash(
    monkeypatch: pytest.MonkeyPatch, store: InMemoryBackupStore
) -> None:
    """Get backup restores bytes and verifies the hash."""
    backend = _FakeBackend()
    service = BackupService(store=store, backend=backend)
    monkeypatch.setattr(storage_routes, "backup_service", service)
    sk, addr = account.generate_account()
    data = b"the actual backup bytes"
    backup = service.create(
        wallet=addr, data=data, declared_size_bytes=len(data), label="", settlement_tx_id="T"
    )

    challenge = auth_service.issue_challenge(addr)
    sig = util.sign_bytes(challenge.signing_message.encode(), sk)
    response = storage_routes.x402_storage_get_backup(
        _request(
            query=_authed_query(addr, challenge.nonce, sig),
            path_params={"backup_id": backup.backup_id},
        )
    )
    assert isinstance(response, dict)
    assert base64.b64decode(response["data"]) == data
    assert response["content_hash"] == hashlib.sha256(data).hexdigest()


@pytest.mark.usefixtures("fake_redis")
def test_get_backup_500s_on_a_content_hash_mismatch_instead_of_serving_corrupt_data(
    monkeypatch: pytest.MonkeyPatch, store: InMemoryBackupStore
) -> None:
    """Get backup 500s on a content hash mismatch instead of serving corrupt data."""
    backend = _FakeBackend()
    service = BackupService(store=store, backend=backend)
    monkeypatch.setattr(storage_routes, "backup_service", service)
    sk, addr = account.generate_account()
    data = b"the real bytes"
    backup = service.create(
        wallet=addr, data=data, declared_size_bytes=len(data), label="", settlement_tx_id="T"
    )
    # Corrupt the stored bytes in place, bypassing put() -- simulates silent
    # on-disk corruption the metadata row's content_hash does not know about.
    backend._blobs[backup.connector_params["key"]] = b"corrupted bytes!"

    challenge = auth_service.issue_challenge(addr)
    sig = util.sign_bytes(challenge.signing_message.encode(), sk)
    response = storage_routes.x402_storage_get_backup(
        _request(
            query=_authed_query(addr, challenge.nonce, sig),
            path_params={"backup_id": backup.backup_id},
        )
    )
    assert response.status_code == 500
    assert "integrity_check_failed" in response.description


@pytest.mark.usefixtures("fake_redis")
def test_authenticated_routes_are_rate_limited_per_ip_before_verification(
    monkeypatch: pytest.MonkeyPatch, store: InMemoryBackupStore
) -> None:
    """Authenticated routes are rate limited per ip before verification."""
    monkeypatch.setattr(
        storage_routes, "backup_service", BackupService(store=store, backend=_FakeBackend())
    )
    monkeypatch.setattr(settings, "x402_storage_rate_limit_per_hour", 1)
    _sk, addr = account.generate_account()

    first = storage_routes.x402_storage_list_backups(
        _request(headers={"X-Real-IP": "203.0.113.5"}, query=_authed_query(addr, "n", "AA=="))
    )
    assert isinstance(first, Response)
    assert first.status_code == 401  # got past the IP gate, failed real verification

    second = storage_routes.x402_storage_list_backups(
        _request(headers={"X-Real-IP": "203.0.113.5"}, query=_authed_query(addr, "n", "AA=="))
    )
    assert second.status_code == 429


# --------------------------------------------------------------------------- #
# api/routes.py -- paid create route
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis")
def test_create_backup_rejects_a_bad_declared_size_before_the_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Create backup rejects a bad declared size before the gate."""
    monkeypatch.setattr(storage_routes, "require_paid_request", _must_not_charge)
    response = storage_routes.x402_storage_create_backup(
        _request(method="POST", query={"declared_size_bytes": "not-a-number"})
    )
    assert response.status_code == 400


@pytest.mark.usefixtures("fake_redis")
def test_create_backup_rejects_over_the_per_blob_cap_before_the_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Create backup rejects over the per blob cap before the gate."""
    monkeypatch.setattr(settings, "x402_storage_max_backup_mb", 1)
    monkeypatch.setattr(storage_routes, "require_paid_request", _must_not_charge)
    response = storage_routes.x402_storage_create_backup(
        _request(method="POST", query={"declared_size_bytes": str(2 * 1024 * 1024)})
    )
    assert response.status_code == 413


@pytest.mark.usefixtures("fake_redis")
@pytest.mark.parametrize("bad_retention", ["0", "-1", "91"])
def test_create_backup_rejects_out_of_range_retention_days_before_the_gate(
    monkeypatch: pytest.MonkeyPatch, bad_retention: str
) -> None:
    """0, negative, and over-the-ceiling retention_days are all a free 400 before any payment is taken."""
    monkeypatch.setattr(settings, "x402_storage_term_days", 90)
    monkeypatch.setattr(storage_routes, "require_paid_request", _must_not_charge)
    response = storage_routes.x402_storage_create_backup(
        _request(
            method="POST",
            query={"declared_size_bytes": "10", "retention_days": bad_retention},
        )
    )
    assert response.status_code == 400
    assert "retention_days" in response.description


@pytest.mark.usefixtures("fake_redis")
def test_create_backup_rejects_invalid_base64_before_the_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Create backup rejects invalid base64 before the gate."""
    monkeypatch.setattr(storage_routes, "require_paid_request", _must_not_charge)
    response = storage_routes.x402_storage_create_backup(
        _request(
            method="POST",
            query={"declared_size_bytes": "10"},
            body=json.dumps({"data": "not-valid-base64!!!"}).encode(),
        )
    )
    assert response.status_code == 400


@pytest.mark.usefixtures("fake_redis")
def test_create_backup_settles_and_stores_on_success(
    monkeypatch: pytest.MonkeyPatch, store: InMemoryBackupStore
) -> None:
    """Create backup settles and stores on success."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _r: False)
    service = BackupService(store=store, backend=_FakeBackend())
    monkeypatch.setattr(storage_routes, "backup_service", service)
    payer = "P" * 58
    monkeypatch.setattr(
        storage_routes, "require_paid_request", lambda *_a, **_kw: _settled_result(payer)
    )
    fulfilled: list[str] = []
    monkeypatch.setattr(
        storage_routes, "mark_fulfilled", lambda txid, **_kw: fulfilled.append(txid)
    )
    data = b"hello agent state"
    body = json.dumps({"data": base64.b64encode(data).decode(), "label": "cfg"}).encode()

    response = storage_routes.x402_storage_create_backup(
        _request(method="POST", query={"declared_size_bytes": str(len(data))}, body=body)
    )

    assert response.status_code == 200
    payload = json.loads(response.description)
    assert payload["content_hash"] == hashlib.sha256(data).hexdigest()
    assert payload["settlement_tx_id"] == "TX1"
    assert fulfilled == ["TX1"]
    assert store.get(payer, payload["backup_id"]) is not None


@pytest.mark.usefixtures("fake_redis")
def test_create_backup_size_mismatch_is_never_refunded(
    monkeypatch: pytest.MonkeyPatch, store: InMemoryBackupStore
) -> None:
    """A caller-fault rejection (claimed a smaller size than actually sent): payment kept, no refund attempted, no breaker count."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _r: False)
    service = BackupService(store=store, backend=_FakeBackend())
    monkeypatch.setattr(storage_routes, "backup_service", service)
    monkeypatch.setattr(
        storage_routes, "require_paid_request", lambda *_a, **_kw: _settled_result("P" * 58)
    )
    monkeypatch.setattr(
        paid_request_module,
        "send_refund",
        lambda **_kw: (_ for _ in ()).throw(AssertionError("size_mismatch must never be refunded")),
    )
    monkeypatch.setattr(
        circuit_breaker,
        "record_refund_failure",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("size_mismatch must never count against the circuit breaker")
        ),
    )
    data = b"12345"
    body = json.dumps({"data": base64.b64encode(data).decode()}).encode()

    response = storage_routes.x402_storage_create_backup(
        _request(method="POST", query={"declared_size_bytes": "3"}, body=body)
    )

    assert response.status_code == 422
    assert "size_mismatch" in response.description
    assert store.list_recent("P" * 58, limit=10) == []


@pytest.mark.usefixtures("fake_redis")
def test_create_backup_capacity_unavailable_is_refunded_and_counts_against_the_breaker(
    monkeypatch: pytest.MonkeyPatch, store: InMemoryBackupStore
) -> None:
    """Unlike size_mismatch, a capacity failure is OUR fault: refunded, and the resource's circuit breaker is incremented."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _r: False)
    monkeypatch.setattr(settings, "x402_storage_local_max_total_mb", 1)
    backend = _FakeBackend(usage=1024 * 1024)  # already full
    service = BackupService(store=store, backend=backend)
    monkeypatch.setattr(storage_routes, "backup_service", service)
    monkeypatch.setattr(
        storage_routes,
        "require_paid_request",
        lambda *_a, **_kw: _settled_result("P" * 58, txid="TXCAP"),
    )
    refund_calls: list[str] = []
    monkeypatch.setattr(
        paid_request_module,
        "send_refund",
        lambda **_kw: (
            refund_calls.append("refunded")
            or SimpleNamespace(status="sent", txid="REFUND1", error=None)
        ),
    )
    breaker_calls: list[str] = []
    monkeypatch.setattr(
        circuit_breaker,
        "record_refund_failure",
        lambda resource: breaker_calls.append(resource),
    )
    data = b"1234567890"
    body = json.dumps({"data": base64.b64encode(data).decode()}).encode()

    response = storage_routes.x402_storage_create_backup(
        _request(method="POST", query={"declared_size_bytes": str(len(data))}, body=body)
    )

    assert response.status_code == 503
    body_json = json.loads(response.description)
    assert body_json["error"]["code"] in (
        "product_failed_refunded",
        "product_failed_refund_pending",
    )
    assert refund_calls == ["refunded"]
    assert breaker_calls == [storage_routes._RESOURCE_CREATE]
    assert store.list_recent("P" * 58, limit=10) == []


@pytest.mark.usefixtures("fake_redis")
def test_create_backup_circuit_breaker_blocks_before_the_payment_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Create backup circuit breaker blocks before the payment gate."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _r: True)
    monkeypatch.setattr(storage_routes, "require_paid_request", _must_not_charge)
    response = storage_routes.x402_storage_create_backup(
        _request(
            method="POST",
            query={"declared_size_bytes": "10"},
            body=json.dumps({"data": "AAAA"}).encode(),
        )
    )
    assert response.status_code == 503


# --------------------------------------------------------------------------- #
# api/routes.py -- paid renew route
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis")
def test_renew_unknown_backup_is_a_404_that_never_reaches_the_gate(
    monkeypatch: pytest.MonkeyPatch, store: InMemoryBackupStore
) -> None:
    """Renew unknown backup is a 404 that never reaches the gate."""
    monkeypatch.setattr(
        storage_routes, "backup_service", BackupService(store=store, backend=_FakeBackend())
    )
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _r: False)
    monkeypatch.setattr(storage_routes, "require_paid_request", _must_not_charge)
    response = storage_routes.x402_storage_renew_backup(
        _request(
            method="POST",
            path_params={"backup_id": "no-such-id"},
            query={"wallet": "A" * 58},
        )
    )
    assert response.status_code == 404


@pytest.mark.usefixtures("fake_redis")
def test_renew_by_another_wallet_settles_but_is_refused_and_never_refunded(
    monkeypatch: pytest.MonkeyPatch, store: InMemoryBackupStore
) -> None:
    """Renew by another wallet settles but is refused and never refunded."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _r: False)
    owner = "O" * 58
    other = "Q" * 58
    monkeypatch.setattr(
        storage_routes, "backup_service", BackupService(store=store, backend=_FakeBackend())
    )
    backup = _stored(store, wallet=owner, expires_in_days=40)
    monkeypatch.setattr(
        storage_routes,
        "require_paid_request",
        lambda *_a, **_kw: _settled_result(other, txid="TXX"),
    )
    monkeypatch.setattr(
        paid_request_module,
        "send_refund",
        lambda **_kw: (_ for _ in ()).throw(
            AssertionError("an ownership rejection must never be refunded")
        ),
    )
    monkeypatch.setattr(
        storage_routes,
        "mark_fulfilled",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("must not mark fulfilled")),
    )

    response = storage_routes.x402_storage_renew_backup(
        _request(
            method="POST",
            path_params={"backup_id": backup.backup_id},
            query={"wallet": owner},
        )
    )

    assert response.status_code == 403
    assert "backup_owned_by_another_payer" in response.description
    assert response.headers["PAYMENT-RESPONSE"] == "ok"
    assert store.get(owner, backup.backup_id).expires_at_epoch == backup.expires_at_epoch


@pytest.mark.usefixtures("fake_redis")
def test_renew_by_the_owner_extends_the_term_and_marks_fulfilled(
    monkeypatch: pytest.MonkeyPatch, store: InMemoryBackupStore
) -> None:
    """Renew by the owner extends the term and marks fulfilled."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _r: False)
    owner = "O" * 58
    monkeypatch.setattr(
        storage_routes, "backup_service", BackupService(store=store, backend=_FakeBackend())
    )
    backup = _stored(store, wallet=owner, expires_in_days=40)
    monkeypatch.setattr(
        storage_routes,
        "require_paid_request",
        lambda *_a, **_kw: _settled_result(owner, txid="TXR"),
    )
    fulfilled: list[str] = []
    monkeypatch.setattr(
        storage_routes, "mark_fulfilled", lambda txid, **_kw: fulfilled.append(txid)
    )

    response = storage_routes.x402_storage_renew_backup(
        _request(
            method="POST",
            path_params={"backup_id": backup.backup_id},
            query={"wallet": owner},
        )
    )

    assert response.status_code == 200
    assert fulfilled == ["TXR"]
    assert store.get(owner, backup.backup_id).expires_at_epoch > backup.expires_at_epoch


@pytest.mark.usefixtures("fake_redis")
def test_renew_write_failure_after_ownership_confirmed_is_refunded_not_500(
    monkeypatch: pytest.MonkeyPatch, store: InMemoryBackupStore
) -> None:
    """Renew write failure after ownership confirmed is refunded not 500."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _r: False)
    owner = "O" * 58
    service = BackupService(store=store, backend=_FakeBackend())
    monkeypatch.setattr(storage_routes, "backup_service", service)
    backup = _stored(store, wallet=owner, expires_in_days=40)
    monkeypatch.setattr(
        storage_routes,
        "require_paid_request",
        lambda *_a, **_kw: _settled_result(owner, txid="TXR2"),
    )

    def _boom(*_a: object, **_kw: object) -> Never:
        """Simulate an unexpected failure in the product write."""
        raise RuntimeError("simulated store failure during renew")

    monkeypatch.setattr(service, "renew", _boom)
    monkeypatch.setattr(
        paid_request_module,
        "send_refund",
        lambda **_kw: SimpleNamespace(status="sent", txid="REFUND3", error=None),
    )

    response = storage_routes.x402_storage_renew_backup(
        _request(
            method="POST",
            path_params={"backup_id": backup.backup_id},
            query={"wallet": owner},
        )
    )

    assert response.status_code == 503
    body_json = json.loads(response.description)
    assert body_json["error"]["code"] in (
        "product_failed_refunded",
        "product_failed_refund_pending",
    )


# --------------------------------------------------------------------------- #
# api/routes.py -- delete
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis")
def test_delete_backup_is_idempotent_second_call_is_404(
    monkeypatch: pytest.MonkeyPatch, store: InMemoryBackupStore
) -> None:
    """Delete backup is idempotent second call is 404."""
    service = BackupService(store=store, backend=_FakeBackend())
    monkeypatch.setattr(storage_routes, "backup_service", service)
    sk, addr = account.generate_account()
    backup = service.create(
        wallet=addr, data=b"x", declared_size_bytes=1, label="", settlement_tx_id="T"
    )

    def _authed_delete() -> Response | dict:
        """Issue a fresh challenge, sign it, and call DELETE with it."""
        challenge = auth_service.issue_challenge(addr)
        sig = util.sign_bytes(challenge.signing_message.encode(), sk)
        return storage_routes.x402_storage_delete_backup(
            _request(
                query=_authed_query(addr, challenge.nonce, sig),
                path_params={"backup_id": backup.backup_id},
            )
        )

    first = _authed_delete()
    assert isinstance(first, dict)
    assert first["deleted"] is True

    second = _authed_delete()
    assert isinstance(second, Response)
    assert second.status_code == 404


@pytest.mark.usefixtures("fake_redis")
def test_renew_at_remaining_cap_is_refused_before_the_payment_gate(
    monkeypatch: pytest.MonkeyPatch, store: InMemoryBackupStore
) -> None:
    """A backup already at the 90-day remaining ceiling is a free 400, never charged."""
    monkeypatch.setattr(settings, "x402_storage_max_remaining_days", 90)
    owner = "O" * 58
    now = datetime.now(tz=UTC)
    backup = _stored(store, wallet=owner, now=now, expires_in_days=91)
    assert at_remaining_cap(backup, now=now)
    monkeypatch.setattr(
        storage_routes, "backup_service", BackupService(store=store, backend=_FakeBackend())
    )
    monkeypatch.setattr(storage_routes, "require_paid_request", _must_not_charge)

    response = storage_routes.x402_storage_renew_backup(
        _request(
            method="POST",
            path_params={"backup_id": backup.backup_id},
            query={"wallet": owner},
        )
    )

    assert isinstance(response, Response)
    assert response.status_code == 400
    assert "term_at_maximum" in response.description


@pytest.mark.usefixtures("fake_redis")
def test_reaper_leaves_grace_window_untouched(store: InMemoryBackupStore) -> None:
    """A backup that expired yesterday is still inside the 2-day grace window: not reaped, still renewable."""
    backend = _FakeBackend()
    service = BackupService(store=store, backend=backend)
    now = datetime.now(tz=UTC)
    backup = service.create(
        wallet="W1",
        data=b"keep-me",
        declared_size_bytes=8,
        label="",
        settlement_tx_id="T",
        now=now - timedelta(days=91),
    )
    assert backup.is_live(now_epoch=int(now.timestamp())) is False

    result = reap_expired(service, now=now)

    assert result["status"] == "ok"
    assert result["reaped"] == 0
    assert result["skipped_in_grace"] == 1
    row = store.get("W1", backup.backup_id)
    assert row is not None
    assert row.status == STATUS_ACTIVE
    assert backend.get(backup.connector_params) == b"keep-me"


@pytest.mark.usefixtures("fake_redis")
def test_reaper_deletes_after_grace(store: InMemoryBackupStore) -> None:
    """A backup expired more than grace_days ago is marked deleted and its bytes removed."""
    backend = _FakeBackend()
    service = BackupService(store=store, backend=backend)
    now = datetime.now(tz=UTC)
    backup = service.create(
        wallet="W1",
        data=b"gone",
        declared_size_bytes=4,
        label="",
        settlement_tx_id="T",
        now=now - timedelta(days=93),
    )

    result = reap_expired(service, now=now)

    assert result["status"] == "ok"
    assert result["reaped"] == 1
    row = store.get("W1", backup.backup_id)
    assert row is not None
    assert row.status == STATUS_DELETED
    assert backend.get(backup.connector_params) is None


@pytest.mark.usefixtures("fake_redis")
def test_reap_route_404s_when_token_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reap route 404s when the token is unset -- empty = disabled."""
    monkeypatch.setattr(settings, "x402_storage_reaper_token", "")
    response = storage_routes.x402_storage_reap(_request(method="POST"))
    assert isinstance(response, Response)
    assert response.status_code == 404


@pytest.mark.usefixtures("fake_redis")
def test_reap_route_rejects_a_wrong_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reap route rejects a wrong token."""
    monkeypatch.setattr(settings, "x402_storage_reaper_token", "secret-token")
    response = storage_routes.x402_storage_reap(
        _request(method="POST", headers={"X-Storage-Reaper-Token": "nope"})
    )
    assert isinstance(response, Response)
    assert response.status_code == 401


@pytest.mark.usefixtures("fake_redis")
def test_reap_route_runs_when_token_matches(
    monkeypatch: pytest.MonkeyPatch, store: InMemoryBackupStore
) -> None:
    """Reap route runs the walk when the token matches."""
    monkeypatch.setattr(settings, "x402_storage_reaper_token", "secret-token")
    monkeypatch.setattr(
        storage_routes, "backup_service", BackupService(store=store, backend=_FakeBackend())
    )
    result = storage_routes.x402_storage_reap(
        _request(method="POST", headers={"X-Storage-Reaper-Token": "secret-token"})
    )
    assert isinstance(result, dict)
    assert result["status"] == "ok"


def test_compute_expiry_epoch_never_exceeds_max_remaining(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Create and renew expiry math never grants more than max_remaining_days from now."""
    monkeypatch.setattr(settings, "x402_storage_term_days", 90)
    monkeypatch.setattr(settings, "x402_storage_max_remaining_days", 90)
    now = datetime(2026, 9, 4, tzinfo=UTC)
    created = compute_expiry_epoch(now, current_expires_at=None)
    assert created == int((now + timedelta(days=90)).timestamp())
    already_long = int((now + timedelta(days=80)).timestamp())
    renewed = compute_expiry_epoch(now, current_expires_at=already_long)
    assert renewed == int((now + timedelta(days=90)).timestamp())


# --------------------------------------------------------------------------- #
# Versioning (migration 115) -- services/backup_service.py
# --------------------------------------------------------------------------- #
def test_create_writes_an_implicit_version_1_row(store: InMemoryBackupStore) -> None:
    """create() writes a real version-1 row in the versions table, not just the head."""
    service = BackupService(store=store, backend=_FakeBackend())
    backup = service.create(
        wallet="W1", data=b"v1-bytes", declared_size_bytes=8, label="", settlement_tx_id="T1"
    )
    assert backup.current_version == 1
    version_row = store.get_version("W1", backup.backup_id, 1)
    assert version_row is not None
    assert version_row.content_hash == backup.content_hash
    assert version_row.size_bytes == 8


def test_add_version_updates_head_and_keeps_the_old_version_retrievable(
    store: InMemoryBackupStore,
) -> None:
    """add_version() makes the new bytes the head's current content while the old version stays readable on its own row."""
    backend = _FakeBackend()
    service = BackupService(store=store, backend=backend)
    backup = service.create(
        wallet="W1", data=b"first", declared_size_bytes=5, label="v1", settlement_tx_id="T1"
    )

    v2 = service.add_version(
        backup, data=b"second-version", declared_size_bytes=14, label="v2", settlement_tx_id="T2"
    )

    assert v2.version == 2
    head = service.get("W1", backup.backup_id)
    assert head is not None
    assert head.current_version == 2
    assert head.content_hash == hashlib.sha256(b"second-version").hexdigest()
    assert head.size_bytes == 14

    # version 1 is untouched and still independently readable.
    old = service.get_version_live("W1", backup.backup_id, 1)
    assert old is not None
    assert service.read_version_bytes(old) == b"first"
    # version 2 is readable both as "the current version" and by number.
    new = service.get_version_live("W1", backup.backup_id, 2)
    assert new is not None
    assert service.read_version_bytes(new) == b"second-version"
    assert service.read_bytes(head) == b"second-version"


def test_add_version_prices_and_caps_from_the_new_upload_not_the_old_size(
    store: InMemoryBackupStore,
) -> None:
    """add_version's own size_mismatch check is against the NEW declared_size_bytes, independent of the backup's existing size."""
    service = BackupService(store=store, backend=_FakeBackend())
    backup = service.create(
        wallet="W1", data=b"x", declared_size_bytes=1, label="", settlement_tx_id="T1"
    )
    with pytest.raises(StorageError) as excinfo:
        service.add_version(
            backup, data=b"12345", declared_size_bytes=3, label="", settlement_tx_id="T2"
        )
    assert excinfo.value.code == "size_mismatch"
    # Head must be untouched by a failed add_version.
    assert service.get("W1", backup.backup_id).current_version == 1


def test_add_version_never_extends_an_older_versions_own_expiry(
    store: InMemoryBackupStore,
) -> None:
    """Adding version 2 computes its OWN fresh expiry and never rewrites version 1's -- the per-version retention ceiling stays independent."""
    service = BackupService(store=store, backend=_FakeBackend())
    now = datetime.now(tz=UTC)
    backup = service.create(
        wallet="W1", data=b"v1", declared_size_bytes=2, label="", settlement_tx_id="T1", now=now
    )
    v1_before = store.get_version("W1", backup.backup_id, 1)
    assert v1_before is not None

    later = now + timedelta(days=30)
    service.add_version(
        backup, data=b"v2", declared_size_bytes=2, label="", settlement_tx_id="T2", now=later
    )

    v1_after = store.get_version("W1", backup.backup_id, 1)
    assert v1_after is not None
    assert v1_after.expires_at_epoch == v1_before.expires_at_epoch


def test_add_version_never_changes_the_heads_own_expiry(store: InMemoryBackupStore) -> None:
    """Adding a version must never silently extend (or shorten) the HEAD's own retention window -- that is renew()'s job (finding #5, 2026-09-06: `StoredBackup.current_version`'s own docstring already documented "unaffected" as the intended behavior, but the code silently overwrote expires_at_epoch with the new version's own fresh schedule)."""
    service = BackupService(store=store, backend=_FakeBackend())
    now = datetime.now(tz=UTC)
    backup = service.create(
        wallet="W1", data=b"v1", declared_size_bytes=2, label="", settlement_tx_id="T1", now=now
    )
    expiry_before = backup.expires_at_epoch

    later = now + timedelta(days=30)
    service.add_version(
        backup, data=b"v2", declared_size_bytes=2, label="", settlement_tx_id="T2", now=later
    )

    head_after = store.get("W1", backup.backup_id)
    assert head_after is not None
    assert head_after.expires_at_epoch == expiry_before


def test_add_version_rejects_a_losing_concurrent_writer_without_orphaning_its_bytes(
    store: InMemoryBackupStore,
) -> None:
    """Two concurrent add_version calls reading the same stale current_version must not both silently claim version N+1 -- the loser gets a clear StorageError and its already-written bytes are cleaned up, rather than being left as an untracked, forever-counted disk orphan (finding #5, 2026-09-06).

    `backup` here plays the role of the stale snapshot BOTH callers would
    have read before either wrote -- exactly the race the finding
    describes: "two concurrent add_version calls on the same backup_id
    both read the same N, both write version N+1."
    """
    backend = _FakeBackend()
    service = BackupService(store=store, backend=backend)
    backup = service.create(
        wallet="W1", data=b"v1", declared_size_bytes=2, label="", settlement_tx_id="T1"
    )
    usage_before_bytes = sum(len(blob) for blob in backend._blobs.values())

    winner = service.add_version(
        backup, data=b"winner-bytes", declared_size_bytes=12, label="", settlement_tx_id="T2"
    )
    assert winner.version == 2

    with pytest.raises(StorageError) as excinfo:
        service.add_version(
            backup,  # the stale pre-race snapshot: current_version still reads 1
            data=b"loser-bytes-longer!",
            declared_size_bytes=19,
            label="",
            settlement_tx_id="T3",
        )
    assert excinfo.value.code == "version_conflict"
    assert excinfo.value.http_status == 409

    # The winner's version 2 is completely untouched by the loser's rejection.
    head = service.get("W1", backup.backup_id)
    assert head is not None
    assert head.current_version == 2
    assert service.read_bytes(head) == b"winner-bytes"
    v2 = store.get_version("W1", backup.backup_id, 2)
    assert v2 is not None
    assert v2.content_hash == hashlib.sha256(b"winner-bytes").hexdigest()
    # No THIRD version was ever created for the loser's clobbered attempt.
    assert store.get_version("W1", backup.backup_id, 3) is None

    # The loser's bytes were never left as a disk orphan: the total bytes
    # actually sitting in the connector are exactly the winner's own write,
    # not inflated by the loser's rejected upload too (_FakeBackend's own
    # usage_bytes() is a canned constant, not derived from real blob sizes,
    # so the real underlying blob store is inspected directly here).
    assert sum(len(blob) for blob in backend._blobs.values()) == usage_before_bytes + len(
        b"winner-bytes"
    )


def test_add_version_recovers_after_a_crash_between_the_lwt_and_the_head_repoint(
    store: InMemoryBackupStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transient failure between insert_new_version_if_absent's LWT success and the head repoint must never permanently wedge this backup_id at 409 (second-round adversarial review, 2026-09-06, bug 1).

    Before this fix: version N+1's row would exist forever with nothing
    pointing at it, every future add_version would recompute the identical
    next_version, lose the LWT, and 409 forever. After this fix: the very
    next add_version call detects the orphaned row (head still says N, but
    N+1 already exists), finishes the interrupted head repoint on the
    earlier caller's behalf, and a THIRD call -- reading a fresh head --
    succeeds normally, proving the backup_id is not permanently stuck.
    """
    backend = _FakeBackend()
    service = BackupService(store=store, backend=backend)
    backup = service.create(
        wallet="W1", data=b"v1", declared_size_bytes=2, label="", settlement_tx_id="T1"
    )

    # Simulate a crash landing exactly between insert_new_version_if_absent's
    # LWT success and the head repoint: the first call to store.upsert (the
    # head-repoint step inside _finish_promoting_version) blows up; every
    # call after that behaves normally.
    original_upsert = store.upsert
    calls = {"n": 0}

    def _boom_once_then_upsert(item: StoredBackup) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated crash between the LWT and the head repoint")
        original_upsert(item)

    monkeypatch.setattr(store, "upsert", _boom_once_then_upsert)

    with pytest.raises(RuntimeError):
        service.add_version(
            backup, data=b"v2-crashed", declared_size_bytes=10, label="", settlement_tx_id="T2"
        )

    # The version-2 row is a real, durable orphan: the LWT succeeded before
    # the crash. The head, however, is still wedged at version 1.
    orphaned_v2 = store.get_version("W1", backup.backup_id, 2)
    assert orphaned_v2 is not None
    assert orphaned_v2.content_hash == hashlib.sha256(b"v2-crashed").hexdigest()
    assert service.get("W1", backup.backup_id).current_version == 1

    # A second call using the same stale `backup` snapshot loses the LWT
    # race against the orphaned version-2 row (expected: a 409, not a
    # silent second orphan) -- but it must repair the head as a side effect.
    with pytest.raises(StorageError) as excinfo:
        service.add_version(
            backup, data=b"v3-loses", declared_size_bytes=8, label="", settlement_tx_id="T3"
        )
    assert excinfo.value.code == "version_conflict"

    # The repair completed: the head now correctly points at version 2 --
    # the ORPHANED write's own data, not the second (losing) caller's data.
    repaired_head = service.get("W1", backup.backup_id)
    assert repaired_head is not None
    assert repaired_head.current_version == 2
    assert repaired_head.content_hash == hashlib.sha256(b"v2-crashed").hexdigest()
    assert service.read_bytes(repaired_head) == b"v2-crashed"

    # Version 1 was handed back to its own independent lifecycle by the
    # repair's own `_retire_superseded_version` call, not left dangling in
    # limbo -- still active since its own schedule isn't overdue yet.
    v1_after_repair = store.get_version("W1", backup.backup_id, 1)
    assert v1_after_repair is not None
    assert v1_after_repair.status == STATUS_ACTIVE

    # Proof the backup_id is NOT permanently wedged: a fresh caller reading
    # the now-correct head can add a further version normally.
    fresh_head = service.get("W1", backup.backup_id)
    assert fresh_head is not None
    v3 = service.add_version(
        fresh_head, data=b"v3", declared_size_bytes=2, label="", settlement_tx_id="T4"
    )
    assert v3.version == 3
    assert service.get("W1", backup.backup_id).current_version == 3


def test_add_version_repoints_head_before_deleting_the_superseded_versions_bytes(
    store: InMemoryBackupStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The head must already durably point at the NEW version before the OLD version's bytes are ever touched -- deleting them first would risk a live GET 500ing `backup_unreadable` if anything failed in between (second-round adversarial review, 2026-09-06, bug 2).

    Forces version 1 to already be past its own grace window so
    `_retire_superseded_version` takes the "reap immediately" branch and
    calls `delete_version` synchronously from inside this same
    `add_version` call, then makes that delete fail -- and confirms (a) by
    the time it was even attempted the head already pointed at version 2,
    and (b) that failure never surfaces to the caller or leaves the backup
    unreadable.
    """
    backend = _FakeBackend()
    service = BackupService(store=store, backend=backend)
    now = datetime.now(tz=UTC)
    backup = service.create(
        wallet="W1", data=b"v1", declared_size_bytes=2, label="", settlement_tx_id="T1", now=now
    )
    # x402_storage_reaper_grace_days defaults to 2 and add_version below runs
    # with now=later (now + 1 day), so version 1's own expiry needs to sit
    # more than 2 days behind THAT moment to already be past its grace
    # window at the instant `_retire_superseded_version` runs.
    v1 = store.get_version("W1", backup.backup_id, 1)
    assert v1 is not None
    store.upsert_version(replace(v1, expires_at_epoch=int((now - timedelta(days=10)).timestamp())))

    head_current_version_at_delete_time: dict[str, int] = {}

    def _spy_delete_version(_version_row: StoredBackupVersion) -> bool:
        current = service.get("W1", backup.backup_id)
        assert current is not None
        head_current_version_at_delete_time["value"] = current.current_version
        raise RuntimeError("simulated failure deleting the superseded version")

    monkeypatch.setattr(service, "delete_version", _spy_delete_version)

    later = now + timedelta(days=1)
    v2 = service.add_version(
        backup, data=b"v2", declared_size_bytes=2, label="", settlement_tx_id="T2", now=later
    )

    # By the time the old version's delete was even attempted, the head had
    # already been durably repointed to the new version -- never the other
    # way around.
    assert head_current_version_at_delete_time["value"] == 2

    # The failed cleanup never surfaces to the caller, and the backup is
    # fully readable via the head right now (never `backup_unreadable`).
    assert v2.version == 2
    head = service.get("W1", backup.backup_id)
    assert head is not None
    assert head.current_version == 2
    assert service.read_bytes(head) == b"v2"


def test_get_version_live_synthesizes_version_1_for_a_pre_migration_legacy_backup(
    store: InMemoryBackupStore,
) -> None:
    """A backup written directly to the store (bypassing create(), the way a pre-115 row would look) has no version rows at all -- get/list fall back to synthesizing version 1 from the head."""
    service = BackupService(store=store, backend=_FakeBackend())
    legacy = _stored(store, wallet="W1", backup_id="2" * 8 + "-1111-1111-1111-111111111111")
    assert store.get_version("W1", legacy.backup_id, 1) is None  # confirms no real row exists

    synthesized = service.get_version_live("W1", legacy.backup_id, 1)
    assert synthesized is not None
    assert synthesized.content_hash == legacy.content_hash
    assert synthesized.version == 1

    listed = service.list_versions_live("W1", legacy.backup_id, limit=10)
    assert len(listed) == 1
    assert listed[0].version == 1

    # A version number other than 1 never resolves for a legacy backup.
    assert service.get_version_live("W1", legacy.backup_id, 2) is None


def test_get_version_live_defers_to_the_heads_expiry_for_the_current_version(
    store: InMemoryBackupStore,
) -> None:
    """After a renew that only touches the HEAD, GET-by-version-number for the CURRENT version must not judge liveness off its own now-stale row (finding #4, 2026-09-06).

    Before this fix: GET /backups/{id} (the unversioned head read) kept
    working after a renew, but GET /backups/{id}/versions/{n} for that same
    CURRENT version 404'd, and GET /backups/{id}/versions dropped it from
    the list entirely -- both judged liveness off the version row's own
    frozen expires_at_epoch instead of the head's.
    """
    service = BackupService(store=store, backend=_FakeBackend())
    now = datetime.now(tz=UTC)
    backup = service.create(
        wallet="W1",
        data=b"v1",
        declared_size_bytes=2,
        label="",
        settlement_tx_id="T1",
        now=now - timedelta(days=91),
    )
    v1_before = store.get_version("W1", backup.backup_id, 1)
    assert v1_before is not None
    now_epoch = int(now.timestamp())
    assert v1_before.expires_at_epoch < now_epoch  # the version row's OWN expiry is stale

    renewed = service.renew(backup, settlement_tx_id="TR", now=now)
    assert renewed.is_live(now_epoch=now_epoch)

    live = service.get_version_live("W1", backup.backup_id, 1, now=now)
    assert live is not None  # must NOT 404 just because the version row's own field is stale
    assert live.content_hash == v1_before.content_hash

    listed = service.list_versions_live("W1", backup.backup_id, limit=10, now=now)
    assert [v.version for v in listed] == [1]  # must NOT silently vanish from the list either


def test_get_version_live_still_uses_its_own_expiry_once_no_longer_current(
    store: InMemoryBackupStore,
) -> None:
    """A NON-current (superseded) version keeps judging its own liveness off its own independent expiry, never the head's -- only the CURRENT version defers to the head (finding #4, 2026-09-06)."""
    service = BackupService(store=store, backend=_FakeBackend())
    now = datetime.now(tz=UTC)
    backup = service.create(
        wallet="W1",
        data=b"v1",
        declared_size_bytes=2,
        label="",
        settlement_tx_id="T1",
        now=now - timedelta(days=91),  # v1's own independent schedule already expired
    )
    renewed = service.renew(
        backup, settlement_tx_id="TR", now=now
    )  # head alive far into the future
    service.add_version(
        renewed, data=b"v2", declared_size_bytes=2, label="", settlement_tx_id="T2", now=now
    )

    # v1 is superseded now: the head being very much alive must NOT make v1
    # look live too -- its own independent schedule already expired, so it
    # must resolve as gone even though head.is_live(now) is True.
    head_after = store.get("W1", backup.backup_id)
    assert head_after is not None
    assert head_after.is_live(now_epoch=int(now.timestamp()))
    assert service.get_version_live("W1", backup.backup_id, 1, now=now) is None


def test_delete_cascades_to_every_version_row_and_its_bytes(store: InMemoryBackupStore) -> None:
    """Deleting the whole backup marks every version deleted and reclaims every version's own connector bytes, not just the current one."""
    backend = _FakeBackend()
    service = BackupService(store=store, backend=backend)
    backup = service.create(
        wallet="W1", data=b"first", declared_size_bytes=5, label="", settlement_tx_id="T1"
    )
    v1 = store.get_version("W1", backup.backup_id, 1)
    assert v1 is not None
    service.add_version(
        backup, data=b"second", declared_size_bytes=6, label="", settlement_tx_id="T2"
    )
    head = service.get("W1", backup.backup_id)
    assert head is not None

    service.delete(head)

    assert store.get("W1", backup.backup_id).status == STATUS_DELETED
    v1_after = store.get_version("W1", backup.backup_id, 1)
    v2_after = store.get_version("W1", backup.backup_id, 2)
    assert v1_after is not None
    assert v1_after.status == STATUS_DELETED
    assert v2_after is not None
    assert v2_after.status == STATUS_DELETED
    assert backend.get(v1.connector_params) is None
    assert backend.get(head.connector_params) is None


# --------------------------------------------------------------------------- #
# Versioning -- api/routes.py
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis")
def test_add_version_route_by_another_wallet_settles_but_is_refused_and_never_refunded(
    monkeypatch: pytest.MonkeyPatch, store: InMemoryBackupStore
) -> None:
    """Same no-refund ownership contract as renew: a payment from a non-owner wallet is kept, nothing is stored."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _r: False)
    owner = "O" * 58
    other = "Q" * 58
    monkeypatch.setattr(
        storage_routes, "backup_service", BackupService(store=store, backend=_FakeBackend())
    )
    backup = _stored(store, wallet=owner)
    monkeypatch.setattr(
        storage_routes,
        "require_paid_request",
        lambda *_a, **_kw: _settled_result(other, txid="TXV"),
    )
    monkeypatch.setattr(
        paid_request_module,
        "send_refund",
        lambda **_kw: (_ for _ in ()).throw(
            AssertionError("an ownership rejection must never be refunded")
        ),
    )
    monkeypatch.setattr(
        storage_routes,
        "mark_fulfilled",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("must not mark fulfilled")),
    )
    data = b"malicious-version"
    body = json.dumps({"data": base64.b64encode(data).decode()}).encode()

    response = storage_routes.x402_storage_add_version(
        _request(
            method="POST",
            path_params={"backup_id": backup.backup_id},
            query={"wallet": owner, "declared_size_bytes": str(len(data))},
            body=body,
        )
    )

    assert response.status_code == 403
    assert "backup_owned_by_another_payer" in response.description
    assert store.get(owner, backup.backup_id).current_version == 1


@pytest.mark.usefixtures("fake_redis")
def test_add_version_route_rejects_out_of_range_retention_days_before_the_gate(
    monkeypatch: pytest.MonkeyPatch, store: InMemoryBackupStore
) -> None:
    """add_version validates retention_days too, before the gate and before even the backup lookup."""
    monkeypatch.setattr(settings, "x402_storage_term_days", 90)
    monkeypatch.setattr(
        storage_routes, "backup_service", BackupService(store=store, backend=_FakeBackend())
    )
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _r: False)
    monkeypatch.setattr(storage_routes, "require_paid_request", _must_not_charge)
    response = storage_routes.x402_storage_add_version(
        _request(
            method="POST",
            path_params={"backup_id": "no-such-id"},
            query={"wallet": "A" * 58, "declared_size_bytes": "10", "retention_days": "91"},
            body=json.dumps({"data": "AAAA"}).encode(),
        )
    )
    assert response.status_code == 400
    assert "retention_days" in response.description


@pytest.mark.usefixtures("fake_redis")
def test_add_version_route_unknown_backup_is_a_404_that_never_reaches_the_gate(
    monkeypatch: pytest.MonkeyPatch, store: InMemoryBackupStore
) -> None:
    """Add version route unknown backup is a 404 that never reaches the gate."""
    monkeypatch.setattr(
        storage_routes, "backup_service", BackupService(store=store, backend=_FakeBackend())
    )
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _r: False)
    monkeypatch.setattr(storage_routes, "require_paid_request", _must_not_charge)
    response = storage_routes.x402_storage_add_version(
        _request(
            method="POST",
            path_params={"backup_id": "no-such-id"},
            query={"wallet": "A" * 58, "declared_size_bytes": "10"},
            body=json.dumps({"data": "AAAA"}).encode(),
        )
    )
    assert response.status_code == 404


@pytest.mark.usefixtures("fake_redis")
def test_add_version_route_settles_and_stores_on_success(
    monkeypatch: pytest.MonkeyPatch, store: InMemoryBackupStore
) -> None:
    """A successful add-version call updates the head, writes the new version, and marks fulfilled."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _r: False)
    service = BackupService(store=store, backend=_FakeBackend())
    monkeypatch.setattr(storage_routes, "backup_service", service)
    owner = "P" * 58
    backup = service.create(
        wallet=owner, data=b"v1-data", declared_size_bytes=7, label="", settlement_tx_id="T0"
    )
    monkeypatch.setattr(
        storage_routes,
        "require_paid_request",
        lambda *_a, **_kw: _settled_result(owner, txid="TXV2"),
    )
    fulfilled: list[str] = []
    monkeypatch.setattr(
        storage_routes, "mark_fulfilled", lambda txid, **_kw: fulfilled.append(txid)
    )
    new_data = b"v2-data-here"
    body = json.dumps({"data": base64.b64encode(new_data).decode(), "label": "v2"}).encode()

    response = storage_routes.x402_storage_add_version(
        _request(
            method="POST",
            path_params={"backup_id": backup.backup_id},
            query={"wallet": owner, "declared_size_bytes": str(len(new_data))},
            body=body,
        )
    )

    assert response.status_code == 200
    payload = json.loads(response.description)
    assert payload["version"] == 2
    assert payload["content_hash"] == hashlib.sha256(new_data).hexdigest()
    assert fulfilled == ["TXV2"]
    assert store.get(owner, backup.backup_id).current_version == 2


@pytest.mark.usefixtures("fake_redis")
def test_list_versions_route_is_metadata_only_and_owner_scoped(
    monkeypatch: pytest.MonkeyPatch, store: InMemoryBackupStore
) -> None:
    """The list-versions route never leaks a non-owner's backup and never includes a `data` field."""
    service = BackupService(store=store, backend=_FakeBackend())
    monkeypatch.setattr(storage_routes, "backup_service", service)
    sk, addr = account.generate_account()
    backup = service.create(
        wallet=addr, data=b"v1", declared_size_bytes=2, label="", settlement_tx_id="T1"
    )
    service.add_version(backup, data=b"v2x", declared_size_bytes=3, label="", settlement_tx_id="T2")

    challenge = auth_service.issue_challenge(addr)
    sig = util.sign_bytes(challenge.signing_message.encode(), sk)
    response = storage_routes.x402_storage_list_versions(
        _request(
            query=_authed_query(addr, challenge.nonce, sig),
            path_params={"backup_id": backup.backup_id},
        )
    )
    assert isinstance(response, dict)
    assert [item["version"] for item in response["items"]] == [2, 1]
    assert all("data" not in item for item in response["items"])

    # A different wallet gets the owner-only 404, not another wallet's list.
    _sk_other, other = account.generate_account()
    challenge2 = auth_service.issue_challenge(other)
    sig2 = util.sign_bytes(challenge2.signing_message.encode(), _sk_other)
    denied = storage_routes.x402_storage_list_versions(
        _request(
            query=_authed_query(other, challenge2.nonce, sig2),
            path_params={"backup_id": backup.backup_id},
        )
    )
    assert isinstance(denied, Response)
    assert denied.status_code == 404


@pytest.mark.usefixtures("fake_redis")
def test_get_version_route_restores_a_specific_older_version(
    monkeypatch: pytest.MonkeyPatch, store: InMemoryBackupStore
) -> None:
    """GET .../versions/:version restores that exact version's bytes, not the current one."""
    service = BackupService(store=store, backend=_FakeBackend())
    monkeypatch.setattr(storage_routes, "backup_service", service)
    sk, addr = account.generate_account()
    backup = service.create(
        wallet=addr,
        data=b"original-content",
        declared_size_bytes=16,
        label="",
        settlement_tx_id="T1",
    )
    service.add_version(
        backup, data=b"replaced-content", declared_size_bytes=16, label="", settlement_tx_id="T2"
    )

    challenge = auth_service.issue_challenge(addr)
    sig = util.sign_bytes(challenge.signing_message.encode(), sk)
    response = storage_routes.x402_storage_get_version(
        _request(
            query=_authed_query(addr, challenge.nonce, sig),
            path_params={"backup_id": backup.backup_id, "version": "1"},
        )
    )
    assert isinstance(response, dict)
    assert base64.b64decode(response["data"]) == b"original-content"
    assert response["version"] == 1

    # The unversioned detail route still returns the CURRENT (v2) content.
    challenge2 = auth_service.issue_challenge(addr)
    sig2 = util.sign_bytes(challenge2.signing_message.encode(), sk)
    current = storage_routes.x402_storage_get_backup(
        _request(
            query=_authed_query(addr, challenge2.nonce, sig2),
            path_params={"backup_id": backup.backup_id},
        )
    )
    assert isinstance(current, dict)
    assert base64.b64decode(current["data"]) == b"replaced-content"
    assert current["current_version"] == 2


@pytest.mark.usefixtures("fake_redis")
def test_get_version_route_404s_for_an_unknown_version_number(
    monkeypatch: pytest.MonkeyPatch, store: InMemoryBackupStore
) -> None:
    """A well-formed but nonexistent version number is a plain 404, not a 500."""
    service = BackupService(store=store, backend=_FakeBackend())
    monkeypatch.setattr(storage_routes, "backup_service", service)
    sk, addr = account.generate_account()
    backup = service.create(
        wallet=addr, data=b"only-version", declared_size_bytes=12, label="", settlement_tx_id="T1"
    )

    challenge = auth_service.issue_challenge(addr)
    sig = util.sign_bytes(challenge.signing_message.encode(), sk)
    response = storage_routes.x402_storage_get_version(
        _request(
            query=_authed_query(addr, challenge.nonce, sig),
            path_params={"backup_id": backup.backup_id, "version": "99"},
        )
    )
    assert isinstance(response, Response)
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Versioning -- services/reaper.py
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis")
def test_reaper_never_deletes_a_version_still_pointed_at_by_the_head(
    store: InMemoryBackupStore,
) -> None:
    """The head was renewed independently of its current version's own row -- the reaper must not delete that version's bytes just because its own projection entry looks due."""
    backend = _FakeBackend()
    service = BackupService(store=store, backend=backend)
    now = datetime.now(tz=UTC)
    # Created 91 days ago: version 1's own expiry (created_at + 90d) is 1 day
    # in the past, well past x402_storage_reaper_grace_days (2) would not
    # even apply here since we renew the HEAD (not the version row) to a
    # fresh 90-day term below, simulating a renew that never touched the
    # versions table.
    backup = service.create(
        wallet="W1",
        data=b"still-current",
        declared_size_bytes=13,
        label="",
        settlement_tx_id="T1",
        now=now - timedelta(days=91),
    )
    renewed = service.renew(backup, settlement_tx_id="TR", now=now)
    assert renewed.current_version == 1
    assert renewed.is_live(now_epoch=int(now.timestamp()))
    v1 = store.get_version("W1", backup.backup_id, 1)
    assert v1 is not None
    assert v1.expires_at_epoch < int(now.timestamp())  # the version row's OWN expiry is stale

    result = reap_expired(service, now=now)

    assert result["reaped_versions"] == 0
    still_there = store.get_version("W1", backup.backup_id, 1)
    assert still_there is not None
    assert still_there.status == STATUS_ACTIVE
    assert backend.get(renewed.connector_params) == b"still-current"
    head_after = store.get("W1", backup.backup_id)
    assert head_after is not None
    assert head_after.status == STATUS_ACTIVE


@pytest.mark.usefixtures("fake_redis")
def test_reaper_reaps_a_superseded_old_version_on_its_own_schedule(
    store: InMemoryBackupStore,
) -> None:
    """An old version that is no longer current is reaped once ITS OWN expiry+grace has passed, independent of the head's own (much later) expiry.

    Since finding #3's fix (2026-09-06), a version whose own independent
    schedule is ALREADY past grace by the moment it is superseded is reaped
    immediately by add_version() itself (`_retire_superseded_version`),
    not by a later reaper tick -- a day-partition scan could never reach an
    already-past-lookback day on its own (see reaper.py's own module
    docstring). The subsequent reap_expired call here proves there is
    nothing left for the periodic sweep to do, not that the sweep is what
    performed the reap.
    """
    backend = _FakeBackend()
    service = BackupService(store=store, backend=backend)
    now = datetime.now(tz=UTC)
    old_moment = now - timedelta(days=93)  # version 1 expires 93+90=... i.e. 3 days ago
    backup = service.create(
        wallet="W1",
        data=b"old-version",
        declared_size_bytes=11,
        label="",
        settlement_tx_id="T1",
        now=old_moment,
    )
    v1 = store.get_version("W1", backup.backup_id, 1)
    assert v1 is not None
    # Keep the BACKUP itself legitimately alive via an explicit renew --
    # since finding #5's fix, add_version() no longer implicitly extends
    # the head's own expiry (that used to mask this exact scenario: the old,
    # buggy add_version() silently pushed the head onto a fresh term as a
    # side effect, so the head could never independently be this overdue).
    backup = service.renew(backup, settlement_tx_id="TR", now=now)
    # Supersede it right away with a fresh version created "now" -- the head
    # is alive on its own separate, just-renewed schedule, but version 1's
    # own row keeps its original (already past grace) expiry untouched.
    service.add_version(
        backup,
        data=b"new-version",
        declared_size_bytes=11,
        label="",
        settlement_tx_id="T2",
        now=now,
    )

    # add_version() already reaped it immediately -- no need to wait for a
    # scheduled tick when the old version's own schedule is already overdue.
    reaped = store.get_version("W1", backup.backup_id, 1)
    assert reaped is not None
    assert reaped.status == STATUS_DELETED
    assert backend.get(v1.connector_params) is None

    result = reap_expired(service, now=now)
    assert result["reaped_versions"] == 0  # already gone, nothing left to reap
    assert result["stale_version_projections"] == 0  # ... and no stale key left behind either

    # The head (now on version 2) and version 2's own bytes are untouched.
    head_after = store.get("W1", backup.backup_id)
    assert head_after is not None
    assert head_after.status == STATUS_ACTIVE
    assert head_after.current_version == 2
    v2_after = store.get_version("W1", backup.backup_id, 2)
    assert v2_after is not None
    assert v2_after.status == STATUS_ACTIVE
    assert backend.get(v2_after.connector_params) == b"new-version"


@pytest.mark.usefixtures("fake_redis")
def test_renew_rekeys_the_current_versions_projection_so_it_stays_reachable(
    store: InMemoryBackupStore,
) -> None:
    """Fable's reproduction, phase 1 (2026-09-06, finding #3): a version's own x402_storage_version_by_expiry row was never touched by a renew that only moves the HEAD, so a version staying current across enough renewals had its projection day permanently fall outside x402_storage_reaper_lookback_days -- unreachable by the periodic scan forever after, even though it was still the backup's live, served content.

    Reproduced directly against the real in-memory projection index (not
    just the canonical row, which never needed to move): before the fix,
    the projection would sit at the ORIGINAL day forever; this proves it
    actually re-keys to track the head, and that the reaper's scan finds it
    again -- correctly as "still current, grace" -- once that new day
    becomes due.
    """
    service = BackupService(store=store, backend=_FakeBackend())
    t0 = datetime.now(tz=UTC)
    backup = service.create(
        wallet="W1", data=b"v1", declared_size_bytes=2, label="", settlement_tx_id="T1", now=t0
    )
    original_day = expiry_day_utc(backup.expires_at_epoch)  # t0 + 90 days

    # Renew once, well before the original expiry -- the head jumps out to
    # a fresh term measured from the renewal moment, capped at
    # renew_moment + max_remaining_days.
    renew_moment = t0 + timedelta(days=80)
    renewed = service.renew(backup, settlement_tx_id="TR", now=renew_moment)
    new_day = expiry_day_utc(renewed.expires_at_epoch)
    assert new_day != original_day

    # The version's projection must have MOVED with it, not stayed behind
    # at the original day.
    assert store.list_versions_by_expiry_day(original_day, limit=10) == []
    moved = store.list_versions_by_expiry_day(new_day, limit=10)
    assert [(r.wallet, r.backup_id, r.version) for r in moved] == [("W1", backup.backup_id, 1)]

    # "Today" now passes ORIGINAL expiry + lookback (t0+90+14) -- exactly
    # the point at which the unfixed code lost this row forever (its
    # day-partition scan can only ever look backward from today).
    past_original_lookback = t0 + timedelta(
        days=90 + settings.x402_storage_reaper_lookback_days + 1
    )
    result = reap_expired(service, now=past_original_lookback)
    assert result["reaped_versions"] == 0
    assert result["stale_version_projections"] == 0
    still_there = store.get_version("W1", backup.backup_id, 1)
    assert still_there is not None
    assert still_there.status == STATUS_ACTIVE

    # Now advance to exactly when the RE-KEYED day becomes due: the scan
    # must actually find it there and correctly grant it grace (still
    # current), never treat the re-keyed row as stale.
    when_new_day_is_due = t0 + timedelta(days=(new_day - t0.date()).days)
    result2 = reap_expired(service, now=when_new_day_is_due)
    assert result2["reaped_versions"] == 0
    assert result2["skipped_versions_in_grace"] == 1
    assert result2["stale_version_projections"] == 0
    still_current = store.get_version("W1", backup.backup_id, 1)
    assert still_current is not None
    assert still_current.status == STATUS_ACTIVE


@pytest.mark.usefixtures("fake_redis")
def test_superseding_a_long_current_version_past_its_original_lookback_window_reaps_it_not_orphans_it(
    store: InMemoryBackupStore,
) -> None:
    """Fable's exact reproduction, phase 2 (2026-09-06, finding #3): create v1, renew the head once (so v1 stays current well past its OWN original expiry + the reaper's lookback), then supersede with add_version -- v1 must end up reaped, not a permanently-untracked disk orphan that keeps counting toward usage_bytes() forever (the real cost fable flagged: an orphan eventually causes OTHER payers' unrelated writes to fail with StorageCapacityUnavailable)."""
    backend = _FakeBackend()
    service = BackupService(store=store, backend=backend)
    t0 = datetime.now(tz=UTC)
    backup = service.create(
        wallet="W1",
        data=b"v1-bytes",
        declared_size_bytes=8,
        label="",
        settlement_tx_id="T1",
        now=t0,
    )
    v1 = store.get_version("W1", backup.backup_id, 1)
    assert v1 is not None

    # Renew once, well before v1's original 90-day expiry.
    renew_moment = t0 + timedelta(days=80)
    renewed = service.renew(backup, settlement_tx_id="TR", now=renew_moment)

    # "Today" is now past v1's ORIGINAL expiry (t0+90) plus the reaper's
    # lookback -- exactly the point past which the unfixed code could never
    # again find v1's projection row, permanently, per fable's reproduction.
    today = t0 + timedelta(days=90 + settings.x402_storage_reaper_lookback_days + 5)
    assert (today - t0).days > 90 + settings.x402_storage_reaper_lookback_days

    # Supersede v1 now.
    service.add_version(
        renewed,
        data=b"v2-bytes-here",
        declared_size_bytes=13,
        label="",
        settlement_tx_id="T2",
        now=today,
    )

    # v1 must be reaped -- not left as an untracked, forever-counted orphan.
    reaped = store.get_version("W1", backup.backup_id, 1)
    assert reaped is not None
    assert reaped.status == STATUS_DELETED
    assert backend.get(v1.connector_params) is None

    # usage_bytes() reflects ONLY the still-live v2 content -- v1's bytes
    # are gone, not still silently counted against everyone else's capacity.
    v2 = store.get_version("W1", backup.backup_id, 2)
    assert v2 is not None
    # Only v2's bytes remain in the connector -- v1's are gone, not still
    # silently occupying space (_FakeBackend's own usage_bytes() is a canned
    # constant, not derived from real blob sizes, so the real underlying
    # blob store is inspected directly here).
    assert sum(len(blob) for blob in backend._blobs.values()) == len(b"v2-bytes-here")

    head_after = store.get("W1", backup.backup_id)
    assert head_after is not None
    assert head_after.status == STATUS_ACTIVE
    assert head_after.current_version == 2

    # A later reap tick finds nothing more to do -- no stale key was left
    # behind by the immediate reap either.
    result = reap_expired(service, now=today)
    assert result["reaped_versions"] == 0
    assert result["stale_version_projections"] == 0


# --------------------------------------------------------------------------- #
# Admin inspect/force-delete (require_admin_wallet-gated, no owner check)
# --------------------------------------------------------------------------- #
def test_admin_inspect_denied_without_admin_auth() -> None:
    """No require_admin_wallet stub patched in -- the real function runs and refuses."""
    denied = admin_routes.admin_x402_storage_inspect(
        _request(
            method="GET",
            query={"wallet": "W1", "backup_id": "B1"},
            path="/api/v1/admin/x402-storage/inspect",
        )
    )
    assert isinstance(denied, Response)
    assert denied.status_code in (401, 403, 503)


def test_admin_remove_denied_without_admin_auth() -> None:
    """No require_admin_wallet stub patched in -- the real function runs and refuses."""
    denied = admin_routes.admin_x402_storage_remove(
        _request(
            method="POST",
            query={"wallet": "W1", "backup_id": "B1"},
            path="/api/v1/admin/x402-storage/remove",
        )
    )
    assert isinstance(denied, Response)
    assert denied.status_code in (401, 403, 503)


def test_admin_lookup_requires_both_wallet_and_backup_id(store: InMemoryBackupStore) -> None:
    """Both admin routes 400 when either query param is missing."""
    with (
        patch.object(admin_routes, "require_admin_wallet", return_value=None),
        patch.object(admin_routes, "verified_admin_wallet", return_value="ADMIN"),
        patch.object(admin_routes, "backup_service", BackupService(store=store)),
    ):
        missing_backup_id = admin_routes.admin_x402_storage_inspect(
            _request(method="GET", query={"wallet": "W1"})
        )
        assert isinstance(missing_backup_id, Response)
        assert missing_backup_id.status_code == 400

        missing_wallet = admin_routes.admin_x402_storage_remove(
            _request(method="POST", query={"backup_id": "B1"})
        )
        assert isinstance(missing_wallet, Response)
        assert missing_wallet.status_code == 400


def test_admin_inspect_returns_data_and_verifies_hash_for_any_wallet(
    tmp_path: Path, store: InMemoryBackupStore
) -> None:
    """Admin inspect works for a wallet that never authenticated -- no wallet-signature check."""
    backend = LocalDiskStorageBackend(str(tmp_path))
    params = backend.put(b"payload-bytes")
    content_hash = hashlib.sha256(b"payload-bytes").hexdigest()
    _stored(
        store,
        wallet="SOME-OTHER-WALLET",
        backup_id="admin-inspect-1",
        content_hash=content_hash,
    )
    row = store.get("SOME-OTHER-WALLET", "admin-inspect-1")
    assert row is not None
    store.upsert(
        StoredBackup(
            wallet=row.wallet,
            backup_id=row.backup_id,
            connector="local",
            connector_params=params,
            size_bytes=len(b"payload-bytes"),
            content_hash=content_hash,
            label=row.label,
            created_at_epoch=row.created_at_epoch,
            expires_at_epoch=row.expires_at_epoch,
            status=row.status,
            settlement_tx_id=row.settlement_tx_id,
        )
    )

    with (
        patch.object(admin_routes, "require_admin_wallet", return_value=None),
        patch.object(admin_routes, "verified_admin_wallet", return_value="ADMIN"),
        patch.object(admin_routes, "backup_service", BackupService(store=store, backend=backend)),
    ):
        result = admin_routes.admin_x402_storage_inspect(
            _request(
                method="GET",
                query={"wallet": "SOME-OTHER-WALLET", "backup_id": "admin-inspect-1"},
            )
        )

    assert isinstance(result, dict)
    assert result["wallet"] == "SOME-OTHER-WALLET"
    assert base64.b64decode(result["data"]) == b"payload-bytes"
    assert result["content_hash_verified"] is True


def test_admin_inspect_404s_for_unknown_wallet_backup_id_pair(store: InMemoryBackupStore) -> None:
    """A well-formed but unknown wallet/backup_id pair is a plain 404."""
    with (
        patch.object(admin_routes, "require_admin_wallet", return_value=None),
        patch.object(admin_routes, "verified_admin_wallet", return_value="ADMIN"),
        patch.object(admin_routes, "backup_service", BackupService(store=store)),
    ):
        result = admin_routes.admin_x402_storage_inspect(
            _request(method="GET", query={"wallet": "NOBODY", "backup_id": "NOTHING"})
        )
    assert isinstance(result, Response)
    assert result.status_code == 404


def test_admin_remove_force_deletes_regardless_of_owner(
    tmp_path: Path, store: InMemoryBackupStore
) -> None:
    """No wallet-signature/ownership check -- the admin route deletes on the (wallet, backup_id) pair alone."""
    backend = LocalDiskStorageBackend(str(tmp_path))
    params = backend.put(b"x")
    row = _stored(store, wallet="VICTIM-OR-ABUSER", backup_id="admin-remove-1")
    store.upsert(
        StoredBackup(
            wallet=row.wallet,
            backup_id=row.backup_id,
            connector="local",
            connector_params=params,
            size_bytes=row.size_bytes,
            content_hash=row.content_hash,
            label=row.label,
            created_at_epoch=row.created_at_epoch,
            expires_at_epoch=row.expires_at_epoch,
            status=row.status,
            settlement_tx_id=row.settlement_tx_id,
        )
    )

    with (
        patch.object(admin_routes, "require_admin_wallet", return_value=None),
        patch.object(admin_routes, "verified_admin_wallet", return_value="ADMIN"),
        patch.object(admin_routes, "backup_service", BackupService(store=store, backend=backend)),
    ):
        result = admin_routes.admin_x402_storage_remove(
            _request(
                method="POST",
                query={"wallet": "VICTIM-OR-ABUSER", "backup_id": "admin-remove-1"},
            )
        )

    assert isinstance(result, dict)
    assert result == {"deleted": True, "wallet": "VICTIM-OR-ABUSER", "backup_id": "admin-remove-1"}
    reloaded = store.get("VICTIM-OR-ABUSER", "admin-remove-1")
    assert reloaded is not None
    assert reloaded.status == STATUS_DELETED
    assert backend.get(params) is None


def test_admin_remove_404s_for_already_deleted(store: InMemoryBackupStore) -> None:
    """Same not_found shape as the owner route for an already-deleted backup."""
    _stored(store, wallet="W1", backup_id="already-gone", status=STATUS_DELETED)
    with (
        patch.object(admin_routes, "require_admin_wallet", return_value=None),
        patch.object(admin_routes, "verified_admin_wallet", return_value="ADMIN"),
        patch.object(admin_routes, "backup_service", BackupService(store=store)),
    ):
        result = admin_routes.admin_x402_storage_remove(
            _request(method="POST", query={"wallet": "W1", "backup_id": "already-gone"})
        )
    assert isinstance(result, Response)
    assert result.status_code == 404
