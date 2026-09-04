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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Never

import pytest

pytest.importorskip("x402")

from algosdk import account, util

from app.core import rate_limit as rate_limit_core
from app.core.config import settings
from app.core.http import QueryParams, Request, Response
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
)
from app.modules.x402_storage.services import auth_service
from app.modules.x402_storage.services.backup_service import (
    BackupService,
    compute_price,
    mb_units,
    validate_declared_size,
)
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

    def set(self, key: str, value: str, *_a: object, **_kw: object) -> bool:
        """Set."""
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


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    """Fake redis."""
    client = _FakeRedis()
    monkeypatch.setattr(auth_service, "get_redis", lambda: client)
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
    expires_in_days: int = 180,
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
def test_mb_units_rounds_up_with_a_floor_of_one() -> None:
    """Mb units rounds up with a floor of one."""
    assert mb_units(0) == 1
    assert mb_units(1) == 1
    assert mb_units(1024 * 1024) == 1
    assert mb_units(1024 * 1024 + 1) == 2


def test_compute_price_scales_with_mb_units(monkeypatch: pytest.MonkeyPatch) -> None:
    """Compute price scales with mb units."""
    monkeypatch.setattr(settings, "x402_storage_price_per_mb", "$0.02")
    assert compute_price(1024 * 1024) == "$0.020000"
    assert compute_price(1024 * 1024 + 1) == "$0.040000"


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


def test_delete_removes_connector_bytes_before_marking_the_row_deleted(
    store: InMemoryBackupStore,
) -> None:
    """Delete removes connector bytes before marking the row deleted."""
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


def test_renew_extends_from_the_later_of_now_and_current_expiry(
    store: InMemoryBackupStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Renewing a STILL-LIVE backup extends from its current expiry, not from now -- renewing 10 days into a 180-day term should not just add 10+180 days, it must add a full term ON TOP of what is already left."""
    monkeypatch.setattr(settings, "x402_storage_term_days", 180)
    service = BackupService(store=store, backend=_FakeBackend())
    now = datetime.now(tz=UTC)
    backup = _stored(store, wallet="W1", now=now, expires_in_days=180)

    renewed = service.renew(backup, settlement_tx_id="TX-RENEW", now=now + timedelta(days=10))

    # base = max(now+10d, current expiry now+180d) = now+180d, then +180d more.
    expected = int((now + timedelta(days=360)).timestamp())
    assert abs(renewed.expires_at_epoch - expected) <= 1
    assert renewed.settlement_tx_id == "TX-RENEW"


def test_renew_resurrects_an_already_expired_backup_from_now(store: InMemoryBackupStore) -> None:
    """Renew resurrects an already expired backup from now."""
    service = BackupService(store=store, backend=_FakeBackend())
    now = datetime.now(tz=UTC)
    backup = _stored(store, wallet="W1", now=now - timedelta(days=400), expires_in_days=180)
    assert backup.is_live(now_epoch=int(now.timestamp())) is False

    renewed = service.renew(backup, settlement_tx_id="TX-RENEW", now=now)

    expected = int((now + timedelta(days=180)).timestamp())
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
            body=json.dumps({"wallet": "A" * 58}).encode(),
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
    backup = _stored(store, wallet=owner)
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
            body=json.dumps({"wallet": owner}).encode(),
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
    backup = _stored(store, wallet=owner)
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
            body=json.dumps({"wallet": owner}).encode(),
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
    backup = _stored(store, wallet=owner)
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
            body=json.dumps({"wallet": owner}).encode(),
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
