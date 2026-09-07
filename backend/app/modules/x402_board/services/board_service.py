"""Board placement rules: link normalization, placement identity, term, storage."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

from app.core.config import settings
from app.modules.x402.probe_payers import is_probe_payer
from app.modules.x402_board.models.domain import (
    BOARD_CATEGORIES,
    DEFAULT_BOARD_CATEGORY,
    BoardError,
    StoredPlacement,
)
from app.modules.x402_board.stores.base import PlacementStore
from app.modules.x402_board.stores.factory import get_placement_store

logger = logging.getLogger(__name__)

_ALLOWED_SCHEMES = ("http", "https")
_MAX_LINK_LENGTH = 2048
_MAX_NAME_LENGTH = 80
_MAX_PITCH_LENGTH = 280
# Coarse referrer bound (host only, see click()'s own docstring for why the
# full URL is never kept) -- generous enough for any real hostname, bounded
# so a hostile Referer header cannot bloat a click-event row.
_MAX_REFERRER_LENGTH = 253


def normalize_link(raw: str) -> str:
    """Normalize an advertised link to the canonical form the board keys on.

    Lowercases the scheme and host (both case-insensitive per RFC 3986) and
    drops the fragment, which is never sent to a server. The path, query and
    any explicit port are left exactly as given: those ARE case- and
    content-significant, and rewriting them could point a placement at a
    different page than the payer paid to advertise.

    Deliberately a near-copy of x402_directory's normalize_url rather than an
    import of it: the board must not depend on the directory module's
    lifecycle, and importing it would make a bad board link raise
    DirectoryError. The right resolution is one shared URL helper both modules
    call -- that belongs in modules/x402/, which this change is not authorized
    to extend, so it is flagged rather than done here.
    """
    trimmed = raw.strip()
    if not trimmed or len(trimmed) > _MAX_LINK_LENGTH:
        raise BoardError("invalid_request", "link must be 1-2048 characters")
    parts = urlsplit(trimmed)
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise BoardError("invalid_request", "link must be http or https")
    if not parts.hostname:
        raise BoardError("invalid_request", "link must include a host")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))


def validate_category(raw: str) -> str:
    """Canonical form of a placement's category, raising invalid_request if it is not in the enum.

    Trimmed and lowercased; blank means DEFAULT_BOARD_CATEGORY. Runs BEFORE
    the payment gate (same place normalize_link runs), so an unknown category
    is a free 400, and again on the `?category=` board-read filter so both
    sides name the same partition -- same shape as the directory's own
    validate_category, a near-copy rather than an import for the same
    module-independence reason normalize_link's own docstring gives.
    """
    category = raw.strip().lower() or DEFAULT_BOARD_CATEGORY
    if category not in BOARD_CATEGORIES:
        raise BoardError(
            "invalid_request",
            f"category must be one of: {', '.join(BOARD_CATEGORIES)}",
        )
    return category


def _coarse_referrer(raw: str) -> str:
    """The host-only form of a Referer header value, or "" when there is none or it is unusable.

    Deliberately drops the path and query string: those can carry the
    visitor's own state on the REFERRING site (a search query, a session
    token in the URL, a tracking id) which is not this board's to store --
    see StoredClickEvent's own docstring. A referrer that fails to parse as a
    URL, or carries no host, is recorded as "" rather than raising: a
    malformed or absent Referer header must never turn a real click-through
    into a failed redirect.
    """
    trimmed = raw.strip()
    if not trimmed:
        return ""
    try:
        host = urlsplit(trimmed).hostname or ""
    except ValueError:
        return ""
    return host.lower()[:_MAX_REFERRER_LENGTH]


def placement_id(*, owner: str, normalized_link: str) -> str:
    """Identity of one placement: a hex SHA-256 of the owner and the normalized link.

    Keyed on the PAIR, not the link alone. Keying on the link alone would let
    anyone pay the (deliberately small) placement fee to overwrite someone
    else's live tile -- same link, attacker's pitch text -- which is a griefing
    hole a paid advertising surface cannot have. Keying on the pair means a
    payer can only ever replace their own placement of their own link, and two
    different payers advertising the same link each get their own tile, which
    is correct: they each paid for one.

    Hashed rather than concatenated raw so the key is a fixed, bounded length
    however long the link is. The newline separator is unambiguous because an
    Algorand address cannot contain one.
    """
    return hashlib.sha256(f"{owner}\n{normalized_link}".encode()).hexdigest()


def _owner_key(*, payer: str, settlement_tx_id: str) -> str:
    """The owner half of a placement's identity.

    Normally the paying wallet. When the gate could not attribute a payer, the
    settlement txid stands in so that an unattributable payment still gets its
    own tile: falling back to a constant (or to "") would make every
    unattributable payment for the same link collide onto one placement, so
    the last such payer would silently overwrite the previous one's paid tile.
    """
    attributed = payer.strip()
    return attributed if attributed else f"tx:{settlement_tx_id.strip()}"


class BoardService:
    """Creates and reads visibility-board placements."""

    def __init__(self, store: PlacementStore | None = None) -> None:
        """Take an explicit store for tests; otherwise resolve the configured one lazily."""
        self._store = store

    @property
    def store(self) -> PlacementStore:
        """The injected store, or the process-wide one built from settings."""
        return self._store or get_placement_store()

    def create(
        self,
        *,
        normalized_link: str,
        name: str,
        pitch: str,
        payer: str,
        settlement_tx_id: str,
        category: str = DEFAULT_BOARD_CATEGORY,
        now: datetime | None = None,
    ) -> StoredPlacement:
        """Store a paid placement for the configured term and return it.

        `normalized_link` must already have come from `normalize_link`, and
        `category` from `validate_category` -- both are what reject an
        unplaceable request, and that rejection has to happen BEFORE the
        payment gate, so both run in the route first. Re-validating either
        here would either be redundant work or, worse, invite a caller to
        skip the pre-gate check and charge for a request it then refuses.

        Re-placing a link the same payer already has on the board replaces it
        and re-stamps both created_at and term_end: they paid for a fresh term
        starting now, not for an extension of whatever the previous term was.
        Re-stamping created_at also moves the tile back to the front of the
        newest-first feed, which is the visibility they just paid for. It also
        overwrites the tile's category with whatever this call declares
        (default DEFAULT_BOARD_CATEGORY, same as an initial placement) -- a
        relist is a fresh declaration of everything about the tile, category
        included, not a merge with what was there before.
        """
        if not normalized_link:
            # normalize_link cannot return this, so it means a caller skipped
            # it. Refuse rather than key a tile on the empty string, which
            # would collide every such placement onto one row per owner.
            raise BoardError("invalid_request", "link must be normalized before placement")
        moment = now or datetime.now(tz=UTC)
        owner = _owner_key(payer=payer, settlement_tx_id=settlement_tx_id)
        placement = StoredPlacement(
            entry_id=placement_id(owner=owner, normalized_link=normalized_link),
            link=normalized_link,
            name=name.strip()[:_MAX_NAME_LENGTH],
            pitch=pitch.strip()[:_MAX_PITCH_LENGTH],
            payer=payer.strip(),
            settlement_tx_id=settlement_tx_id,
            term_end_epoch=int(
                (moment + timedelta(days=settings.x402_board_term_days)).timestamp()
            ),
            created_at_epoch=int(moment.timestamp()),
            category=category,
        )
        self.store.upsert(placement)
        return placement

    def get(self, entry_id: str) -> StoredPlacement | None:
        """Return one placement by id, expired or not, or None if there is none.

        A point read, used BEFORE the renew route's payment gate so a renewal
        of an unknown entry is a free 404 rather than a charged one. It gives
        away nothing the feed does not already publish (entry ids are served
        there).
        """
        return self.store.get(entry_id) if entry_id else None

    def renew(
        self,
        *,
        placement: StoredPlacement,
        payer: str,
        settlement_tx_id: str,
        now: datetime | None = None,
    ) -> StoredPlacement:
        """Boost a placement to the top of the board for one more boost window, and return it.

        Repurposed 2026-09-06 (owner decision, the directory's identical
        pricing-model change -- see ListingService.renew()'s own docstring
        for the full reasoning): this used to extend the placement's
        SURVIVAL (term_end_epoch); it now buys PRIORITY PLACEMENT instead,
        stacking a window onto boosted_until_epoch, and never touches
        term_end_epoch. Unlike the directory, the board is not probed (see
        StoredPlacement.term_end_epoch's own comment), so a placement's
        survival is unaffected either way by this change -- it still simply
        expires after settings.x402_board_term_days with no keep-alive.

        Only the wallet that owns the placement may boost it. The payer is
        only known AFTER the payment has settled -- the same accepted
        tradeoff as the directory's relist ownership check
        (listing_service.create): a boost by a different wallet is refused
        with the payment already taken, and the existing tile is left
        untouched. That is the price of not letting anyone pay the small fee
        to take over someone else's paid tile, and it is stated in the 402
        offer before the payer commits. An unattributable payment (empty
        payer) cannot prove ownership either and is refused the same way.

        The new boost window runs from max(now, existing.boosted_until_epoch):
        boosting early adds a full window on top of what is left, boosting
        after a previous boost lapsed starts a fresh one from now, and
        neither shortens what was already paid for. created_at and
        term_end_epoch are untouched -- a boost buys ranking priority, not a
        jump back to the front of the newest-first feed (that is what a
        fresh placement of the same link buys, see create()) and not more
        survival time. The settlement txid is replaced with the boosting
        payment's so the tile traces to the payment that bought its current
        boost.
        """
        attributed = payer.strip()
        if not attributed or attributed != placement.payer:
            raise BoardError(
                "placement_owned_by_another_payer",
                "Only the wallet that placed this entry may boost it. Payment has "
                "settled but the existing placement was not changed.",
                http_status=403,
            )
        moment = now or datetime.now(tz=UTC)
        base = max(int(moment.timestamp()), placement.boosted_until_epoch)
        renewed = replace(
            placement,
            settlement_tx_id=settlement_tx_id,
            boosted_until_epoch=int(
                (
                    datetime.fromtimestamp(base, tz=UTC)
                    + timedelta(days=settings.x402_board_boost_days)
                ).timestamp()
            ),
        )
        self.store.upsert(renewed)
        return renewed

    def click(
        self, entry_id: str, *, referrer: str = "", now: datetime | None = None
    ) -> str | None:
        """Record one click-through on a live placement and return its link.

        Returns None when the entry does not exist or its term has ended: an
        expired tile must stop being advertised, and that includes stopping
        being a redirect target. The counter bump and the click-event log
        write both happen only here, never on the feed read, so the feed
        stays a pure read.

        `referrer` is the RAW Referer header value, if any (the route's own
        job, not this method's, to read it from the request) -- coarsened to
        a bare hostname by `_coarse_referrer` before it is ever stored, see
        StoredClickEvent's own docstring for why.

        A counter or click-event write failure is logged and does NOT fail
        the redirect: the visitor asked for the link, and a bookkeeping blip
        must not turn a working link into a 5xx. Each is its own try/except
        so a failure in one never prevents the other from being recorded.
        """
        placement = self.get(entry_id)
        if placement is None:
            return None
        moment = now or datetime.now(tz=UTC)
        if placement.term_end_epoch <= int(moment.timestamp()):
            return None
        try:
            self.store.increment_clicks(entry_id)
        except Exception:
            logger.warning(
                "x402 board: click on %s redirected but its counter bump failed",
                entry_id,
                exc_info=True,
            )
        try:
            self.store.record_click_event(
                entry_id,
                clicked_at_epoch=int(moment.timestamp()),
                referrer=_coarse_referrer(referrer),
            )
        except Exception:
            logger.warning(
                "x402 board: click on %s redirected but its analytics event failed to record",
                entry_id,
                exc_info=True,
            )
        return placement.link

    def click_counts(self, items: list[StoredPlacement]) -> dict[str, int]:
        """Click totals for a page of placements, keyed by entry id (missing = 0).

        One batched read, bounded by the page the caller already clamped. An
        unreadable counter table degrades to "no counts" with a log line
        rather than taking the free feed down -- the placements themselves
        are the product, the click number is a courtesy.
        """
        if not items:
            return {}
        try:
            return self.store.get_click_counts([item.entry_id for item in items])
        except Exception:
            logger.warning(
                "x402 board: click counts unreadable; feed served without them", exc_info=True
            )
            return {}

    def list_active(
        self, *, limit: int, category: str | None = None, now: datetime | None = None
    ) -> list[StoredPlacement]:
        """Return placements whose term is still running, boosted-first then newest-first, clamped.

        Expired placements are dropped here rather than in each store so the
        rule applies identically to Cassandra and memory. The filter runs after
        the LIMITed read, so a page can come back short when the front of the
        board is full of expired tiles -- accepted for now: the board is a
        single bounded partition and every read stays LIMITed. A Cassandra TTL
        on the projection, or a sweep, is the real fix and is not built here.

        A paid term that has ended must stop being advertised: the payer bought
        N days of visibility, not permanent placement.

        Placements paid for by one of OUR wallets (x402_probe_payers) are
        never served: the probe may pay this endpoint to measure it, but a
        tile it bought is wash visibility and is dropped here, in code, not
        by a label (CLAUDE.md section 9). The row is still stored and still
        resolvable by id, so the probe's own round-trip still succeeds.

        A currently-boosted placement (boosted_until_epoch > now, bought via
        renew(), the board's own mirror of the directory's boost, roadmap
        item 3) sorts before every non-boosted one, newest-first within each
        group -- a stable sort, so this never disturbs relative recency
        order (same _boosted_first shape as ListingService.search(), not
        shared code: see normalize_link's own docstring for why this module
        keeps a near-copy rather than an import).

        `category` (already validated -- `validate_category`) reads
        `store.list_by_category` instead of `store.list_recent`, exactly the
        directory search's own tag/category branch. None (the default) is
        the plain browse case, unfiltered, unchanged from before this filter
        existed.
        """
        moment = now or datetime.now(tz=UTC)
        cutoff = int(moment.timestamp())
        clamped = max(1, min(limit, settings.x402_board_max_results))
        rows = (
            self.store.list_by_category(category, limit=clamped)
            if category is not None
            else self.store.list_recent(limit=clamped)
        )
        live = [
            item for item in rows if item.term_end_epoch > cutoff and not is_probe_payer(item.payer)
        ]
        return sorted(live, key=lambda item: item.boosted_until_epoch <= cutoff)

    def click_history(
        self, entry_id: str, *, days: int, limit: int, now: datetime | None = None
    ) -> dict[str, object] | None:
        """Owner-only click-analytics read: daily click counts and coarse referrer breakdown.

        Returns None when the entry does not exist -- the route turns that
        into its own 404, same shape as `get()`. Unlike `list_active`, this
        does NOT filter out an expired placement: an owner is entitled to see
        the click history of a tile that has since expired, they still paid
        for and received those clicks.

        Reads up to `limit` raw click events (already clamped by the route to
        settings.x402_board_click_history_max_results) within the last `days`
        (already clamped to settings.x402_board_click_history_max_days) and
        aggregates them here rather than in the store, so the aggregation
        rule is identical on Cassandra and memory -- same precedent as
        `list_active`'s own expiry filter.

        `daily` is a zero-filled list covering every day in the window,
        oldest first (chart-friendly), even days with no clicks at all --
        silently omitting a zero day would look like "no data" rather than
        "measured, zero." `top_referrers` is the coarse (hostname-only)
        breakdown, sorted by count descending, capped at 10 entries so a tile
        with many distinct referrers cannot make the response unbounded.
        `capped` is True exactly when the row cap -- not the `days` boundary
        or a lack of history -- is what stopped the read short (same
        "truncated" honesty as HistoryService.read's own `truncated` flag):
        a caller reading `total_clicks_in_window` after `capped` is True
        knows it is a floor, not the real total for a very popular tile.

        `now` defaults to the live clock; tests pass a fixed value so the
        `days`-window boundary and the `daily` bucket dates are deterministic.
        """
        placement = self.get(entry_id)
        if placement is None:
            return None
        moment = now or datetime.now(tz=UTC)
        since_epoch = int((moment - timedelta(days=days)).timestamp())
        events = self.store.list_click_events(entry_id, since_epoch=since_epoch, limit=limit)
        buckets: dict[str, int] = {}
        referrer_counts: dict[str, int] = {}
        for event in events:
            day = datetime.fromtimestamp(event.clicked_at_epoch, tz=UTC).strftime("%Y-%m-%d")
            buckets[day] = buckets.get(day, 0) + 1
            if event.referrer:
                referrer_counts[event.referrer] = referrer_counts.get(event.referrer, 0) + 1
        today = moment.date()
        daily = [
            {
                "date": (today - timedelta(days=offset)).isoformat(),
                "clicks": buckets.get((today - timedelta(days=offset)).isoformat(), 0),
            }
            for offset in range(days - 1, -1, -1)
        ]
        top_referrers = [
            {"referrer": referrer, "clicks": count}
            for referrer, count in sorted(
                referrer_counts.items(), key=lambda pair: pair[1], reverse=True
            )[:10]
        ]
        return {
            "entry_id": entry_id,
            "days": days,
            "total_clicks_in_window": len(events),
            "capped": len(events) >= limit,
            "daily": daily,
            "top_referrers": top_referrers,
        }

    def delete(self, entry_id: str) -> bool:
        """Admin-only: remove a placement outright, recency and category projections included.

        Returns False if there was nothing to delete, so the admin route can
        tell a real removal from a no-op. The click counter and click-event
        log stay (see the store Protocol).
        """
        return bool(entry_id) and self.store.delete(entry_id)
