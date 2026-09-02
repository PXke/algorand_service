"""Independent on-chain proof that a grader actually paid the endpoint they are grading.

Owner ask 2026-09-02: grading previously could not answer "did this wallet
pay the endpoint it is grading" at all (see services/credibility.py's own
docstring) because a third-party payment never traverses our gate. This module
answers a narrower, structurally answerable version of that question instead:
given a txid the grader cites as their own payment, was it REALLY a payment
from this same wallet to the graded endpoint's own payTo? That is public,
verifiable, on-chain fact -- it proves engagement, never correctness (what the
endpoint returned is still unknowable to us), and it costs no custody: this is
a read-only chain lookup, no funds move.

Two ways to learn the endpoint's expected payTo, so this works for ANY
gradeable http(s) URL, not just ones listed with us (grading has never
required a listing -- see x402_grade_submit's own docstring):

1. Directory-listed URL: use the listing's newest probe result
   (`payto_seen`, migration 097) already on file -- no extra network call.
2. Not listed: fetch the URL live ourselves, SSRF-guarded exactly like
   x402_scan's own fetch (reuses `media.api.routes._resolve_public_ip`, the
   same accepted shortcut that module already takes rather than a new SSRF
   module -- see its own docstring), and read the payTo out of its 402
   offer. Same header-then-body decode order and the same "POST if GET gets
   a 405" retry `workers/app/modules/x402_probe/probe.py` already uses for
   exactly this purpose (x402 resources often only answer POST) -- this is
   the read-side of that same idea, not a new probing mechanism, just
   reachable from `backend/` instead of `workers/`.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

import httpx
from algosdk.encoding import is_valid_address
from x402.mechanisms.avm.constants import ALGORAND_MAINNET_CAIP2

from app.core.config import settings
from app.modules.media.api.routes import _resolve_public_ip

logger = logging.getLogger(__name__)

# Algorand txids are 52-character base32 (RFC 4648, no padding). The schema
# already bounds length; this bounds character set, so a well-formed-looking
# but garbage string is a 400 before any network call, not an indexer 400.
_TXID_RE = re.compile(r"^[A-Z2-7]{52}$")

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_MAX_REDIRECTS = 3
_MAX_OFFER_BODY_BYTES = 65_536
_UA = "algorand-platform-x402-grading-usage-proof/1 (+https://algorand.pxke.me)"
_PAYMENT_REQUIRED_HEADER = "payment-required"


class UsageProofError(Exception):
    """A usage-proof check could not even be attempted; callers turn this into a 400."""


@dataclass(frozen=True)
class UsageProofResult:
    """Outcome of checking one txid against one graded URL's expected payTo, RECEIVER side only.

    The grading payment gate settles AFTER this pre-gate check runs, so the
    grader's own wallet (the SENDER side of the comparison) is not known yet
    -- see x402_grade_submit, which re-checks `actual_sender` against the
    settled payer once it is. `actual_sender` travels on this result so that
    post-gate check never re-queries the indexer.

    `verified` is None only when the receiver-side check could not be
    completed at all (indexer unreachable, or -- for an unlisted URL -- the
    live fetch itself failed to learn an expected payTo) and the caller must
    decide, per `x402_grading_usage_proof_required`, whether that blocks
    submission or degrades to a stored False. It is never None after a real
    on-chain lookup succeeded: at that point the receiver either matches or
    it does not, a definite True/False.
    """

    verified: bool | None
    detail: str
    actual_sender: str | None = None


def _indexer_url_for_network(network: str) -> str:
    """Same derivation KYA's indexer_client.py uses (not importable -- app/modules/kya/ is off-limits to this change): from x402_network, never a separate setting, so the indexer and the payment network can never drift apart."""
    if network == ALGORAND_MAINNET_CAIP2:
        return settings.kyc_mainnet_indexer_url
    return settings.kyc_testnet_indexer_url


def validate_txid_shape(raw: str) -> str:
    """Return the trimmed, upper-cased txid, or raise UsageProofError for a malformed one.

    Pre-gate shape check only -- whether the txid actually EXISTS and proves
    anything is a separate, later, network-bound question.
    """
    candidate = raw.strip().upper()
    if not _TXID_RE.match(candidate):
        raise UsageProofError("tx_id must be a 52-character Algorand transaction id")
    return candidate


def _fetch_transaction(tx_id: str) -> dict | None:
    """Look up one confirmed transaction by id via the public indexer. None if unreachable or not found."""
    base_url = _indexer_url_for_network(settings.x402_network).rstrip("/")
    try:
        with httpx.Client(timeout=settings.x402_grading_usage_proof_timeout_s) as client:
            resp = client.get(f"{base_url}/v2/transactions/{tx_id}")
            if resp.status_code == 404:
                return {}  # confirmed absence, distinct from "could not ask" below
            resp.raise_for_status()
            body = resp.json()
    except Exception as exc:
        logger.warning("x402 grading usage-proof: indexer lookup failed for %s: %s", tx_id, exc)
        return None
    txn = body.get("transaction") if isinstance(body, dict) else None
    return txn if isinstance(txn, dict) else {}


def _receiver_of(txn: dict) -> str | None:
    """The receiving address of a payment or ASA-transfer transaction, or None for any other type."""
    payment = txn.get("payment-transaction")
    if isinstance(payment, dict):
        receiver = payment.get("receiver")
        return receiver if isinstance(receiver, str) else None
    axfer = txn.get("asset-transfer-transaction")
    if isinstance(axfer, dict):
        receiver = axfer.get("receiver")
        return receiver if isinstance(receiver, str) else None
    return None


def verify_onchain_payment(*, tx_id: str, expected_payto: str) -> UsageProofResult:
    """True when `tx_id` is a real, confirmed transaction to `expected_payto` -- receiver side only.

    The sender side (does this txid actually belong to the wallet doing the
    grading) is checked separately, post-gate, once the grading payment has
    settled and the real payer is known -- see UsageProofResult's own
    docstring for why, and `actual_sender` on the result this returns so
    that check never re-queries the indexer.
    """
    txn = _fetch_transaction(tx_id)
    if txn is None:
        return UsageProofResult(None, "indexer unreachable")
    if not txn:
        return UsageProofResult(False, "no confirmed transaction found for that txid")
    receiver = _receiver_of(txn)
    actual_sender = txn.get("sender") if isinstance(txn.get("sender"), str) else None
    if receiver != expected_payto:
        return UsageProofResult(
            False, "txid receiver does not match the graded endpoint's payTo", actual_sender
        )
    return UsageProofResult(True, "confirmed on-chain, pending sender check", actual_sender)


def _decode_offer(headers: httpx.Headers, body: bytes) -> dict | None:
    """Same header-then-body decode order as workers/x402_probe/probe.py's `_decode_offer`."""
    header = headers.get(_PAYMENT_REQUIRED_HEADER)
    if header:
        try:
            decoded = json.loads(base64.b64decode(header, validate=False))
        except (ValueError, TypeError):
            decoded = None
        if isinstance(decoded, dict):
            return decoded
    if body:
        try:
            decoded = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return None
        if isinstance(decoded, dict) and "accepts" in decoded:
            return decoded
    return None


def _payto_from_offer(offer: dict) -> str | None:
    accepts = offer.get("accepts")
    if not isinstance(accepts, list):
        return None
    for option in accepts:
        if not isinstance(option, dict):
            continue
        payto = option.get("payTo") or option.get("pay_to")
        if isinstance(payto, str) and payto.strip() and is_valid_address(payto.strip()):
            return payto.strip()
    return None


def _pinned_connect_url(current: str) -> tuple[str, dict[str, str], str] | None:
    """(connect_url pinned to a resolved public IP, headers, original host), or None if unsafe.

    Same SSRF pattern x402_scan's `_fetch_bounded_to_disk` uses: resolve the
    host to a public IP first and connect to THAT IP (never a second
    unvalidated DNS lookup at connect time, closing the rebinding window).
    """
    parsed = urlparse(current)
    host = parsed.hostname or ""
    if parsed.scheme not in _ALLOWED_SCHEMES:
        logger.info("x402 grading usage-proof: unsupported scheme %r", parsed.scheme)
        return None
    ip = _resolve_public_ip(host)
    if ip is None:
        logger.info("x402 grading usage-proof: %s does not resolve publicly", host)
        return None
    port_suffix = f":{parsed.port}" if parsed.port else ""
    connect_url = urlunparse(parsed._replace(netloc=f"{ip}{port_suffix}"))
    headers = {"User-Agent": _UA, "Host": parsed.netloc, "Accept": "application/json"}
    return connect_url, headers, host


def _payto_from_402_response(resp: httpx.Response) -> str | None:
    """Read a capped response body and pull the declared payTo out of its 402 offer, or None."""
    body = b""
    for chunk in resp.iter_bytes():
        body += chunk
        if len(body) > _MAX_OFFER_BODY_BYTES:
            break
    offer = _decode_offer(resp.headers, body[:_MAX_OFFER_BODY_BYTES])
    return _payto_from_offer(offer) if offer else None


def fetch_live_payto(url: str) -> str | None:
    """SSRF-guarded fetch of `url`'s own 402 offer to learn its declared payTo. None on any failure.

    Redirect-capped, small body cap (an offer is a few hundred bytes, never
    GB-scale like a scan target). GET first, POST-with-empty-body retry on a
    405 -- many x402 resources only answer POST, same as the workers probe.
    """
    current = url
    method = "GET"
    try:
        with httpx.Client(
            timeout=settings.x402_grading_usage_proof_timeout_s, follow_redirects=False
        ) as client:
            for _ in range(_MAX_REDIRECTS + 1):
                pinned = _pinned_connect_url(current)
                if pinned is None:
                    return None
                connect_url, headers, host = pinned
                with client.stream(
                    method,
                    connect_url,
                    headers=headers,
                    content=b"{}" if method == "POST" else None,
                    extensions={"sni_hostname": host},
                ) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        loc = resp.headers.get("location")
                        if not loc:
                            return None
                        current = httpx.URL(current).join(loc).human_repr()
                        continue
                    if resp.status_code == 405 and method == "GET":
                        method = "POST"
                        continue
                    if resp.status_code != 402:
                        return None
                    return _payto_from_402_response(resp)
    except Exception as exc:
        logger.info("x402 grading usage-proof: live payTo fetch failed for %s: %s", url, exc)
        return None
    return None
