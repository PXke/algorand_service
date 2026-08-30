"""Grading rules: one grade per (wallet, url), and the credibility-weighted aggregate."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from app.core.config import settings
from app.modules.x402_grading.models.domain import (
    MAX_COMMENT_LENGTH,
    MAX_SCORE,
    MIN_SCORE,
    GradeAggregate,
    GradedEndpoint,
    GradeSummary,
    GradingError,
    StoredGrade,
    WeightedGrade,
)
from app.modules.x402_grading.services.credibility import (
    SpendLookup,
    get_spend_lookup,
    weight_for,
)
from app.modules.x402_grading.services.url_key import normalize_url, url_hash
from app.modules.x402_grading.stores.base import GradeStore
from app.modules.x402_grading.stores.factory import get_grade_store

logger = logging.getLogger(__name__)

# How many tag-listed endpoints a tag leaderboard considers. A module constant
# rather than a setting: it bounds the paid read's cost (one grade-partition
# scan per candidate plus one batched ledger lookup) and 25 is a leaderboard,
# not a catalogue. Raise here, deliberately, if a tag ever needs more.
TOP_CANDIDATE_LIMIT = 25

# The ONE seam through which this module sees the directory: given a raw tag
# and a bound, return the URLs of live listings carrying that tag (at most
# `limit`). Raising is allowed for an unusable tag -- the route maps a
# PlatformError to its status. Defined as a callable, not an import, so this
# service never depends on x402_directory; api/routes.py binds it to the
# directory's public ListingService.search and nothing else does.
TagCandidateLookup = Callable[[str, int], list[str]]


class GradingService:
    """Stores grades of arbitrary endpoint URLs and aggregates them by grader credibility."""

    def __init__(
        self,
        store: GradeStore | None = None,
        *,
        lookup: SpendLookup | None = None,
        tag_lookup: TagCandidateLookup | None = None,
    ) -> None:
        """Take explicit collaborators for tests; otherwise resolve the configured ones lazily.

        `tag_lookup` has no lazy default: without one the tag leaderboard is
        simply unavailable (graded_candidates_for_tag raises), which is the
        honest state for a service constructed with no directory to read.
        """
        self._store = store
        self._lookup = lookup
        self._tag_lookup = tag_lookup

    @property
    def store(self) -> GradeStore:
        """The injected grade store, or the process-wide one built from settings."""
        return self._store or get_grade_store()

    @property
    def lookup(self) -> SpendLookup:
        """The injected spend lookup, or the process-wide one built from settings."""
        return self._lookup or get_spend_lookup()

    # ----------------------------------------------------------------- #
    # URL resolution
    # ----------------------------------------------------------------- #
    def resolve_url(self, url: str) -> tuple[str, str]:
        """Return (normalized url, url hash) for a submitted URL, or raise invalid_request.

        There is no existence check of any kind here. Any http(s) URL can be
        graded: an endpoint does not have to be listed with us, or known to us,
        to have an opinion about it -- the grader names it, and the flat
        payment is the whole cost of entry. This is what decouples grading from
        x402_directory entirely.
        """
        normalized = normalize_url(url)
        return normalized, url_hash(normalized)

    def graded_endpoint(self, url_hash_value: str) -> GradedEndpoint | None:
        """Return the index entry for one URL hash, or None if nobody has graded it."""
        return self.store.get_graded_endpoint(url_hash_value)

    def graded_candidates_for_tag(self, tag: str) -> list[GradedEndpoint]:
        """Tag-listed endpoints that have at least one grade, in listing order, bounded.

        Asks the injected tag lookup for at most TOP_CANDIDATE_LIMIT listed
        URLs, re-normalizes each with THIS module's rule so the grade key
        matches, and keeps only URLs the existence index knows. Every
        existence check is a point read, so this costs a bounded number of
        reads -- it runs BEFORE the leaderboard route's payment gate. The
        lookup's own error for an unusable tag propagates unchanged.
        """
        if self._tag_lookup is None:
            raise GradingError("not_found", "Tag leaderboards are not available on this server")
        candidates: list[GradedEndpoint] = []
        seen: set[str] = set()
        for url in self._tag_lookup(tag, TOP_CANDIDATE_LIMIT)[:TOP_CANDIDATE_LIMIT]:
            try:
                _, hashed = self.resolve_url(url)
            except GradingError:
                # A listed URL this module would refuse to grade cannot have
                # grades under any key, so it simply is not a candidate.
                continue
            if hashed in seen:
                continue
            seen.add(hashed)
            endpoint = self.graded_endpoint(hashed)
            if endpoint is not None:
                candidates.append(endpoint)
        return candidates

    # ----------------------------------------------------------------- #
    # Writes
    # ----------------------------------------------------------------- #
    def submit(
        self,
        *,
        url: str,
        url_hash_value: str,
        grader: str,
        score: int,
        comment: str,
        settlement_tx_id: str,
        now: datetime | None = None,
    ) -> StoredGrade:
        """Store one grader's grade of one URL, replacing their previous one.

        One grade per (grader, url), latest overwrites. This is NOT the feature
        board's vote semantics, and the difference is deliberate: there a
        stacked vote is the demand signal, and paying twice for the same
        request legitimately means twice the demand. Here the read side is a
        quality SCORE, and an average is only meaningful if one wallet
        contributes one data point -- unlimited stacking would let anyone buy
        an endpoint's rating outright for the price of a few grades, which
        corrupts the signal rather than expressing it. Credibility weighting
        does not change that: it decides how much a wallet's ONE data point
        counts, and stacking on top of it would let a wallet buy influence
        twice over.

        The grader keeps their right to change their mind: re-grading replaces
        the row and re-stamps created_at, because the payment they just made
        buys their CURRENT opinion, not an amendment to an old one.

        A grader is never anything the request body claimed -- it comes from
        the settled payment, so nobody can grade in another wallet's name.
        """
        if not MIN_SCORE <= score <= MAX_SCORE:
            # Also enforced by the request schema before the payment gate; kept
            # here so the service cannot be handed an out-of-range score by a
            # future caller and silently store one that skews every average.
            raise GradingError(
                "invalid_request", f"score must be between {MIN_SCORE} and {MAX_SCORE}"
            )
        if not grader.strip():
            # Without an attributable payer there is no (grader, url) key, so
            # the overwrite rule has nothing to key on and one anonymous payer
            # could stack unlimited grades. It would also have no spend history
            # to weight by. Refuse rather than degrade the aggregate; the
            # board's txid-fallback trick is right for a tile that only
            # represents itself and wrong for a shared average.
            raise GradingError(
                "invalid_request", "The settled payment carried no payer address to grade under"
            )
        moment = now or datetime.now(tz=UTC)
        grade = StoredGrade(
            url_hash=url_hash_value,
            url=url,
            grader=grader.strip(),
            score=score,
            comment=comment.strip()[:MAX_COMMENT_LENGTH],
            settlement_tx_id=settlement_tx_id,
            created_at_epoch=int(moment.timestamp()),
        )
        self.store.upsert(grade)
        return grade

    # ----------------------------------------------------------------- #
    # Reads
    # ----------------------------------------------------------------- #
    def aggregate(self, endpoint: GradedEndpoint) -> GradeAggregate:
        """Compute one URL's credibility-weighted aggregate from its stored grades.

        Averaged in Python over a single LIMITed partition read rather than
        maintained as a Cassandra counter or a running-average column. The
        previous build's reasoning for that still holds and this design adds a
        second, stronger reason:

        1. (unchanged) The overwrite rule makes a counter non-idempotent -- a
           re-grade would have to subtract the previous score before adding the
           new one, so one retried write would corrupt the average permanently,
           while re-reading rows and averaging them is idempotent by
           construction. The partition holds one row per grader per URL, which
           is bounded by how many distinct wallets paid to grade one endpoint.
        2. (new) The weights are not a property of the grades at all. A
           grader's weight moves every time that wallet pays for anything,
           anywhere in this marketplace, with no write to this module. A stored
           total would be stale the moment any grader spent again, and would
           have to be recomputed on every settlement in every other product --
           exactly the coupling this module does not have. The weighted number
           is only correct if it is computed at read time.

        Revisit only if one URL's grader count is ever plausibly in the
        thousands.
        """
        return self.aggregate_many([endpoint])[0]

    def aggregate_many(self, endpoints: list[GradedEndpoint]) -> list[GradeAggregate]:
        """Aggregate several URLs with ONE credibility lookup for all their graders.

        The ledger read behind the weights costs a fixed number of
        day-partition scans per call, so a leaderboard over N endpoints must
        not pay it N times: every grader across every endpoint is resolved in
        a single batch and the per-URL aggregates are built from that one
        answer. Output is in input order. The caller bounds N.
        """
        scanned = [self._scan(endpoint) for endpoint in endpoints]
        all_rows = [row for rows, _truncated in scanned for row in rows]
        weights, weights_resolved = self._weights(all_rows)
        return [
            self._build_aggregate(
                endpoint, rows, weights=weights, weights_resolved=weights_resolved, truncated=trunc
            )
            for endpoint, (rows, trunc) in zip(endpoints, scanned, strict=True)
        ]

    def summary(self, endpoint: GradedEndpoint) -> GradeSummary:
        """The free existence-tier summary: grader count and last-graded time, no scores.

        Reads the same bounded partition the paid aggregate reads but never
        touches the ledger and never averages anything -- free is existence,
        paid is signal.
        """
        rows, truncated = self._scan(endpoint)
        return GradeSummary(
            url_hash=endpoint.url_hash,
            url=endpoint.url,
            count=len(rows),
            last_graded_at_epoch=endpoint.last_graded_at_epoch,
            truncated=truncated,
        )

    def _scan(self, endpoint: GradedEndpoint) -> tuple[list[StoredGrade], bool]:
        """One URL's grades over the bounded scan, plus whether the scan hit its bound."""
        scan_limit = max(1, settings.x402_grading_scan_limit)
        # One extra row is asked for purely to detect truncation: a reader who
        # paid for a number must be told when it is over a partial sample.
        rows = self.store.list_for_url(endpoint.url_hash, limit=scan_limit + 1)
        truncated = len(rows) > scan_limit
        if truncated:
            logger.warning(
                "x402 grading: url %s has more than %s grades; aggregate is over a partial "
                "sample. Raise x402_grading_scan_limit or build a rollup.",
                endpoint.url_hash,
                scan_limit,
            )
        return rows[:scan_limit], truncated

    def _build_aggregate(
        self,
        endpoint: GradedEndpoint,
        rows: list[StoredGrade],
        *,
        weights: dict[str, int],
        weights_resolved: bool,
        truncated: bool,
    ) -> GradeAggregate:
        """Fold one URL's scanned rows and already-resolved weights into its aggregate."""
        distribution = dict.fromkeys(range(MIN_SCORE, MAX_SCORE + 1), 0)
        for row in rows:
            if row.score in distribution:
                distribution[row.score] += 1
        count = len(rows)

        graders = {row.grader for row in rows}
        total_weight = sum(weights[grader] for grader in graders)
        # Rounded so the wire value is a stable decimal rather than binary
        # float noise; 3 places is finer than any 1-5 average needs.
        mean = round(sum(row.score for row in rows) / count, 3) if count else 0.0
        weighted_mean = (
            round(sum(row.score * weights[row.grader] for row in rows) / total_weight, 3)
            if total_weight
            else mean
        )
        served = sorted(rows, key=lambda row: (-row.created_at_epoch, row.grader))
        return GradeAggregate(
            url_hash=endpoint.url_hash,
            url=endpoint.url,
            count=count,
            mean=mean,
            weighted_mean=weighted_mean,
            total_weight=total_weight,
            distribution=distribution,
            grades=[
                WeightedGrade(grade=row, weight=weights[row.grader])
                for row in served[: max(1, settings.x402_grading_max_results)]
            ],
            weights_resolved=weights_resolved,
            truncated=truncated,
        )

    def _weights(self, rows: list[StoredGrade]) -> tuple[dict[str, int], bool]:
        """Credibility weight per grader, and whether the ledger actually answered.

        One batched lookup for every grader in the aggregate, never one per
        grader: the ledger read costs a fixed number of day-partition scans, so
        making it per-grader would multiply that by the grader count on a paid
        request path.

        An unreadable ledger is NOT collapsed into "everyone has spent zero".
        It falls back to the base weight for everyone -- which makes the
        weighted mean equal the plain mean -- and reports weights_resolved
        False so the paid response can say the weighting did not run. Silently
        serving an unweighted number as a weighted one is the failure this
        distinction exists to prevent.
        """
        graders = {row.grader for row in rows}
        if not graders:
            return {}, True
        spend = self.lookup.spend_by_payer(sorted(graders))
        if spend is None:
            logger.error(
                "x402 grading: settlement ledger unreadable; serving %s grade(s) at base weight "
                "and reporting weights_resolved=false",
                len(rows),
            )
            return {grader: weight_for(0) for grader in graders}, False
        return {grader: weight_for(spend.get(grader, 0)) for grader in graders}, True

    def list_graded(self, *, limit: int) -> list[GradedEndpoint]:
        """Return URLs that have at least one grade, clamped to the configured maximum."""
        clamped = max(1, min(limit, settings.x402_grading_max_results))
        return self.store.list_graded_endpoints(limit=clamped)
