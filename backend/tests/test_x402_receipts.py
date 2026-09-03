"""Signed fulfillment receipts, their wiring into run_with_refund, and the free read route.

Covers modules/x402/receipts.py, receipt_store.py, their wiring into
paid_request.run_with_refund, and the free GET
/api/v1/x402/receipts/{receipt_id} read route.

Fully offline: no real Algorand/Redis/Cassandra calls. Signing keys are
generated locally via algosdk.account.generate_account -- never touching the
network -- and every store is an InMemoryReceiptStore, same pattern
test_x402_refund.py / test_x402_settlement.py already use for their own
stores.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from types import SimpleNamespace
from typing import Never

import pytest
from algosdk import account, mnemonic
from algosdk.util import verify_bytes

from app.core import serialization
from app.core.config import settings
from app.core.http import QueryParams, Request, Response
from app.modules.x402 import receipt_store as receipt_store_module
from app.modules.x402.guard import PaymentResult
from app.modules.x402.paid_request import run_with_refund
from app.modules.x402.receipt_store import InMemoryReceiptStore, ReceiptRecord
from app.modules.x402.receipts import RECEIPT_HEADER, attach_fulfillment_receipt
from app.modules.x402_catalog.services.catalog import build_catalog
from app.modules.x402_receipts.api.routes import x402_receipt_detail


def _request(*, body: bytes = b"{}", path_params: dict[str, str] | None = None) -> Request:
    return Request(
        method="POST",
        headers={},
        query_params=QueryParams({}),
        path_params=path_params or {},
        body=body,
        url=SimpleNamespace(scheme="http", host="localhost", path="/api/v1/x402/ping"),
    )


def _settled_result(*, txid: str = "TX1") -> PaymentResult:
    return PaymentResult(
        error=None,
        payer="P" * 58,
        settlement_headers={"PAYMENT-RESPONSE": "ok"},
        amount_atomic="1000",
        payment_txid=txid,
        asset_id="10458941",
        network="algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI=",
    )


def _payload_bytes(bundle: dict) -> bytes:
    """Rebuild the exact payload a third party would sign-verify against, from a header bundle."""
    return (
        f"{bundle['request_hash']}|{bundle['response_hash']}|"
        f"{bundle['settlement_tx_id']}|{bundle['issued_at_epoch']}"
    ).encode()


# --------------------------------------------------------------------------- #
# attach_fulfillment_receipt
# --------------------------------------------------------------------------- #
def test_attach_fulfillment_receipt_success_produces_a_valid_verifiable_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the signing key configured: correct hashes, a verifiable signature, the documented header shape, and the content is retrievable via the store."""
    priv, addr = account.generate_account()
    monkeypatch.setattr(settings, "x402_receipt_signing_mnemonic", mnemonic.from_private_key(priv))
    store = InMemoryReceiptStore()
    result = _settled_result(txid="TX1")
    request = _request(body=b'{"q": "tinyman volume"}')
    outcome = {"pong": True, "served_at_epoch": 0}

    attach_fulfillment_receipt(
        result, resource="x402-ping", request=request, outcome=outcome, store=store
    )

    header = result.settlement_headers.get(RECEIPT_HEADER)
    assert header is not None
    bundle = json.loads(header)
    assert bundle["settlement_tx_id"] == "TX1"
    assert bundle["resource"] == "x402-ping"
    assert bundle["signing_address"] == addr

    # The signature verifies against the documented payload construction --
    # a third party rebuilds this exactly the way this assertion does.
    assert verify_bytes(_payload_bytes(bundle), bundle["signature"], addr) is True

    assert bundle["request_hash"] == hashlib.sha256(b'{"q": "tinyman volume"}').hexdigest()
    expected_response_text = serialization.dumps({**outcome, "settlement_tx_id": "TX1"})
    assert (
        bundle["response_hash"]
        == hashlib.sha256(expected_response_text.encode("utf-8")).hexdigest()
    )

    # Stored content matches, independently retrievable -- not just the hash.
    record = store.get_receipt(bundle["receipt_id"])
    assert record is not None
    assert record.output == expected_response_text
    assert record.output_truncated is False
    assert record.response_hash == bundle["response_hash"]
    assert record.request_hash == bundle["request_hash"]
    assert record.signature == bundle["signature"]
    assert record.signing_address == addr


def test_attach_fulfillment_receipt_truncates_oversize_output_but_hashes_the_full_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retained copy is capped at x402_receipt_output_max_chars; the signature/hash still cover the FULL response, never the truncated copy."""
    priv, addr = account.generate_account()
    monkeypatch.setattr(settings, "x402_receipt_signing_mnemonic", mnemonic.from_private_key(priv))
    monkeypatch.setattr(settings, "x402_receipt_output_max_chars", 50)
    store = InMemoryReceiptStore()
    result = _settled_result(txid="TX1")
    request = _request()
    outcome = {"blob": "x" * 500}

    attach_fulfillment_receipt(
        result, resource="x402-ping", request=request, outcome=outcome, store=store
    )

    bundle = json.loads(result.settlement_headers[RECEIPT_HEADER])
    record = store.get_receipt(bundle["receipt_id"])
    assert record is not None
    assert record.output_truncated is True
    assert len(record.output) == 50
    full_text = serialization.dumps({**outcome, "settlement_tx_id": "TX1"})
    assert bundle["response_hash"] == hashlib.sha256(full_text.encode("utf-8")).hexdigest()
    assert record.response_hash == bundle["response_hash"]
    assert verify_bytes(_payload_bytes(bundle), bundle["signature"], addr) is True


def test_attach_fulfillment_receipt_skipped_when_signing_key_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unconfigured signing key: no header, nothing stored, no exception -- receipts are additive only."""
    monkeypatch.setattr(settings, "x402_receipt_signing_mnemonic", "")
    store = InMemoryReceiptStore()
    result = _settled_result()
    request = _request()

    attach_fulfillment_receipt(
        result, resource="x402-ping", request=request, outcome={"pong": True}, store=store
    )

    assert RECEIPT_HEADER not in result.settlement_headers
    assert store.receipts == {}


def test_attach_fulfillment_receipt_skipped_without_request_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No Request object available (request=None): skipped, not an error."""
    priv, _ = account.generate_account()
    monkeypatch.setattr(settings, "x402_receipt_signing_mnemonic", mnemonic.from_private_key(priv))
    store = InMemoryReceiptStore()
    result = _settled_result()

    attach_fulfillment_receipt(
        result, resource="x402-ping", request=None, outcome={"pong": True}, store=store
    )

    assert RECEIPT_HEADER not in result.settlement_headers
    assert store.receipts == {}


def test_attach_fulfillment_receipt_skipped_for_non_dict_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller whose product_write returns something other than a dict (e.g. x402_social's domain objects) is skipped, never mis-hashed."""
    priv, _ = account.generate_account()
    monkeypatch.setattr(settings, "x402_receipt_signing_mnemonic", mnemonic.from_private_key(priv))
    store = InMemoryReceiptStore()
    result = _settled_result()
    request = _request()

    attach_fulfillment_receipt(
        result,
        resource="x402-social-post",
        request=request,
        outcome=SimpleNamespace(tags=["a"]),
        store=store,
    )

    assert RECEIPT_HEADER not in result.settlement_headers
    assert store.receipts == {}


class _BoomReceiptStore:
    """A store whose writes always raise -- simulates a Cassandra/Redis blip."""

    def record_receipt(self, item: ReceiptRecord) -> Never:  # noqa: ARG002 -- Protocol shape
        raise ConnectionError("cassandra unreachable")

    def get_receipt(self, receipt_id: str) -> Never:  # noqa: ARG002 -- Protocol shape
        raise ConnectionError("cassandra unreachable")


def test_attach_fulfillment_receipt_storage_failure_fails_open(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A storage failure never propagates -- caught, logged at WARNING, no header attached, the underlying paid response is unaffected."""
    priv, _ = account.generate_account()
    monkeypatch.setattr(settings, "x402_receipt_signing_mnemonic", mnemonic.from_private_key(priv))
    result = _settled_result()
    request = _request()

    with caplog.at_level("WARNING"):
        attach_fulfillment_receipt(
            result,
            resource="x402-ping",
            request=request,
            outcome={"pong": True},
            store=_BoomReceiptStore(),
        )

    assert RECEIPT_HEADER not in result.settlement_headers
    assert any("failed to generate/store a receipt" in r.message for r in caplog.records)


def test_private_key_invalid_mnemonic_never_logs_the_secret(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Found-in-audit fix mirrored a third time (refund.py, payout_service.py, now receipts.py): algosdk's mnemonic.to_private_key raises ValueError(mnemonic) -- the message IS the entire 25-word secret -- on any invalid word. That secret must never reach a log line."""
    real_secret = "word1 word2 word3 not a real valid mnemonic phrase at all"
    monkeypatch.setattr(settings, "x402_receipt_signing_mnemonic", real_secret)
    store = InMemoryReceiptStore()
    result = _settled_result()
    request = _request()

    with caplog.at_level("DEBUG"):
        attach_fulfillment_receipt(
            result, resource="x402-ping", request=request, outcome={"pong": True}, store=store
        )

    assert RECEIPT_HEADER not in result.settlement_headers
    assert store.receipts == {}
    for record in caplog.records:
        assert real_secret not in record.getMessage()
        assert "word1" not in record.getMessage()


# --------------------------------------------------------------------------- #
# run_with_refund wiring
# --------------------------------------------------------------------------- #
def test_run_with_refund_attaches_a_receipt_on_success_when_wired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared-mechanism hook: passing request= to run_with_refund attaches a receipt with zero other per-route code, via the process-wide receipt store."""
    priv, addr = account.generate_account()
    monkeypatch.setattr(settings, "x402_receipt_signing_mnemonic", mnemonic.from_private_key(priv))
    store = InMemoryReceiptStore()
    receipt_store_module.set_receipt_store(store)
    try:
        result = _settled_result(txid="TX9")
        request = _request(body=b"{}")

        outcome = run_with_refund(
            result,
            resource="x402-ping",
            product_write=lambda: {"pong": True},
            request=request,
        )

        assert outcome == {"pong": True}  # untouched
        header = result.settlement_headers.get(RECEIPT_HEADER)
        assert header is not None
        bundle = json.loads(header)
        assert verify_bytes(_payload_bytes(bundle), bundle["signature"], addr) is True
        assert store.get_receipt(bundle["receipt_id"]) is not None
    finally:
        receipt_store_module.set_receipt_store(None)


def test_run_with_refund_without_request_kwarg_never_attaches_a_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backward compatible: a caller that doesn't pass request= (the default) gets no header, even with a signing key configured -- never an error."""
    priv, _ = account.generate_account()
    monkeypatch.setattr(settings, "x402_receipt_signing_mnemonic", mnemonic.from_private_key(priv))
    receipt_store_module.set_receipt_store(InMemoryReceiptStore())
    try:
        result = _settled_result(txid="TX10")

        outcome = run_with_refund(
            result, resource="x402-ping", product_write=lambda: {"pong": True}
        )

        assert outcome == {"pong": True}
        assert RECEIPT_HEADER not in result.settlement_headers
    finally:
        receipt_store_module.set_receipt_store(None)


def test_run_with_refund_receipt_storage_failure_does_not_fail_the_paid_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Cassandra/Redis blip while storing the receipt must never turn a successful paid response into a failure -- the response still returns successfully, just without a receipt."""
    priv, _ = account.generate_account()
    monkeypatch.setattr(settings, "x402_receipt_signing_mnemonic", mnemonic.from_private_key(priv))
    receipt_store_module.set_receipt_store(_BoomReceiptStore())
    try:
        result = _settled_result(txid="TX11")
        request = _request()

        outcome = run_with_refund(
            result, resource="x402-ping", product_write=lambda: {"pong": True}, request=request
        )

        assert outcome == {"pong": True}
        assert not isinstance(outcome, Response)
        assert RECEIPT_HEADER not in result.settlement_headers
    finally:
        receipt_store_module.set_receipt_store(None)


# --------------------------------------------------------------------------- #
# GET /api/v1/x402/receipts/{receipt_id}
# --------------------------------------------------------------------------- #
def test_x402_receipt_detail_returns_the_stored_receipt() -> None:
    """A real, unexpired receipt id returns its full stored content and verification metadata."""
    store = InMemoryReceiptStore()
    record = ReceiptRecord(
        receipt_id="11111111-1111-1111-1111-111111111111",
        settlement_tx_id="TX1",
        resource="x402-ping",
        signing_address="A" * 58,
        signature="SIGBASE64",
        request_hash="REQHASH",
        response_hash="RESPHASH",
        output='{"pong": true, "settlement_tx_id": "TX1"}',
        output_truncated=False,
        issued_at_epoch=1234567890,
    )
    store.record_receipt(record)
    receipt_store_module.set_receipt_store(store)
    try:
        request = _request(path_params={"receipt_id": record.receipt_id})

        response = x402_receipt_detail(request)

        assert isinstance(response, dict)
        assert response["receipt_id"] == record.receipt_id
        assert response["settlement_tx_id"] == "TX1"
        assert response["signature"] == "SIGBASE64"
        assert response["output"] == record.output
        assert response["output_truncated"] is False
    finally:
        receipt_store_module.set_receipt_store(None)


def test_x402_receipt_detail_404s_for_an_unknown_id() -> None:
    """An id the store never recorded (or that a real Cassandra deployment has TTL'd out past the 90-day retention window -- not fakeable in a unit test, see this module's own note) is a clean 404, never a crash."""
    receipt_store_module.set_receipt_store(InMemoryReceiptStore())
    try:
        request = _request(path_params={"receipt_id": "does-not-exist"})

        response = x402_receipt_detail(request)

        assert isinstance(response, Response)
        assert response.status_code == 404
    finally:
        receipt_store_module.set_receipt_store(None)


def test_x402_receipt_detail_requires_a_receipt_id() -> None:
    """A missing path param is a 400, not a lookup with an empty id."""
    request = _request(path_params={})

    response = x402_receipt_detail(request)

    assert isinstance(response, Response)
    assert response.status_code == 400


# --------------------------------------------------------------------------- #
# Catalog: supports_receipts reflects whether the signing key is configured
# --------------------------------------------------------------------------- #
def _route(catalog: dict, resource: str) -> dict:
    matches = [r for r in catalog["routes"] if r.get("resource") == resource]
    assert len(matches) == 1, f"expected exactly one route for resource={resource!r}"
    return matches[0]


def test_catalog_supports_receipts_false_when_signing_key_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A route wired through run_with_refund still reports supports_receipts=false while no signing key is configured."""
    monkeypatch.setattr(settings, "x402_enabled", True)
    monkeypatch.setattr(settings, "x402_receipt_signing_mnemonic", "")
    monkeypatch.setattr(settings, "x402_directory_store", "cassandra")

    catalog = build_catalog()

    assert _route(catalog, "x402-directory-list")["supports_receipts"] is False


def test_catalog_supports_receipts_true_when_signing_key_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once a signing key is configured, a route wired through run_with_refund flips to supports_receipts=true."""
    priv, _ = account.generate_account()
    monkeypatch.setattr(settings, "x402_enabled", True)
    monkeypatch.setattr(settings, "x402_receipt_signing_mnemonic", mnemonic.from_private_key(priv))
    monkeypatch.setattr(settings, "x402_directory_store", "cassandra")

    catalog = build_catalog()

    assert _route(catalog, "x402-directory-list")["supports_receipts"] is True


def test_catalog_supports_receipts_false_for_a_route_never_wired_through_run_with_refund(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """kyc-verify calls mark_fulfilled directly, never run_with_refund -- it must never claim a receipt, key configured or not."""
    priv, _ = account.generate_account()
    monkeypatch.setattr(settings, "x402_enabled", True)
    monkeypatch.setattr(settings, "x402_receipt_signing_mnemonic", mnemonic.from_private_key(priv))
    monkeypatch.setattr(settings, "kyc_store", "cassandra")

    catalog = build_catalog()

    assert _route(catalog, "kyc-verify")["supports_receipts"] is False


class _TypeCheckingCassandraSession:
    """Stands in for the real cassandra-driver session.

    Raises TypeError on a non-UUID bound against a `uuid` column, exactly like
    cassandra.cqltypes.UUIDType.serialize does for real. Regression guard for a bug where
    CassandraReceiptStore bound receipt_id (a plain str) directly against the
    `receipt_id uuid PRIMARY KEY` column -- silently swallowed by attach_fulfillment_receipt's
    blanket except-Exception, so every Cassandra-backed receipt write (and every read) would fail
    with no route-visible symptom. InMemoryReceiptStore, used by every other test in this file,
    never exercises the driver's own type-checking, so this needs its own fake session rather than
    reusing InMemoryReceiptStore.
    """

    def __init__(self) -> None:
        self.inserted: dict = {}

    def prepare(self, cql: str) -> str:
        return cql

    def execute(self, statement: object, params: tuple) -> SimpleNamespace:
        _ = statement
        if len(params) == 1:
            (receipt_id,) = params
            if not isinstance(receipt_id, uuid.UUID):
                raise TypeError("Got a non-UUID object for a UUID value")
            row = self.inserted.get(receipt_id)
            return SimpleNamespace(one=lambda: row)

        receipt_id = params[0]
        if not isinstance(receipt_id, uuid.UUID):
            raise TypeError("Got a non-UUID object for a UUID value")
        (
            _receipt_id,
            settlement_tx_id,
            resource,
            signing_address,
            signature,
            request_hash,
            response_hash,
            output,
            output_truncated,
            _issued_at,
            issued_at_epoch,
        ) = params
        self.inserted[receipt_id] = SimpleNamespace(
            receipt_id=receipt_id,
            settlement_tx_id=settlement_tx_id,
            resource=resource,
            signing_address=signing_address,
            signature=signature,
            request_hash=request_hash,
            response_hash=response_hash,
            output=output,
            output_truncated=output_truncated,
            issued_at_epoch=issued_at_epoch,
        )
        return SimpleNamespace(one=lambda: None)


def test_cassandra_receipt_store_round_trips_with_uuid_typed_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """record_receipt then get_receipt against a type-checking fake session.

    Would TypeError before the fix, since receipt_id was bound as a str against a uuid column
    both ways.
    """
    from app.modules.x402.receipt_store import CassandraReceiptStore

    fake_session = _TypeCheckingCassandraSession()
    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: fake_session)

    store = CassandraReceiptStore()
    record = ReceiptRecord(
        receipt_id="3fa85f64-5717-4562-b3fc-2c963f66afa6",
        settlement_tx_id="TX1",
        resource="x402-directory-list",
        signing_address="A" * 58,
        signature="sig",
        request_hash="req",
        response_hash="resp",
        output="{}",
        output_truncated=False,
        issued_at_epoch=1700000000,
    )

    store.record_receipt(record)
    fetched = store.get_receipt("3fa85f64-5717-4562-b3fc-2c963f66afa6")

    assert fetched is not None
    assert fetched.receipt_id == "3fa85f64-5717-4562-b3fc-2c963f66afa6"
    assert fetched.settlement_tx_id == "TX1"


def test_cassandra_receipt_store_get_receipt_returns_none_for_malformed_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A garbage receipt_id from the URL path must 404 (None), never a raw TypeError/500."""
    from app.modules.x402.receipt_store import CassandraReceiptStore

    fake_session = _TypeCheckingCassandraSession()
    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: fake_session)

    store = CassandraReceiptStore()

    assert store.get_receipt("not-a-uuid") is None
