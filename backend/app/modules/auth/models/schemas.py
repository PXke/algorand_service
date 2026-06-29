"""Re-export shim — definitions live in app/schemas.py (msgspec.Struct)."""

from app.schemas import (  # noqa: F401
    Arc0060Proof,
    Caip122Payload,
    NonceRequest,
    NonceResponse,
    SessionInfo,
    VerifyRequest,
    VerifyResponse,
)
