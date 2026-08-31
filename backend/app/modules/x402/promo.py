"""Promo-code payment bypass, shared across every x402 module (CLAUDE.md section 9).

A promo code lets a caller skip payment for a bounded number of uses, scoped
to exactly one resource (one paid route). A successful redemption is NEVER a
payment: it carries no settlement_tx_id, is never written to the settlement
ledger (modules/x402/settlement.py), and require_paid_request never claims a
replay header for it (there is no payment header). See guard.PaymentResult's
`is_promo` field and paid_request.require_paid_request's `promo_code` /
`promo_wallet` kwargs for how a route wires this in — worked example:
x402_catalog's x402_ping.

Storage split (migration 100, backend/schema/migrations/app/100_x402_promo_codes.cql):
  * Cassandra `x402_promo_codes` — the durable, admin-managed record (code,
    resource, starting_count, expiry, active). Read once per attempt to
    resolve the code; never touched by the hot-path decrement.
  * Redis `algorand:x402:promo:remaining:<code>` — the LIVE atomic remaining
    count, seeded from `starting_count` on first use (SET NX, so two
    concurrent first uses cannot double-seed) and DECRemented atomically on
    every attempt. If the decrement result is negative the code is
    exhausted: re-INCREMENT to undo (this is the compare-and-swap-via-
    atomic-decrement pattern — no read-then-write race) and reject. This key
    deliberately carries NO TTL: expiring it would let a partially-redeemed
    code silently reseed to its full starting_count on the next attempt,
    handing out redemptions the durable ledger never authorised.
  * Cassandra `x402_promo_redemptions` — append-only audit log AND the whole
    per-(code, wallet) abuse cap in one: its primary key IS (code,
    wallet_hash), so the `INSERT ... IF NOT EXISTS` that writes the audit row
    is also the atomic once-per-wallet-per-code claim. Only ever written for
    a redemption that actually succeeded — the Redis slot reserved by the
    decrement above is undone (re-incremented) whenever this insert does not
    apply (already redeemed) or itself fails, so a rejected attempt never
    burns a slot from the durable total.

Fail-open vs fail-closed (CLAUDE.md section 2 invariant 9 says cooldown/
lock/budget checks fail open — this is a deliberate, documented exception):
Redis being unreachable during a redemption attempt FAILS CLOSED — the
attempt is refused and falls through to the normal paid gate, which still
works. Unlike a rate limit, an unlimited-redemption failure mode during a
Redis blip is worse than briefly refusing free access; there is no
availability cost since paying normally is still on the table. The
complementary per-IP redemption-attempt rate limit (`x402_promo_rate_limit_
per_hour`), by contrast, DOES fail open — it is a second, best-effort abuse
layer on top of the LWT cap and the atomic decrement, not itself a source of
truth, so a Redis blip there should not additionally block the fallback path.

Every failure mode (unknown code, wrong resource, expired, exhausted,
already redeemed by this wallet, a malformed wallet, a rate-limited IP, or
Redis/Cassandra being unreachable) is silent: `attempt_promo_redemption`
returns None and the caller falls straight through to the normal payment
gate. A promo failure must never itself be a 402/error the caller cannot
route around (owner decision).

Wallet abuse guard: the caller supplies a wallet address (`?promo_wallet=`,
see `promo_request_params`), checked for SYNTACTIC validity only
(`algosdk.encoding.is_valid_address`) — there is no signature proof behind
it. This is a soft deterrent against a script iterating addresses, not
cryptographic proof of ownership; a successful redemption is not proof the
caller controls that wallet. The address is hashed (sha256, hex) before it
touches Redis or Cassandra — the raw address is never stored or logged for
this feature.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from algosdk.encoding import is_valid_address

from app.core.cassandra import get_cassandra_session
from app.core.config import settings
from app.core.errors import PlatformError
from app.core.http import Request
from app.core.query_params import query_param
from app.core.rate_limit import incr_with_expiry
from app.core.redis_client import get_redis
from app.core.request_headers import client_ip
from app.core.statements import X402PromoStmts
from app.core.store_factory import StoreFactory
from app.modules.x402.guard import PaymentResult

logger = logging.getLogger(__name__)

MAX_CODE_LENGTH = 64
MAX_RESOURCE_LENGTH = 128

_REMAINING_PREFIX = "algorand:x402:promo:remaining:"
_ATTEMPT_RATE_LIMIT_PREFIX = "algorand:x402:promo:attempt_rl:"
_ATTEMPT_RATE_LIMIT_WINDOW_SECONDS = 3600


class PromoError(PlatformError):
    """Raised only by admin-facing promo management (create/deactivate).

    Never raised on the redemption path — see the module docstring for why a
    redemption failure is always a silent fall-through instead.
    """


@dataclass
class PromoRecord:
    """One admin-issued promo code, as x402_promo_codes holds it."""

    code: str
    resource: str
    starting_count: int
    created_at_epoch: int
    # 0 means "no expiry".
    expires_at_epoch: int
    active: bool


@dataclass
class RedemptionRecord:
    """One redemption, as the append-only x402_promo_redemptions log holds it."""

    code: str
    wallet_hash: str
    redeemed_at_epoch: int
    resource: str


class PromoStore(Protocol):
    """Storage interface for promo codes and their redemption log."""

    def create_code(self, record: PromoRecord) -> bool:
        """Insert a new code if absent. Returns False if the code already exists."""
        ...

    def get_code(self, code: str) -> PromoRecord | None:
        """The stored record for one code, or None."""
        ...

    def deactivate_code(self, code: str) -> bool:
        """Flip one code's active flag false. Returns False if no such code exists."""
        ...

    def insert_redemption_if_absent(self, item: RedemptionRecord) -> bool:
        """Append one redemption, atomically, iff (code, wallet_hash) has never redeemed before."""
        ...


class CassandraPromoStore:
    """Cassandra-backed promo-code storage (migration 100)."""

    def create_code(self, record: PromoRecord) -> bool:
        """Insert `record` via INSERT ... IF NOT EXISTS. False means a code with that name already exists."""
        result = get_cassandra_session().execute(
            X402PromoStmts.INSERT_PROMO_CODE_IF_ABSENT,
            (
                record.code,
                record.resource,
                record.starting_count,
                _dt(record.created_at_epoch),
                _dt(record.expires_at_epoch) if record.expires_at_epoch else None,
                record.active,
            ),
        )
        return bool(result.was_applied)

    def get_code(self, code: str) -> PromoRecord | None:
        """Point-read one code's record, or None if it was never created."""
        row = get_cassandra_session().execute(X402PromoStmts.GET_PROMO_CODE, (code,)).one()
        if row is None:
            return None
        return PromoRecord(
            code=row.code,
            resource=row.resource or "",
            starting_count=int(row.starting_count or 0),
            created_at_epoch=_epoch(row.created_at),
            expires_at_epoch=_epoch(row.expires_at),
            active=bool(row.active),
        )

    def deactivate_code(self, code: str) -> bool:
        """Flip `active` false via UPDATE ... IF EXISTS, so a code that was never created can never be upserted as a phantom row."""
        result = get_cassandra_session().execute(X402PromoStmts.DEACTIVATE_PROMO_CODE, (code,))
        return bool(result.was_applied)

    def insert_redemption_if_absent(self, item: RedemptionRecord) -> bool:
        """Append one redemption via INSERT ... IF NOT EXISTS -- the whole per-(code, wallet_hash) abuse cap in one atomic statement."""
        result = get_cassandra_session().execute(
            X402PromoStmts.INSERT_PROMO_REDEMPTION_IF_ABSENT,
            (item.code, item.wallet_hash, _dt(item.redeemed_at_epoch), item.resource),
        )
        return bool(result.was_applied)


class InMemoryPromoStore:
    """In-memory promo-code storage for dev and tests."""

    def __init__(self) -> None:
        """Start with no codes and no redemptions."""
        self._codes: dict[str, PromoRecord] = {}
        self._redemptions: set[tuple[str, str]] = set()

    def create_code(self, record: PromoRecord) -> bool:
        """Insert `record`. False means a code with that name already exists."""
        if record.code in self._codes:
            return False
        self._codes[record.code] = record
        return True

    def get_code(self, code: str) -> PromoRecord | None:
        """The stored record for `code`, or None if it was never created."""
        return self._codes.get(code)

    def deactivate_code(self, code: str) -> bool:
        """Flip one code's active flag false. False means no such code exists."""
        record = self._codes.get(code)
        if record is None:
            return False
        record.active = False
        return True

    def insert_redemption_if_absent(self, item: RedemptionRecord) -> bool:
        """Append one redemption iff (code, wallet_hash) has never redeemed before."""
        key = (item.code, item.wallet_hash)
        if key in self._redemptions:
            return False
        self._redemptions.add(key)
        return True


_factory: StoreFactory[PromoStore] = StoreFactory(
    backend_name=lambda: settings.x402_promo_store,
    cassandra=CassandraPromoStore,
    memory=InMemoryPromoStore,
)


def get_promo_store() -> PromoStore:
    """Return the process-wide promo store, built from settings on first use."""
    return _factory.get()


def set_promo_store(store: PromoStore | None) -> None:
    """Override the process-wide promo store (test seam); None restores lazy build."""
    _factory.set(store)


def _dt(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, tz=UTC)


def _epoch(value: datetime | None) -> int:
    return int(value.replace(tzinfo=UTC).timestamp()) if value else 0


def _hash_wallet(wallet: str) -> str:
    """sha256 hex digest of a wallet address — the raw address is never stored."""
    return hashlib.sha256(wallet.strip().encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Admin management
# --------------------------------------------------------------------------- #
def create_promo_code(
    *,
    code: str,
    resource: str,
    starting_count: int,
    expires_at_epoch: int = 0,
    store: PromoStore | None = None,
) -> PromoRecord:
    """Create one promo code scoped to one resource. Raises PromoError on a bad request or a duplicate code.

    `code` is admin-chosen (not generated), so a code can be a memorable
    campaign name; callers of the admin route decide their own naming.
    """
    code = code.strip()
    resource = resource.strip()
    if not code or len(code) > MAX_CODE_LENGTH:
        raise PromoError(
            "invalid_request", f"code is required and at most {MAX_CODE_LENGTH} characters"
        )
    if not resource or len(resource) > MAX_RESOURCE_LENGTH:
        raise PromoError(
            "invalid_request", f"resource is required and at most {MAX_RESOURCE_LENGTH} characters"
        )
    if starting_count <= 0:
        raise PromoError("invalid_request", "starting_count must be a positive integer")
    if expires_at_epoch < 0:
        raise PromoError("invalid_request", "expires_at_epoch must not be negative")

    record = PromoRecord(
        code=code,
        resource=resource,
        starting_count=starting_count,
        created_at_epoch=int(datetime.now(tz=UTC).timestamp()),
        expires_at_epoch=expires_at_epoch,
        active=True,
    )
    applied = (store or get_promo_store()).create_code(record)
    if not applied:
        raise PromoError(
            "promo_code_exists", f"A promo code {code!r} already exists", http_status=409
        )
    return record


def deactivate_promo_code(code: str, *, store: PromoStore | None = None) -> bool:
    """Deactivate one promo code early. Returns False if no such code exists."""
    return (store or get_promo_store()).deactivate_code(code.strip())


# --------------------------------------------------------------------------- #
# Request-side trigger
# --------------------------------------------------------------------------- #
def promo_request_params(request: Request) -> tuple[str, str]:
    """(code, wallet) from `?promo=` and `?promo_wallet=`, trimmed.

    Either or both empty means "no promo attempted" — a route passes these
    straight to require_paid_request's promo_code / promo_wallet kwargs.
    """
    code = query_param(request.query_params.get("promo", ""))
    wallet = query_param(request.query_params.get("promo_wallet", ""))
    return code, wallet


def _attempt_rate_limited(request: Request) -> bool:
    """True when this IP has exceeded the hourly redemption-ATTEMPT budget.

    Fails OPEN — a second, complementary abuse layer on top of the Redis
    atomic decrement and the Cassandra per-wallet LWT cap, not itself a
    source of truth, so its own unavailability must not additionally block
    the fallback to the normal paid gate.
    """
    ip = client_ip(request.headers)
    if not ip:
        return False
    count = incr_with_expiry(
        f"{_ATTEMPT_RATE_LIMIT_PREFIX}{ip}", window_seconds=_ATTEMPT_RATE_LIMIT_WINDOW_SECONDS
    )
    if count is None:
        return False
    return count > settings.x402_promo_rate_limit_per_hour


def _undo_reserved_slot(redis_client: object, key: str, *, code: str) -> None:
    """Re-increment a Redis remaining-count key after a decrement turns out not to be a real redemption."""
    try:
        redis_client.incr(key)  # type: ignore[attr-defined]
    except Exception:
        logger.warning(
            "x402 promo: could not undo a reserved slot for code=%s; the remaining count may "
            "now under-count by one until an operator reconciles it",
            code,
            exc_info=True,
        )


def _lookup_redeemable_record(store: PromoStore, code: str, resource: str) -> PromoRecord | None:
    """The stored record for `code`, or None if it does not exist, is inactive, is scoped to a different resource, or has expired."""
    try:
        record = store.get_code(code)
    except Exception:
        logger.warning(
            "x402 promo: code lookup failed for code=%s; falling through to normal payment",
            code,
            exc_info=True,
        )
        return None
    if record is None or not record.active or record.resource != resource:
        return None
    now_epoch = int(datetime.now(tz=UTC).timestamp())
    if record.expires_at_epoch and record.expires_at_epoch < now_epoch:
        return None
    return record


def _reserve_slot(code: str, starting_count: int) -> tuple[object, str] | None:
    """Atomically reserve one redemption slot for `code` via Redis DECR.

    Returns (redis_client, remaining_key) on success -- the caller keeps both
    to undo the reservation later if the redemption turns out not to go
    through. Returns None, having already undone the reservation itself,
    when Redis is unreachable (fails CLOSED, see the module docstring) or the
    code is exhausted.
    """
    remaining_key = f"{_REMAINING_PREFIX}{code}"
    try:
        redis_client = get_redis()
        # SET NX seeds the remaining count from the durable total on the
        # very first attempt against this code; a losing concurrent seed is
        # a harmless no-op (the key already holds the winner's value).
        redis_client.set(remaining_key, starting_count, nx=True)
        remaining = int(redis_client.decr(remaining_key))
    except Exception:
        logger.warning(
            "x402 promo: Redis unavailable during redemption for code=%s; failing CLOSED, "
            "falling through to normal payment",
            code,
            exc_info=True,
        )
        return None
    if remaining < 0:
        _undo_reserved_slot(redis_client, remaining_key, code=code)
        return None
    return redis_client, remaining_key


def attempt_promo_redemption(
    request: Request,
    *,
    code: str,
    wallet: str,
    resource: str,
    store: PromoStore | None = None,
) -> PaymentResult | None:
    """Try to redeem a promo code for one resource; None means no bypass -- pay normally.

    See the module docstring for the full storage/ordering design and the
    fail-open/fail-closed split. Never raises and never returns a
    PaymentResult with `.error` set — every rejection is a silent None.
    """
    code = code.strip()
    wallet = wallet.strip()
    if not code or not wallet or not resource:
        return None

    if _attempt_rate_limited(request):
        logger.info(
            "x402 promo: redemption attempt rate-limited, code=%s resource=%s", code, resource
        )
        return None

    if not is_valid_address(wallet):
        return None

    active_store = store or get_promo_store()
    record = _lookup_redeemable_record(active_store, code, resource)
    if record is None:
        return None

    reserved = _reserve_slot(code, record.starting_count)
    if reserved is None:
        return None
    redis_client, remaining_key = reserved

    now_epoch = int(datetime.now(tz=UTC).timestamp())
    wallet_hash = _hash_wallet(wallet)
    redemption = RedemptionRecord(
        code=code, wallet_hash=wallet_hash, redeemed_at_epoch=now_epoch, resource=resource
    )
    try:
        claimed = active_store.insert_redemption_if_absent(redemption)
    except Exception:
        logger.warning(
            "x402 promo: redemption-log write failed for code=%s; undoing the reserved slot, "
            "falling through to normal payment",
            code,
            exc_info=True,
        )
        _undo_reserved_slot(redis_client, remaining_key, code=code)
        return None
    if not claimed:
        # This wallet already redeemed this code -- the decremented slot was
        # never actually spent, so give it back to the durable total.
        _undo_reserved_slot(redis_client, remaining_key, code=code)
        return None

    logger.info("x402 promo: code=%s redeemed for resource=%s", code, resource)
    return PaymentResult(error=None, is_promo=True)
