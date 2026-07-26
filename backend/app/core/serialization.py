"""msgspec serialization seam for the HTTP layer.

One place that knows how to turn objects into JSON responses and request bodies
into validated msgspec.Structs, so routes never touch msgspec/json directly.
Validation failures are normalised to the project's uniform 400 contract.
"""

from __future__ import annotations

from typing import Any, TypeVar

import msgspec

T = TypeVar("T")

# Reused encoder/decoder factory caching is internal to msgspec; a module-level
# encoder avoids rebuilding the encode config per call.
_encoder = msgspec.json.Encoder()


def encode(obj: Any) -> bytes:  # noqa: ANN401 -- any msgspec.Struct / dict / list / primitive
    """JSON-encode any msgspec.Struct / dict / list / primitive (datetimes -> RFC 3339) to bytes."""
    return _encoder.encode(obj)


def to_builtins(obj: Any) -> Any:  # noqa: ANN401 -- any nested Struct/dict/list structure in or out
    """Convert a Struct (or nested structure) to plain dict/list builtins — for the few spots that merge model data into a larger hand-built payload."""
    return msgspec.to_builtins(obj)


class DecodeError(Exception):
    """Raised when a request body fails to decode/validate. Carries a client-safe message; callers translate it to a 400."""


def decode(raw: str | bytes | None, type_: type[T]) -> T:
    """Decode + validate a request body into `type_`.

    Raises DecodeError (client-safe) on malformed JSON or schema/`__post_init__`
    validation failure, so callers return a uniform 400.
    """
    if raw is None or raw == "" or raw == b"":
        raise DecodeError("request body is required")
    try:
        return msgspec.json.decode(raw, type=type_)
    except (msgspec.ValidationError, msgspec.DecodeError, ValueError, TypeError) as exc:
        raise DecodeError(str(exc)) from exc
