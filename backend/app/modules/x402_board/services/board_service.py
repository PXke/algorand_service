"""Board placement rules: link normalization, placement identity, term, storage."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

from app.core.config import settings
from app.modules.x402_board.models.domain import BoardError, StoredPlacement
from app.modules.x402_board.stores.base import PlacementStore
from app.modules.x402_board.stores.factory import get_placement_store

_ALLOWED_SCHEMES = ("http", "https")
_MAX_LINK_LENGTH = 2048
_MAX_NAME_LENGTH = 80
_MAX_PITCH_LENGTH = 280


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
        now: datetime | None = None,
    ) -> StoredPlacement:
        """Store a paid placement for the configured term and return it.

        `normalized_link` must already have come from `normalize_link`. The
        caller normalizes rather than this method because normalization is
        what rejects a non-http(s) or host-less link, and that rejection has
        to happen BEFORE the payment gate -- normalizing here as well would
        either be redundant work or, worse, invite a caller to skip the
        pre-gate check and charge for a link it then refuses.

        Re-placing a link the same payer already has on the board replaces it
        and re-stamps both created_at and term_end: they paid for a fresh term
        starting now, not for an extension of whatever the previous term was.
        Re-stamping created_at also moves the tile back to the front of the
        newest-first feed, which is the visibility they just paid for.
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
        )
        self.store.upsert(placement)
        return placement

    def list_active(self, *, limit: int, now: datetime | None = None) -> list[StoredPlacement]:
        """Return placements whose term is still running, newest-first, clamped.

        Expired placements are dropped here rather than in each store so the
        rule applies identically to Cassandra and memory. The filter runs after
        the LIMITed read, so a page can come back short when the front of the
        board is full of expired tiles -- accepted for now: the board is a
        single bounded partition and every read stays LIMITed. A Cassandra TTL
        on the projection, or a sweep, is the real fix and is not built here.

        A paid term that has ended must stop being advertised: the payer bought
        N days of visibility, not permanent placement.
        """
        moment = now or datetime.now(tz=UTC)
        cutoff = int(moment.timestamp())
        clamped = max(1, min(limit, settings.x402_board_max_results))
        return [
            item for item in self.store.list_recent(limit=clamped) if item.term_end_epoch > cutoff
        ]
