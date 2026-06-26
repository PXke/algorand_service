from __future__ import annotations

import base64

import pytest

algosdk = pytest.importorskip("algosdk")

from app.modules.auth.utils.arc0060_verify import verify_arc0060_auth  # noqa: E402

# Reference vector from ARC-0060 / CAIP-122 interop tests.
_REF_DATA_B64 = (
    "eyJhY2NvdW50X2FkZHJlc3MiOiJCWVZCRlhDR0pMRFU1UTdQT0ZBMkc0Q0xBR1VCV1JVM1RPS0RQTlFHNTdENDRLVzZDVlkzRlBJWFJNIiwiY2hhaW5faWQiOiIyODMiLCJkb21haW4iOiJhcmM2MC5pbyIsImV4cGlyYXRpb24tdGltZSI6IjIwMjItMTItMzFUMjM6NTk6NTlaIiwiaXNzdWVkLWF0IjoiMjAyMS0xMi0zMVQyMzo1OTo1OVoiLCJub25jZSI6IkE0bkVRWVkzU3M5c0NrVE13SUladWk1VmVVUzVZMUhBUURLMitpdk50WDg9Iiwibm90LWJlZm9yZSI6IjIwMjEtMTItMzFUMjM6NTk6NTlaIiwicmVzb3VyY2VzIjpbImF1dGgiLCJzaWduIl0sInN0YXRlbWVudCI6IldlIGFyZSByZXF1ZXN0aW5nIHlvdSB0byBzaWduIHRoaXMgbWVzc2FnZSB0byBhdXRoZW50aWNhdGUgdG8gYXJjNjAuaW8iLCJ0eXBlIjoiZWQyNTUxOSIsInVyaSI6Imh0dHBzOi8vYXJjNjAuaW8iLCJ2ZXJzaW9uIjoiMSJ9"
)
_REF_AUTH_B64 = base64.b64encode(
    bytes(
        [
            40, 17, 135, 250, 132, 103, 23, 140, 255, 141, 13, 0, 202, 221, 193, 109,
            84, 160, 98, 238, 168, 110, 71, 86, 185, 47, 228, 100, 96, 154, 174, 132,
        ]
    )
).decode()
_REF_SIG_B64 = base64.b64encode(
    bytes(
        [
            13, 3, 239, 198, 110, 225, 101, 241, 182, 39, 25, 5, 213, 152, 121, 2,
            22, 151, 254, 26, 240, 35, 80, 196, 49, 114, 80, 45, 141, 150, 177, 238,
            65, 133, 106, 251, 225, 197, 215, 185, 145, 31, 207, 199, 54, 194, 128, 12,
            55, 100, 113, 170, 2, 93, 203, 250, 180, 33, 245, 166, 123, 131, 224, 9,
        ]
    )
).decode()
_REF_WALLET = "BYVBFXCGJLDU5Q7POFA2G4CLAGUBWRU3TOKDPNQG57D44KW6CVY3FPIXRM"


def test_arc0060_caip122_reference_vector() -> None:
    assert verify_arc0060_auth(
        _REF_WALLET,
        data_b64=_REF_DATA_B64,
        signature_b64=_REF_SIG_B64,
        authenticator_data_b64=_REF_AUTH_B64,
        domain="arc60.io",
    )


def test_arc0060_rejects_wrong_domain() -> None:
    assert not verify_arc0060_auth(
        _REF_WALLET,
        data_b64=_REF_DATA_B64,
        signature_b64=_REF_SIG_B64,
        authenticator_data_b64=_REF_AUTH_B64,
        domain="evil.example",
    )


def test_arc0060_rejects_tampered_signature() -> None:
    bad_sig = base64.b64encode(b"x" * 64).decode()
    assert not verify_arc0060_auth(
        _REF_WALLET,
        data_b64=_REF_DATA_B64,
        signature_b64=bad_sig,
        authenticator_data_b64=_REF_AUTH_B64,
        domain="arc60.io",
    )


def test_arc0060_rejects_malformed_data_b64() -> None:
    assert not verify_arc0060_auth(
        _REF_WALLET,
        data_b64="not-valid-json-payload!!!",
        signature_b64=_REF_SIG_B64,
        authenticator_data_b64=_REF_AUTH_B64,
        domain="arc60.io",
    )


def test_arc0060_rejects_wrong_wallet_address() -> None:
    assert not verify_arc0060_auth(
        "A" * 58,
        data_b64=_REF_DATA_B64,
        signature_b64=_REF_SIG_B64,
        authenticator_data_b64=_REF_AUTH_B64,
        domain="arc60.io",
    )
