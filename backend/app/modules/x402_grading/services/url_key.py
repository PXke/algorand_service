"""How this module turns a submitted URL into the key it stores grades under.

## This is the third near-copy of the same normalizer, on purpose

x402_directory has `normalize_url` and x402_board has `normalize_link`, both
doing exactly this, and both documenting that they deliberately keep their own
copy rather than importing the other's: a module must not depend on a sibling
product's lifecycle, and importing one would make a bad URL here raise that
sibling's error type. This module now follows the same precedent, and has a
second reason the other two do not: this rework's whole point is that grading
no longer depends on x402_directory in ANY way -- not for listing lookup, and
so also not for the one import the previous build did keep. Importing the
directory's normalizer would leave the coupling that was just removed, in a
place where it is easy to miss.

The right long-run resolution is unchanged from what x402_board already flagged
and is unchanged by this change: ONE shared URL helper in modules/x402/ that
all three call. modules/x402/ is off-limits to this change, so this is flagged
here rather than done here. Whoever does it should collapse all three copies at
once -- three identical bodies is the point at which this stops being
precedent and starts being debt.

Divergence from the directory's copy is safe here in a way it would not have
been for the previous build: grades are keyed under THIS module's hash of THIS
module's normalization, and nothing cross-references the directory's key, so a
future change to either normalizer can no longer silently stop resolving.
"""

from __future__ import annotations

import hashlib
from urllib.parse import urlsplit, urlunsplit

from app.modules.x402_grading.models.domain import MAX_URL_LENGTH, GradingError

_ALLOWED_SCHEMES = {"http", "https"}


def normalize_url(raw: str) -> str:
    """Normalize a graded endpoint URL to the canonical form grades are keyed on.

    Lowercases the scheme and host (both case-insensitive per RFC 3986) and
    drops the fragment, which is never sent to a server and so cannot identify
    a distinct endpoint. The path, query and any explicit port are left exactly
    as given: those ARE case- and content-significant, and rewriting them could
    fold grades of two different endpoints onto one score.

    Raises GradingError("invalid_request"), so a caller never has to catch a
    sibling module's exception class to handle bad input here.
    """
    trimmed = raw.strip()
    if not trimmed or len(trimmed) > MAX_URL_LENGTH:
        raise GradingError("invalid_request", f"url must be 1-{MAX_URL_LENGTH} characters")
    parts = urlsplit(trimmed)
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise GradingError("invalid_request", "url must be http or https")
    if not parts.hostname:
        raise GradingError("invalid_request", "url must include a host")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))


def url_hash(normalized_url: str) -> str:
    """Partition key for one graded endpoint: a hex SHA-256 of the normalized URL.

    Hashed rather than using the URL itself so the partition key is a fixed,
    bounded length however long the graded URL is -- the URL is arbitrary
    caller input here, so an unbounded key would be caller-controlled.
    """
    return hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()
